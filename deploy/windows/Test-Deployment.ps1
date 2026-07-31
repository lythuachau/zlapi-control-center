[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [switch]$UseExistingCaddy
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Service-ProcessHelpers.ps1")

function Wait-ZlapiReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [int]$Attempts = 12,
        [int]$DelaySeconds = 5
    )

    $lastError = "health endpoint did not return ready"
    foreach ($attempt in 1..$Attempts) {
        try {
            $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 10
            if ($response.status -eq "ready") {
                return $response
            }
            $lastError = "health endpoint returned '$($response.status)'"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Seconds $DelaySeconds
        }
    }
    throw "$Uri did not become ready after $Attempts attempts: $lastError"
}

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

$processFailures = @()
$serviceModules = [ordered]@{
    "zlapi-web" = "uvicorn"
    "zlapi-worker" = "control_center.worker"
    "zlapi-listener" = "control_center.listener"
}
foreach ($serviceId in $serviceModules.Keys) {
    $module = $serviceModules[$serviceId]
    $moduleProcesses = @(Get-ZlapiModuleProcesses -Module $module -ServiceRoot $ServiceRoot)
    $rootProcesses = @($moduleProcesses | Where-Object {
        Test-ZlapiRootModuleProcess -Process $_ -ServiceRoot $ServiceRoot
    })
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
    $wrapperPid = [int]$service.ProcessId
    $validChain = $moduleProcesses.Count -gt 0 -and $wrapperPid -gt 0
    if ($validChain) {
        foreach ($process in $moduleProcesses) {
            if (-not (Test-ZlapiProcessDescendsFrom `
                -ProcessId ([int]$process.ProcessId) `
                -AncestorProcessId $wrapperPid)) {
                $validChain = $false
                break
            }
        }
    }
    $checks["$serviceId.RootProcessCount"] = $rootProcesses.Count
    $checks["$serviceId.ScopedProcessCount"] = $moduleProcesses.Count
    $checks["$serviceId.VerifiedServiceChain"] = $validChain
    if ($rootProcesses.Count -ne 1 -or -not $validChain) {
        $processIds = if ($moduleProcesses.Count -gt 0) {
            ($moduleProcesses | ForEach-Object { [string]$_.ProcessId }) -join ", "
        }
        else {
            "none"
        }
        $processFailures += "$serviceId expected one $module root and one valid WinSW chain for PID $wrapperPid; found $($rootProcesses.Count) root(s), $($moduleProcesses.Count) scoped process(es): $processIds"
    }
}

$localHealth = Wait-ZlapiReady -Uri "http://127.0.0.1:8000/health/ready"
$checks.LocalHealth = $localHealth.status
$publicHealth = Wait-ZlapiReady -Uri "https://$Domain/health/ready"
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
if ($failed -or $processFailures.Count -gt 0) {
    if ($processFailures.Count -gt 0) {
        Write-Warning ($processFailures -join [Environment]::NewLine)
    }
    throw "One or more deployment checks failed."
}

Write-Host "Deployment acceptance checks passed."
