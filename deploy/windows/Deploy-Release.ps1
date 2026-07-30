[CmdletBinding()]
param(
    [string]$SourcePath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [switch]$SkipServiceRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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

function Restart-ServiceSafely([string]$Name, [int]$TimeoutSeconds = 30) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        return
    }
    if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
        & sc.exe stop $Name | Out-Null
        try {
            $service.WaitForStatus(
                [ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds($TimeoutSeconds)
            )
        }
        catch {
            $service.Refresh()
            if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
                $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$Name'"
                $servicePid = [int]$serviceInfo.ProcessId
                if ($servicePid -gt 0) {
                    Write-Warning "Service $Name did not stop within $TimeoutSeconds seconds; terminating its process tree."
                    & taskkill.exe /PID $servicePid /T /F | Out-Null
                }
                else {
                    # WinSW can reach Stopped between WaitForStatus timing out and
                    # Win32_Service being queried. Give SCM one final refresh before
                    # treating a zero process ID as an actual failure.
                    Start-Sleep -Milliseconds 750
                    $service.Refresh()
                    if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
                        throw "Service $Name did not stop and has no process ID to terminate."
                    }
                }
                $service = Get-Service -Name $Name
                if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
                    $service.WaitForStatus(
                        [ServiceProcess.ServiceControllerStatus]::Stopped,
                        [TimeSpan]::FromSeconds(15)
                    )
                }
            }
        }
    }
    Start-Service -Name $Name
    $service = Get-Service -Name $Name
    $service.WaitForStatus(
        [ServiceProcess.ServiceControllerStatus]::Running,
        [TimeSpan]::FromSeconds(30)
    )
}

$source = (Resolve-Path -LiteralPath $SourcePath).Path
if (-not (Test-Path -LiteralPath (Join-Path $source "pyproject.toml"))) {
    throw "SourcePath does not contain pyproject.toml."
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
$null = robocopy.exe $source $release /E /XD .git node_modules .data __pycache__ .pytest_cache .ruff_cache chat-ui-concepts /XF *.pyc
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
    python -m venv .venv
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

$previousTarget = $null
if (Test-Path -LiteralPath $current) {
    $currentItem = Get-Item -LiteralPath $current -Force
    if (-not ($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$current exists but is not a junction; refusing to replace it."
    }
    $previousTarget = $currentItem.Target
    Remove-VerifiedJunction $current
}
New-Item -ItemType Junction -Path $current -Target $release | Out-Null

try {
    if (-not $SkipServiceRestart) {
        foreach ($serviceId in @("zlapi-web", "zlapi-worker", "zlapi-listener")) {
            Restart-ServiceSafely -Name $serviceId
            Start-Sleep -Seconds 2
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
    if (Test-Path -LiteralPath $current) {
        Remove-VerifiedJunction $current
    }
    if ($previousTarget) {
        $resolvedPrevious = (Resolve-Path -LiteralPath $previousTarget).Path
        if (-not $resolvedPrevious.StartsWith((Resolve-Path -LiteralPath $releases).Path, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Previous release target is outside the releases directory."
        }
        New-Item -ItemType Junction -Path $current -Target $resolvedPrevious | Out-Null
        foreach ($serviceId in @("zlapi-web", "zlapi-worker", "zlapi-listener")) {
            Restart-ServiceSafely -Name $serviceId
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
