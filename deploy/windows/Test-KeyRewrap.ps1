[CmdletBinding()]
param(
    [string]$TestRoot = "C:\Migration\tmp\zlapi-rewrap-fake-integration"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

$allowedRoot = (Resolve-Path -LiteralPath "C:\Migration\tmp").Path
$root = [IO.Path]::GetFullPath($TestRoot)
if (-not $root.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "TestRoot must be a child of C:\Migration\tmp."
}
if (Test-Path -LiteralPath $root) {
    throw "Refusing to reuse existing test directory: $root"
}
New-Item -ItemType Directory -Path $root | Out-Null

$publicCertificate = Join-Path $root "public.cer"
$sourceKey = Join-Path $root "old-master-key.dpapi"
$envelope = Join-Path $root "master-key.envelope.json"
$targetKey = Join-Path $root "new-master-key.dpapi"
$dataKey = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($dataKey)
$protected = [Security.Cryptography.ProtectedData]::Protect(
    $dataKey,
    $null,
    [Security.Cryptography.DataProtectionScope]::LocalMachine
)
[IO.File]::WriteAllBytes($sourceKey, $protected)

$certificate = $null
$verification = $null
try {
    & (Join-Path $PSScriptRoot "New-RewrapCertificate.ps1") `
        -PublicCertificatePath $publicCertificate `
        -StoreLocation CurrentUser `
        -ValidDays 1
    $exported = [Security.Cryptography.X509Certificates.X509Certificate2]::new($publicCertificate)
    $thumbprint = $exported.Thumbprint
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint"

    & (Join-Path $PSScriptRoot "Export-RewrapEnvelope.ps1") `
        -ProtectedKeyPath $sourceKey `
        -PublicCertificatePath $publicCertificate `
        -EnvelopePath $envelope
    & (Join-Path $PSScriptRoot "Import-RewrappedMasterKey.ps1") `
        -EnvelopePath $envelope `
        -CertificateThumbprint $thumbprint `
        -OutputKeyPath $targetKey `
        -StoreLocation CurrentUser

    $verification = [Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes($targetKey),
        $null,
        [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    if (-not [Security.Cryptography.CryptographicOperations]::FixedTimeEquals($dataKey, $verification)) {
        throw "Fake rewrap integration key mismatch."
    }
    Write-Output "FAKE_REWRAP_INTEGRATION_OK"
}
finally {
    foreach ($buffer in @($dataKey, $protected, $verification)) {
        if ($null -ne $buffer) { [Array]::Clear($buffer, 0, $buffer.Length) }
    }
    if ($null -ne $certificate) {
        Remove-Item -LiteralPath $certificate.PSPath -Force
    }
    if (Test-Path -LiteralPath $root) {
        $resolved = (Resolve-Path -LiteralPath $root).Path
        if (-not $resolved.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a directory outside C:\Migration\tmp."
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
