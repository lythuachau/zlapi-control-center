[CmdletBinding()]
param(
    [string]$BackupRoot = "C:\ZlapiData\backups",
    [ValidateRange(1,720)][int]$MaximumAgeHours = 30
)

$ErrorActionPreference = "Stop"
$latest = Get-ChildItem -LiteralPath $BackupRoot -Filter 'zlapi-*.dump.aes' -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $latest) { throw "No encrypted ZLAPI database backup found in $BackupRoot" }
$age = [DateTime]::UtcNow - $latest.LastWriteTimeUtc
[ordered]@{ path=$latest.FullName; timestamp_utc=$latest.LastWriteTimeUtc.ToString('o'); age_hours=[Math]::Round($age.TotalHours,2); maximum_age_hours=$MaximumAgeHours } | ConvertTo-Json
if ($age.TotalHours -gt $MaximumAgeHours) { throw "Latest encrypted backup is stale: $([Math]::Round($age.TotalHours,2)) hours." }
