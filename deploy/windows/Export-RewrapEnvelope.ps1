[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProtectedKeyPath,
    [Parameter(Mandatory = $true)]
    [string]$PublicCertificatePath,
    [Parameter(Mandatory = $true)]
    [string]$EnvelopePath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

$keyPath = (Resolve-Path -LiteralPath $ProtectedKeyPath).Path
$certificatePath = (Resolve-Path -LiteralPath $PublicCertificatePath).Path
$target = [IO.Path]::GetFullPath($EnvelopePath)
if (Test-Path -LiteralPath $target) {
    throw "Refusing to overwrite existing envelope: $target"
}
$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Envelope output directory does not exist: $parent"
}

$protected = [IO.File]::ReadAllBytes($keyPath)
$dataKey = $null
$ciphertext = $null
$rsa = $null
$certificate = $null
try {
    $dataKey = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected,
        $null,
        [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    if ($dataKey.Length -ne 32) {
        throw "DPAPI payload is not a 32-byte ZLAPI data key."
    }

    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificatePath)
    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
    if ($null -eq $rsa -or $rsa.KeySize -lt 3072) {
        throw "Rewrap certificate must use RSA with at least 3072 bits."
    }
    $ciphertext = $rsa.Encrypt($dataKey, [Security.Cryptography.RSAEncryptionPadding]::OaepSHA256)
    $fingerprint = [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData($certificate.RawData)
    ).ToLowerInvariant()
    $payload = [ordered]@{
        version = 1
        algorithm = "RSA-OAEP-SHA256"
        certificate_sha256 = $fingerprint
        ciphertext = [Convert]::ToBase64String($ciphertext)
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($target, $payload + "`n", [Text.Encoding]::ASCII)
    Write-Output "Rewrap envelope created at $target"
}
finally {
    if ($null -ne $dataKey) { [Array]::Clear($dataKey, 0, $dataKey.Length) }
    if ($null -ne $protected) { [Array]::Clear($protected, 0, $protected.Length) }
    if ($null -ne $ciphertext) { [Array]::Clear($ciphertext, 0, $ciphertext.Length) }
    if ($null -ne $rsa) { $rsa.Dispose() }
    if ($null -ne $certificate) { $certificate.Dispose() }
}
