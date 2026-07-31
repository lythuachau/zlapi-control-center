[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvelopePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)]
    [string]$OutputKeyPath,
    [ValidateSet("LocalMachine", "CurrentUser")]
    [string]$StoreLocation = "LocalMachine"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

function Test-FixedTimeEqual {
    param(
        [byte[]]$Left,
        [byte[]]$Right
    )
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    $difference = $Left.Length -bxor $Right.Length
    $length = [Math]::Min($Left.Length, $Right.Length)
    for ($index = 0; $index -lt $length; $index++) {
        $difference = $difference -bor ($Left[$index] -bxor $Right[$index])
    }
    return $difference -eq 0
}

$envelopeFile = (Resolve-Path -LiteralPath $EnvelopePath).Path
$target = [IO.Path]::GetFullPath($OutputKeyPath)
if (Test-Path -LiteralPath $target) {
    throw "Refusing to overwrite existing DPAPI key: $target"
}
$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "DPAPI key output directory does not exist: $parent"
}

$payload = Get-Content -Raw -LiteralPath $envelopeFile | ConvertFrom-Json
if ($payload.version -ne 1 -or $payload.algorithm -ne "RSA-OAEP-SHA256") {
    throw "Unsupported rewrap envelope format."
}
$thumbprint = ($CertificateThumbprint -replace "\s", "").ToUpperInvariant()
$certificatePath = "Cert:\$StoreLocation\My\$thumbprint"
$certificate = Get-Item -LiteralPath $certificatePath
$sha256 = [Security.Cryptography.SHA256]::Create()
$certificateHash = $sha256.ComputeHash($certificate.RawData)
$fingerprint = ([BitConverter]::ToString($certificateHash) -replace "-", "").ToLowerInvariant()
$sha256.Dispose()
[Array]::Clear($certificateHash, 0, $certificateHash.Length)
$fingerprintBytes = [Text.Encoding]::ASCII.GetBytes($fingerprint)
$expectedFingerprintBytes = [Text.Encoding]::ASCII.GetBytes([string]$payload.certificate_sha256)
if (-not (Test-FixedTimeEqual -Left $fingerprintBytes -Right $expectedFingerprintBytes)) {
    throw "Envelope certificate fingerprint does not match the selected certificate."
}

$ciphertext = [Convert]::FromBase64String([string]$payload.ciphertext)
$dataKey = $null
$protected = $null
$verification = $null
$rsa = $null
$temporary = "$target.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($certificate)
    if ($null -eq $rsa) { throw "Selected certificate has no accessible private key." }
    $dataKey = $rsa.Decrypt($ciphertext, [Security.Cryptography.RSAEncryptionPadding]::OaepSHA256)
    if ($dataKey.Length -ne 32) { throw "Envelope did not contain a 32-byte ZLAPI data key." }
    $protected = [Security.Cryptography.ProtectedData]::Protect(
        $dataKey,
        $null,
        [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    [IO.File]::WriteAllBytes($temporary, $protected)
    $verification = [Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes($temporary),
        $null,
        [Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    if (-not (Test-FixedTimeEqual $dataKey $verification)) {
        throw "DPAPI verification failed."
    }
    Move-Item -LiteralPath $temporary -Destination $target
    Write-Output "Rewrapped DPAPI key written to $target"
}
finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    foreach ($buffer in @($ciphertext, $dataKey, $protected, $verification)) {
        if ($null -ne $buffer) { [Array]::Clear($buffer, 0, $buffer.Length) }
    }
    if ($null -ne $rsa) { $rsa.Dispose() }
}
