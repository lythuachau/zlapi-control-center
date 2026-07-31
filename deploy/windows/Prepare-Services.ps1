[CmdletBinding()]
param(
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$DataRoot = "C:\ZlapiData",
    [string]$WinSWPath = "C:\Tools\winsw\winsw.exe",
    [string]$PythonRoot = "C:\Program Files\Python312"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run elevated." }

$serviceIds = @("zlapi-web", "zlapi-worker", "zlapi-listener")
foreach ($serviceId in $serviceIds) {
    if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) { throw "$serviceId already exists." }
}
$currentPython = Join-Path $ServiceRoot "current\.venv\Scripts\python.exe"
$environmentFile = Join-Path $ServiceRoot "shared\.env"
$keyFile = Join-Path $DataRoot "secrets\master-key.dpapi"
foreach ($path in @($currentPython, $environmentFile, $keyFile, $WinSWPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file is missing: $path" }
}

$bin = Join-Path $ServiceRoot "bin"
$logs = Join-Path $DataRoot "logs"
$archive = Join-Path $DataRoot "archive"
$backups = Join-Path $DataRoot "backups"
$media = Join-Path $DataRoot "media"
$uploads = Join-Path $DataRoot "uploads"
$run = Join-Path $DataRoot "run"
$secrets = Join-Path $DataRoot "secrets"
foreach ($path in @($bin, $logs, $archive, $backups, $media, $uploads, $run, $secrets)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$templates = @{
    "zlapi-web" = "zlapi-web.xml.template"
    "zlapi-worker" = "zlapi-worker.xml.template"
    "zlapi-listener" = "zlapi-listener.xml.template"
}
foreach ($serviceId in $serviceIds) {
    $wrapper = Join-Path $bin "$serviceId.exe"
    $xml = Join-Path $bin "$serviceId.xml"
    Copy-Item -LiteralPath $WinSWPath -Destination $wrapper -Force
    $template = Join-Path $PSScriptRoot ("services\" + $templates[$serviceId])
    $content = [IO.File]::ReadAllText($template)
    $content = $content.Replace("{{SERVICE_ROOT}}", $ServiceRoot).Replace("{{DATA_ROOT}}", $DataRoot)
    $content = $content.Replace("<startmode>Automatic</startmode>", "<startmode>Manual</startmode>")
    $content = $content.Replace("  <delayedAutoStart>true</delayedAutoStart>" + [Environment]::NewLine, "")
    [IO.File]::WriteAllText($xml, $content, (New-Object Text.UTF8Encoding($false)))
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw "$serviceId installation failed." }
    & sc.exe sidtype $serviceId unrestricted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$serviceId service SID configuration failed." }
    & sc.exe config $serviceId obj= "NT SERVICE\$serviceId" start= demand | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$serviceId virtual account configuration failed." }
}
& sc.exe config zlapi-worker depend= zlapi-web | Out-Null
if ($LASTEXITCODE -ne 0) { throw "zlapi-worker dependency configuration failed." }
& sc.exe config zlapi-listener depend= zlapi-worker | Out-Null
if ($LASTEXITCODE -ne 0) { throw "zlapi-listener dependency configuration failed." }

$serviceGrants = @($serviceIds | ForEach-Object { "NT SERVICE\${_}:(OI)(CI)RX" })
& icacls.exe $ServiceRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' $serviceGrants | Out-Null
& icacls.exe (Join-Path $ServiceRoot '*') /reset /T /C | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' $serviceGrants | Out-Null

$allModify = @($serviceIds | ForEach-Object { "NT SERVICE\${_}:(OI)(CI)M" })
& icacls.exe $logs /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' $allModify | Out-Null
& icacls.exe $uploads /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' 'NT SERVICE\zlapi-web:(OI)(CI)M' 'NT SERVICE\zlapi-worker:(OI)(CI)M' 'NT SERVICE\zlapi-listener:(OI)(CI)RX' | Out-Null
& icacls.exe $media /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' 'NT SERVICE\zlapi-web:(OI)(CI)M' 'NT SERVICE\zlapi-worker:(OI)(CI)M' 'NT SERVICE\zlapi-listener:(OI)(CI)RX' | Out-Null
& icacls.exe $run /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' 'NT SERVICE\zlapi-web:(OI)(CI)RX' 'NT SERVICE\zlapi-worker:(OI)(CI)M' 'NT SERVICE\zlapi-listener:(OI)(CI)M' | Out-Null
& icacls.exe $archive /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' 'NT SERVICE\zlapi-web:(OI)(CI)R' | Out-Null
& icacls.exe $backups /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' | Out-Null
foreach ($path in @($logs, $uploads, $media, $run, $archive, $backups)) {
    & icacls.exe (Join-Path $path '*') /reset /T /C | Out-Null
}
& icacls.exe $secrets /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' $serviceGrants | Out-Null
foreach ($path in @($environmentFile, $keyFile)) {
    $readGrants = @($serviceIds | ForEach-Object { "NT SERVICE\${_}:R" })
    & icacls.exe $path /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' $readGrants | Out-Null
}
foreach ($serviceId in $serviceIds) {
    & icacls.exe $PythonRoot /grant "NT SERVICE\${serviceId}:(OI)(CI)RX" /T /C | Out-Null
}

foreach ($serviceId in $serviceIds) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
    if ($service.State -ne "Stopped" -or $service.StartMode -ne "Manual" -or $service.StartName -ne "NT SERVICE\$serviceId") {
        throw "$serviceId is not prepared as Manual/Stopped under its virtual account."
    }
}
Write-Output "ZLAPI_SERVICES_PREPARED Count=3 State=Stopped StartMode=Manual"
