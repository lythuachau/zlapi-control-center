[CmdletBinding()]
param(
    [string]$ServiceRoot = "C:\Services\Caddy",
    [string]$DataRoot = "C:\ServiceData\Caddy",
    [string]$CaddyPath = "C:\Tools\caddy\caddy.exe",
    [string]$WinSWPath = "C:\Tools\winsw\winsw.exe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$serviceId = "caddy"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run elevated." }
if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) { throw "$serviceId already exists." }
foreach ($path in @($CaddyPath, $WinSWPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required executable is missing: $path" }
}

$bin = Join-Path $ServiceRoot "bin"
$shared = Join-Path $ServiceRoot "shared"
$mutable = @("data", "config", "logs") | ForEach-Object { Join-Path $DataRoot $_ }
foreach ($path in @($bin, $shared, $DataRoot) + $mutable) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
Copy-Item -LiteralPath $CaddyPath -Destination (Join-Path $bin "caddy.exe") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Caddyfile.staging") -Destination (Join-Path $shared "Caddyfile") -Force
& (Join-Path $bin "caddy.exe") validate --config (Join-Path $shared "Caddyfile")
if ($LASTEXITCODE -ne 0) { throw "Staging Caddyfile validation failed." }

$wrapper = Join-Path $bin "$serviceId.exe"
$xml = Join-Path $bin "$serviceId.xml"
Copy-Item -LiteralPath $WinSWPath -Destination $wrapper -Force
$content = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "services\shared-caddy.xml.template"))
$content = $content.Replace("{{SERVICE_ROOT}}", $ServiceRoot).Replace("{{DATA_ROOT}}", $DataRoot)
[IO.File]::WriteAllText($xml, $content, (New-Object Text.UTF8Encoding($false)))

& $wrapper install
if ($LASTEXITCODE -ne 0) { throw "Shared Caddy service installation failed." }
& sc.exe sidtype $serviceId unrestricted | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Caddy service SID configuration failed." }
& sc.exe config $serviceId obj= "NT SERVICE\$serviceId" start= demand | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Caddy virtual account configuration failed." }

& icacls.exe $ServiceRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "NT SERVICE\${serviceId}:(OI)(CI)RX" | Out-Null
& icacls.exe (Join-Path $ServiceRoot '*') /reset /T /C | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "NT SERVICE\${serviceId}:(OI)(CI)RX" | Out-Null
foreach ($path in $mutable) {
    & icacls.exe $path /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "NT SERVICE\${serviceId}:(OI)(CI)M" | Out-Null
    if (Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue | Select-Object -First 1) {
        & icacls.exe (Join-Path $path '*') /reset /T /C | Out-Null
    }
}

$service = Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
if ($service.State -ne "Stopped" -or $service.StartMode -ne "Manual" -or $service.StartName -ne "NT SERVICE\$serviceId") {
    throw "Shared Caddy is not Manual/Stopped under its virtual account."
}
Write-Output "SHARED_CADDY_PREPARED State=Stopped StartMode=Manual Bind=127.0.0.1:2015"
