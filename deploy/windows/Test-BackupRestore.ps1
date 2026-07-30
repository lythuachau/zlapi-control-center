[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [Parameter(Mandatory = $true)]
    [string]$TemporaryDatabaseUrl,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$PgRestorePath = "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $TemporaryDatabaseUrl.Contains("restore_test")) {
    throw "TemporaryDatabaseUrl must contain 'restore_test' to prevent restoring over production."
}
$connectionUri = [Uri]($TemporaryDatabaseUrl -replace "^postgresql\+psycopg://", "postgresql://")
$userInfo = $connectionUri.UserInfo.Split(":", 2)
if ($userInfo.Count -ne 2 -or -not $connectionUri.Host -or -not $connectionUri.AbsolutePath) {
    throw "TemporaryDatabaseUrl is not a complete PostgreSQL connection URL."
}
$restoreEnvironment = @{
    PGHOST = $connectionUri.Host
    PGPORT = if ($connectionUri.Port -gt 0) { $connectionUri.Port.ToString() } else { "5432" }
    PGUSER = [Uri]::UnescapeDataString($userInfo[0])
    PGPASSWORD = [Uri]::UnescapeDataString($userInfo[1])
    PGDATABASE = $connectionUri.AbsolutePath.TrimStart("/")
    PGSSLMODE = "require"
}

$backup = (Resolve-Path -LiteralPath $BackupPath).Path
$current = Join-Path $ServiceRoot "current"
$shared = Join-Path $ServiceRoot "shared"
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("zlapi-restore-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
$plain = Join-Path $temporaryRoot "restore.dump"

try {
    Push-Location $shared
    try {
        & (Join-Path $current ".venv\Scripts\python.exe") `
            -m control_center.backup_tools decrypt `
            --input $backup `
            --output $plain
        if ($LASTEXITCODE -ne 0) {
            throw "Backup decryption failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
    foreach ($name in $restoreEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $restoreEnvironment[$name], "Process")
    }
    try {
        & $PgRestorePath `
            --dbname $restoreEnvironment.PGDATABASE `
            --exit-on-error `
            --no-owner `
            --clean `
            --if-exists `
            $plain
        if ($LASTEXITCODE -ne 0) {
            throw "pg_restore failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        foreach ($name in $restoreEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
    Write-Host "Backup restored successfully into the temporary database."
}
finally {
    $resolvedTemp = (Resolve-Path -LiteralPath $temporaryRoot).Path
    $systemTemp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
    if (-not $resolvedTemp.StartsWith($systemTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the system temp directory."
    }
    Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
}
