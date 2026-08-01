[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$ServiceRoot = "C:\Services\ZlapiControl",
    [string]$DataRoot = "C:\ZlapiData",
    [string]$AdminUsername = "admin"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($DatabaseUrl -match "^postgres://") {
    $DatabaseUrl = $DatabaseUrl -replace "^postgres://", "postgresql+psycopg://"
} elseif ($DatabaseUrl -match "^postgresql://") {
    $DatabaseUrl = $DatabaseUrl -replace "^postgresql://", "postgresql+psycopg://"
} elseif ($DatabaseUrl -notmatch "^postgresql\+psycopg://") {
    throw "DatabaseUrl must be an external PostgreSQL URL."
}

if ($DatabaseUrl -notmatch "[?&]sslmode=") {
    $separator = if ($DatabaseUrl.Contains("?")) { "&" } else { "?" }
    $DatabaseUrl = "$DatabaseUrl${separator}sslmode=require"
}

$shared = Join-Path $ServiceRoot "shared"
New-Item -ItemType Directory -Path $shared -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null

function New-RandomSecret([int]$Bytes) {
    $buffer = [byte[]]::new($Bytes)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($buffer)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($buffer).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

$sessionSecret = New-RandomSecret 48
$adminPassword = (New-RandomSecret 24) + "!aA1"
$escapedDatabaseUrl = $DatabaseUrl.Replace('"', '\"')
$escapedDomain = $Domain.Replace('"', '\"')
$escapedDataRoot = $DataRoot.Replace('"', '\"')

$environment = @"
ZLAPI_APP_ENV=production
ZLAPI_DATABASE_URL="$escapedDatabaseUrl"
ZLAPI_SESSION_SECRET="$sessionSecret"
ZLAPI_DATA_DIR="$escapedDataRoot"
ZLAPI_DOMAIN="$escapedDomain"
ZLAPI_TRUSTED_HOSTS="$escapedDomain,localhost,127.0.0.1"
ZLAPI_COOKIE_SECURE=true
ZLAPI_AUTO_CREATE_SCHEMA=false
ZLAPI_DATABASE_CAP_BYTES=1073741824
ZLAPI_MAX_UPLOAD_MB=25
ZLAPI_INBOUND_DOWNLOAD_MAX_MB=250
ZLAPI_GIF_LIBRARY_MAX_ITEMS=200
ZLAPI_GIF_LIBRARY_MAX_BYTES=2147483648
ZLAPI_ARCHIVE_AFTER_DAYS=90
ZLAPI_API_KEY_REQUESTS_PER_MINUTE=120
ZLAPI_SESSION_REQUESTS_PER_MINUTE=300
ZLAPI_LOGIN_ATTEMPTS_PER_MINUTE=10
ZLAPI_PG_DUMP_PATH="C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"
ZLAPI_RCLONE_REMOTE=
ZLAPI_ALERT_WEBHOOK_URL=
ZLAPI_BOOTSTRAP_ADMIN_USERNAME="$AdminUsername"
ZLAPI_BOOTSTRAP_ADMIN_PASSWORD="$adminPassword"
"@

$envPath = Join-Path $shared ".env"
[IO.File]::WriteAllText($envPath, $environment, [Text.UTF8Encoding]::new($false))

Write-Host "Configuration created at $envPath"
Write-Host "Initial administrator: $AdminUsername"
Write-Host "Initial password (copy now): $adminPassword"
Write-Host "Deploy-Release.ps1 clears the bootstrap password after a successful health check."
