[CmdletBinding()]
param(
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$TaskName = "ZLAPI Control Maintenance",
    [datetime]$DailyAt = "02:15"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run elevated." }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw "Task already exists: $TaskName" }
$python = Join-Path $ServiceRoot "current\.venv\Scripts\python.exe"
$shared = Join-Path $ServiceRoot "shared"
foreach ($path in @($python,(Join-Path $shared '.env'))) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file missing: $path" } }
$action = New-ScheduledTaskAction -Execute $python -Argument "-m control_center.maintenance all" -WorkingDirectory $shared
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
Write-Output "ZLAPI_MAINTENANCE_TASK_REGISTERED Name=$TaskName State=$($task.State) DailyAt=$($DailyAt.ToString('HH:mm'))"
