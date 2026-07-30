[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [switch]$UseExistingCaddy
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$checks = [ordered]@{}
$current = Join-Path $ServiceRoot "current"
$shared = Join-Path $ServiceRoot "shared"

$currentItem = Get-Item -LiteralPath $current -Force
$checks.CurrentIsJunction = [bool]($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)

$serviceIds = @("zlapi-web", "zlapi-worker", "zlapi-listener")
if (-not $UseExistingCaddy) {
    $serviceIds += "zlapi-caddy"
}
foreach ($serviceId in $serviceIds) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
    $checks["$serviceId.Status"] = $service.State
    $checks["$serviceId.StartMode"] = $service.StartMode
    $checks["$serviceId.LeastPrivilegeIdentity"] = $service.StartName -eq "NT SERVICE\$serviceId"
}
$checks["CaddyMode"] = if ($UseExistingCaddy) { "Shared" } else { "DedicatedService" }

$localHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready" -TimeoutSec 10
$checks.LocalHealth = $localHealth.status
$publicHealth = Invoke-RestMethod -Uri "https://$Domain/health/ready" -TimeoutSec 20
$checks.PublicHealth = $publicHealth.status

$task = Get-ScheduledTask -TaskName "ZLAPI Control Maintenance"
$checks.MaintenanceTask = $task.State.ToString()

Push-Location $shared
try {
    & (Join-Path $current ".venv\Scripts\python.exe") -m control_center.cli doctor
}
finally {
    Pop-Location
}

$failed = $checks.GetEnumerator() | Where-Object {
    $_.Value -in @($false, "Stopped", "Disabled", "StartPending", "StopPending")
}
$checks | Format-Table -AutoSize
if ($failed) {
    throw "One or more deployment checks failed."
}

Write-Host "Deployment acceptance checks passed."
