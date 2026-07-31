[CmdletBinding()]
param(
    [string]$SourcePath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$PythonPath = "python",
    [switch]$SkipServiceRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Service-ProcessHelpers.ps1")

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Remove-VerifiedJunction([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Path exists but is not a junction; refusing to replace it."
    }
    [IO.Directory]::Delete($item.FullName)
}

$source = (Resolve-Path -LiteralPath $SourcePath).Path
if (-not (Test-Path -LiteralPath (Join-Path $source "pyproject.toml"))) {
    throw "SourcePath does not contain pyproject.toml."
}
$pythonCommand = Get-Command $PythonPath -CommandType Application -ErrorAction Stop
$pythonExecutable = $pythonCommand.Source
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "PythonPath does not point to an executable: $PythonPath"
}

$releases = Join-Path $ServiceRoot "releases"
$shared = Join-Path $ServiceRoot "shared"
$current = Join-Path $ServiceRoot "current"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$release = Join-Path $releases $stamp
foreach ($path in @($ServiceRoot, $releases, $shared)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $shared ".env"))) {
    throw "Missing $shared\.env. Run Initialize-Config.ps1 first."
}

New-Item -ItemType Directory -Path $release -Force | Out-Null
$null = robocopy.exe $source $release /E /XD .git .venv node_modules .data __pycache__ .pytest_cache .ruff_cache chat-ui-concepts /XF *.pyc
if ($LASTEXITCODE -gt 7) {
    throw "Robocopy failed with exit code $LASTEXITCODE."
}

Push-Location $release
try {
    $env:ZLAPI_ENV_FILE = Join-Path $shared ".env"
    npm ci
    Assert-NativeSuccess "npm ci"
    npm run build
    Assert-NativeSuccess "Frontend asset build"
    & $pythonExecutable -m venv .venv
    Assert-NativeSuccess "Virtual environment creation"
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
    Assert-NativeSuccess "pip upgrade"
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.lock
    Assert-NativeSuccess "Production dependency installation"
    & ".\.venv\Scripts\python.exe" -m pip install --no-deps .
    Assert-NativeSuccess "Application installation"
    & ".\.venv\Scripts\python.exe" -m alembic -c alembic.ini upgrade head
    Assert-NativeSuccess "Alembic migration"
    & ".\.venv\Scripts\python.exe" -c "from control_center.config import get_settings; from control_center.security import SecretBox; SecretBox(get_settings()); print('Encryption key ready')"
    Assert-NativeSuccess "Encryption key initialization"
    & ".\.venv\Scripts\python.exe" -m control_center.cli doctor
    Assert-NativeSuccess "Application doctor"
}
finally {
    Pop-Location
}

$serviceModules = @{
    "zlapi-web" = "uvicorn"
    "zlapi-worker" = "control_center.worker"
    "zlapi-listener" = "control_center.listener"
}
$stopOrder = @("zlapi-listener", "zlapi-worker", "zlapi-web")
$startOrder = @("zlapi-web", "zlapi-worker", "zlapi-listener")
$previousTarget = $null
if (Test-Path -LiteralPath $current) {
    $currentItem = Get-Item -LiteralPath $current -Force
    if (-not ($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$current exists but is not a junction; refusing to replace it."
    }
    $previousTarget = [string]$currentItem.Target
}

$junctionChanged = $false
try {
    if (-not $SkipServiceRestart) {
        foreach ($serviceId in $stopOrder) {
            $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
            Stop-ZlapiServiceSafely -Name $serviceId -ServiceRoot $ServiceRoot -Module $module
        }
    }

    if (Test-Path -LiteralPath $current) {
        Remove-VerifiedJunction $current
        $junctionChanged = $true
    }
    New-Item -ItemType Junction -Path $current -Target $release | Out-Null
    $junctionChanged = $true

    if (-not $SkipServiceRestart) {
        foreach ($serviceId in $startOrder) {
            $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
            if (-not [string]::IsNullOrWhiteSpace($module)) {
                # A final scoped cleanup closes the race between stop and start.
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
            throw "Health check did not become ready within 60 seconds."
        }
    }
}
catch {
    if (-not $SkipServiceRestart) {
        foreach ($serviceId in $stopOrder) {
            $module = if ($serviceModules.ContainsKey($serviceId)) { $serviceModules[$serviceId] } else { "" }
            try {
                Stop-ZlapiServiceSafely -Name $serviceId -ServiceRoot $ServiceRoot -Module $module
            }
            catch {
                Write-Warning "Rollback cleanup for $serviceId failed: $($_.Exception.Message)"
            }
        }
    }

    if ($previousTarget) {
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
        if (-not $SkipServiceRestart) {
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
        }
    }
    throw
}

if (-not $SkipServiceRestart) {
    $envPath = Join-Path $shared ".env"
    $environment = [IO.File]::ReadAllText($envPath)
    $environment = [Text.RegularExpressions.Regex]::Replace(
        $environment,
        "(?m)^ZLAPI_BOOTSTRAP_ADMIN_PASSWORD=.*$",
        "ZLAPI_BOOTSTRAP_ADMIN_PASSWORD="
    )
    [IO.File]::WriteAllText($envPath, $environment, [Text.UTF8Encoding]::new($false))
}

Write-Host "Release $stamp deployed successfully."
