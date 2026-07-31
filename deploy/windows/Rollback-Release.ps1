[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseName,
    [string]$ServiceRoot = "C:\Services\ZlapiControl"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Service-ProcessHelpers.ps1")

function Remove-VerifiedJunction([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Path is not a junction; refusing to replace it."
    }
    [IO.Directory]::Delete($item.FullName)
}

$releases = (Resolve-Path -LiteralPath (Join-Path $ServiceRoot "releases")).Path
$target = (Resolve-Path -LiteralPath (Join-Path $releases $ReleaseName)).Path
if (-not (Test-ZlapiPathWithinRoot -Path $target -Root $releases)) {
    throw "Release target is outside the releases directory."
}
if (-not (Test-Path -LiteralPath (Join-Path $target ".venv\Scripts\python.exe"))) {
    throw "Release is incomplete."
}

$current = Join-Path $ServiceRoot "current"
$currentItem = Get-Item -LiteralPath $current -Force
if (-not ($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "$current is not a junction; refusing to replace it."
}
$previousTarget = [string]$currentItem.Target
$serviceModules = @{
    "zlapi-web" = "uvicorn"
    "zlapi-worker" = "control_center.worker"
    "zlapi-listener" = "control_center.listener"
}
$stopOrder = @("zlapi-listener", "zlapi-worker", "zlapi-web")
$startOrder = @("zlapi-web", "zlapi-worker", "zlapi-listener")
$junctionChanged = $false

try {
    foreach ($serviceId in $stopOrder) {
        $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
        Stop-ZlapiServiceSafely -Name $serviceId -ServiceRoot $ServiceRoot -Module $module
    }

    Remove-VerifiedJunction $current
    $junctionChanged = $true
    New-Item -ItemType Junction -Path $current -Target $target | Out-Null

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
        throw "Rollback health check did not become ready within 60 seconds."
    }
}
catch {
    foreach ($serviceId in $stopOrder) {
        $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
        try {
            Stop-ZlapiServiceSafely -Name $serviceId -ServiceRoot $ServiceRoot -Module $module
        }
        catch {
            Write-Warning "Recovery cleanup for $serviceId failed: $($_.Exception.Message)"
        }
    }

    if ($junctionChanged) {
        if (Test-Path -LiteralPath $current) {
            Remove-VerifiedJunction $current
        }
        $resolvedPrevious = (Resolve-Path -LiteralPath $previousTarget).Path
        if (-not (Test-ZlapiPathWithinRoot -Path $resolvedPrevious -Root $releases)) {
            throw "Previous release target is outside the releases directory."
        }
        New-Item -ItemType Junction -Path $current -Target $resolvedPrevious | Out-Null
    }

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
    throw
}

Write-Host "Rolled back to $ReleaseName."
