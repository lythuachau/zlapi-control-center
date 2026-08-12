[CmdletBinding()]
param(
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$DataRoot = "C:\ZlapiData",
    [string]$EnvironmentFile = "C:\ZlapiData\secrets\zlapi.env"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$keyFile = Join-Path $DataRoot "secrets\master-key.dpapi"
foreach ($path in @($EnvironmentFile, $keyFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required ZLAPI secret file is missing." }
}
$shared = Join-Path $ServiceRoot "shared"
$releases = Join-Path $ServiceRoot "releases"
foreach ($path in @($ServiceRoot, $shared, $releases)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
$sharedEnvironment = Join-Path $shared ".env"
if (Test-Path -LiteralPath $sharedEnvironment) {
    throw "Shared environment link already exists; refusing to replace it."
}
New-Item -ItemType HardLink -Path $sharedEnvironment -Target $EnvironmentFile | Out-Null

& icacls.exe $ServiceRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' /T /C | Out-Null
& icacls.exe $sharedEnvironment /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
Write-Output "ZLAPI_LAYOUT_PREPARED Environment=HardLink"
