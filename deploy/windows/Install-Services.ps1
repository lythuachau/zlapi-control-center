[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$DataRoot = "C:\ZlapiData",
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$WinSWVersion = "v2.12.0",
    [string]$ExistingCaddyConfig = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Service-ProcessHelpers.ps1")

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

$bin = Join-Path $ServiceRoot "bin"
$shared = Join-Path $ServiceRoot "shared"
$logs = Join-Path $DataRoot "logs"
$caddyData = Join-Path $DataRoot "caddy"
$useExistingCaddy = -not [string]::IsNullOrWhiteSpace($ExistingCaddyConfig)
$serviceModules = @{
    "zlapi-web" = "uvicorn"
    "zlapi-worker" = "control_center.worker"
    "zlapi-listener" = "control_center.listener"
}
$serviceIds = @("zlapi-web", "zlapi-worker", "zlapi-listener")
if (-not $useExistingCaddy) {
    $serviceIds += "zlapi-caddy"
}
foreach ($path in @($ServiceRoot, $bin, $shared, $logs, $caddyData)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $shared ".env"))) {
    throw "Missing $shared\.env. Run Initialize-Config.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $ServiceRoot "current"))) {
    throw "Missing current release. Run Deploy-Release.ps1 first."
}
$pythonBase = (& (Join-Path $ServiceRoot "current\.venv\Scripts\python.exe") -c "import sys; print(sys.base_prefix)").Trim()
Assert-NativeSuccess "Python base path discovery"
if (-not (Test-Path -LiteralPath (Join-Path $pythonBase "python.exe"))) {
    throw "Python base installation was not found at $pythonBase."
}

$winswBase = Join-Path $bin "WinSW-x64.exe"
if (-not (Test-Path -LiteralPath $winswBase)) {
    $uri = "https://github.com/winsw/winsw/releases/download/$WinSWVersion/WinSW-x64.exe"
    Invoke-WebRequest -Uri $uri -OutFile $winswBase -UseBasicParsing
}

$caddyCommand = Get-Command caddy.exe -ErrorAction SilentlyContinue
if (-not $caddyCommand) {
    winget install --id CaddyServer.Caddy --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Caddy installation failed with exit code $LASTEXITCODE."
    }
    $caddyCommand = Get-Command caddy.exe -ErrorAction Stop
}
if (-not $useExistingCaddy) {
    Copy-Item -LiteralPath $caddyCommand.Source -Destination (Join-Path $bin "caddy.exe") -Force
}

$templateRoot = Join-Path $SourceRoot "deploy\windows"
$serviceTemplates = @{
    "zlapi-web" = "zlapi-web.xml.template"
    "zlapi-worker" = "zlapi-worker.xml.template"
    "zlapi-listener" = "zlapi-listener.xml.template"
}
if (-not $useExistingCaddy) {
    $serviceTemplates["zlapi-caddy"] = "zlapi-caddy.xml.template"
}

# Stop wrappers before replacing their executables/configuration, and remove only
# Python module processes launched from this ServiceRoot. Listener and worker are
# stopped first so they cannot enqueue or consume work while web is being replaced.
$stopOrder = @("zlapi-listener", "zlapi-worker", "zlapi-web", "zlapi-caddy")
foreach ($serviceId in $stopOrder) {
    if ($serviceId -notin $serviceIds) {
        continue
    }
    $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
    Stop-ZlapiServiceSafely -Name $serviceId -ServiceRoot $ServiceRoot -Module $module
}

foreach ($serviceId in $serviceTemplates.Keys) {
    $wrapper = Join-Path $bin "$serviceId.exe"
    $xml = Join-Path $bin "$serviceId.xml"
    Copy-Item -LiteralPath $winswBase -Destination $wrapper -Force
    $templatePath = Join-Path $templateRoot ("services\" + $serviceTemplates[$serviceId])
    $content = [IO.File]::ReadAllText($templatePath)
    $content = $content.Replace("{{SERVICE_ROOT}}", $ServiceRoot).Replace("{{DATA_ROOT}}", $DataRoot)
    [IO.File]::WriteAllText($xml, $content, [Text.UTF8Encoding]::new($false))
}

$tlsDirective = if ($Domain -in @("localhost", "127.0.0.1") -or -not $Domain.Contains(".")) { "tls internal" } else { "" }
$caddyTemplate = [IO.File]::ReadAllText((Join-Path $templateRoot "Caddyfile.template"))
$caddyConfig = $caddyTemplate.Replace("{{DOMAIN}}", $Domain).Replace("{{TLS_DIRECTIVE}}", $tlsDirective).Replace("{{DATA_ROOT}}", $DataRoot)
[IO.File]::WriteAllText((Join-Path $shared "Caddyfile"), $caddyConfig, [Text.UTF8Encoding]::new($false))
$caddyExecutable = if ($useExistingCaddy) { $caddyCommand.Source } else { Join-Path $bin "caddy.exe" }
& $caddyExecutable validate --config (Join-Path $shared "Caddyfile")
if ($LASTEXITCODE -ne 0) {
    throw "ZLAPI Caddy configuration validation failed."
}

foreach ($serviceId in $serviceIds) {
    $wrapper = Join-Path $bin "$serviceId.exe"
    if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) {
        & $wrapper uninstall
        Assert-NativeSuccess "$serviceId uninstallation"
        foreach ($attempt in 1..30) {
            if (-not (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) {
            throw "$serviceId remains registered after uninstallation."
        }
    }
    & $wrapper install
    Assert-NativeSuccess "$serviceId installation"
    sc.exe sidtype $serviceId unrestricted | Out-Null
    Assert-NativeSuccess "$serviceId service SID configuration"
    sc.exe config $serviceId obj= "NT SERVICE\$serviceId" | Out-Null
    Assert-NativeSuccess "$serviceId virtual account configuration"
    if ($serviceId -eq "zlapi-caddy") {
        icacls.exe $bin /grant "NT SERVICE\${serviceId}:(OI)(CI)RX" /T /C | Out-Null
        icacls.exe (Join-Path $shared "Caddyfile") /grant "NT SERVICE\${serviceId}:R" /C | Out-Null
        icacls.exe $logs /grant "NT SERVICE\${serviceId}:(OI)(CI)M" /T /C | Out-Null
        icacls.exe $caddyData /grant "NT SERVICE\${serviceId}:(OI)(CI)M" /T /C | Out-Null
    }
    else {
        icacls.exe $ServiceRoot /grant "NT SERVICE\${serviceId}:(OI)(CI)RX" /T /C | Out-Null
        Assert-NativeSuccess "$serviceId application ACL"
        icacls.exe $DataRoot /grant "NT SERVICE\${serviceId}:(OI)(CI)M" /T /C | Out-Null
        Assert-NativeSuccess "$serviceId data ACL"
        icacls.exe $pythonBase /grant "NT SERVICE\${serviceId}:(OI)(CI)RX" /T /C | Out-Null
        Assert-NativeSuccess "$serviceId Python runtime ACL"
    }
}

if ($useExistingCaddy) {
    $resolvedExistingCaddyConfig = (Resolve-Path -LiteralPath $ExistingCaddyConfig).Path
    $existingContent = [IO.File]::ReadAllText($resolvedExistingCaddyConfig)
    $importTarget = (Join-Path $shared "Caddyfile").Replace("\", "/")
    $importLine = "import $importTarget"
    $importPattern = "(?m)^\s*import\s+" + [Text.RegularExpressions.Regex]::Escape($importTarget) + "\s*$"
    $backupPath = "$resolvedExistingCaddyConfig.pre-zlapi-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Copy-Item -LiteralPath $resolvedExistingCaddyConfig -Destination $backupPath
    if (-not [Text.RegularExpressions.Regex]::IsMatch($existingContent, $importPattern)) {
        $updatedContent = $existingContent.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $importLine + [Environment]::NewLine
        [IO.File]::WriteAllText($resolvedExistingCaddyConfig, $updatedContent, [Text.UTF8Encoding]::new($false))
    }
    & $caddyExecutable validate --config $resolvedExistingCaddyConfig
    if ($LASTEXITCODE -ne 0) {
        Copy-Item -LiteralPath $backupPath -Destination $resolvedExistingCaddyConfig -Force
        throw "Combined Caddy configuration validation failed; the original file was restored."
    }
    & $caddyExecutable reload --config $resolvedExistingCaddyConfig
    if ($LASTEXITCODE -ne 0) {
        Copy-Item -LiteralPath $backupPath -Destination $resolvedExistingCaddyConfig -Force
        & $caddyExecutable reload --config $resolvedExistingCaddyConfig
        throw "Caddy reload failed; the original file was restored and reloaded."
    }
}

if (-not (Get-NetFirewallRule -DisplayName "ZLAPI Control HTTPS" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "ZLAPI Control HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow | Out-Null
}
if (-not (Get-NetFirewallRule -DisplayName "ZLAPI Control HTTP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "ZLAPI Control HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow | Out-Null
}

$maintenanceAction = New-ScheduledTaskAction `
    -Execute (Join-Path $ServiceRoot "current\.venv\Scripts\python.exe") `
    -Argument "-m control_center.maintenance all" `
    -WorkingDirectory $shared
$maintenanceTrigger = New-ScheduledTaskTrigger -Daily -At 2:15am
$maintenanceSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask `
    -TaskName "ZLAPI Control Maintenance" `
    -Action $maintenanceAction `
    -Trigger $maintenanceTrigger `
    -Settings $maintenanceSettings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force | Out-Null

$startOrder = @()
if (-not $useExistingCaddy) {
    $startOrder += "zlapi-caddy"
}
$startOrder += @("zlapi-web", "zlapi-worker", "zlapi-listener")
foreach ($serviceId in $startOrder) {
    $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
    if (-not [string]::IsNullOrWhiteSpace($module)) {
        Remove-ZlapiOrphanModuleProcesses -Module $module -ServiceRoot $ServiceRoot
    }
    Start-ZlapiServiceSafely -Name $serviceId
    if (-not [string]::IsNullOrWhiteSpace($module)) {
        Assert-ZlapiSingleServiceModuleProcess `
            -ServiceName $serviceId `
            -Module $module `
            -ServiceRoot $ServiceRoot
    }
}

$healthy = $false
foreach ($attempt in 1..12) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready" -TimeoutSec 5
        if ($response.status -eq "ready") {
            $healthy = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 5
    }
}
if (-not $healthy) {
    throw "Services were installed, but the web health check did not become ready."
}

$envPath = Join-Path $shared ".env"
$environment = [IO.File]::ReadAllText($envPath)
$environment = [Text.RegularExpressions.Regex]::Replace(
    $environment,
    "(?m)^ZLAPI_BOOTSTRAP_ADMIN_PASSWORD=.*$",
    "ZLAPI_BOOTSTRAP_ADMIN_PASSWORD="
)
[IO.File]::WriteAllText($envPath, $environment, [Text.UTF8Encoding]::new($false))

$caddyMode = if ($useExistingCaddy) { "existing Caddy integration" } else { "dedicated Caddy service" }
Write-Host "Windows services, firewall rules, $caddyMode and maintenance task installed."
