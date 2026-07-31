[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PublicCertificatePath,
    [ValidateSet("LocalMachine", "CurrentUser")]
    [string]$StoreLocation = "LocalMachine",
    [int]$ValidDays = 2
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$target = [IO.Path]::GetFullPath($PublicCertificatePath)
if (Test-Path -LiteralPath $target) {
    throw "Refusing to overwrite existing certificate: $target"
}
$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Certificate output directory does not exist: $parent"
}

$subject = "CN=ZLAPI Migration Rewrap $([Guid]::NewGuid().ToString('N'))"
$certificate = New-SelfSignedCertificate `
    -Subject $subject `
    -CertStoreLocation "Cert:\$StoreLocation\My" `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -KeyExportPolicy NonExportable `
    -KeyUsage KeyEncipherment, DataEncipherment `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddDays($ValidDays) `
    -Provider "Microsoft Software Key Storage Provider"
if ($null -eq $certificate -or -not $certificate.Thumbprint) {
    throw "Windows certificate enrollment did not return a certificate."
}

Export-Certificate -Cert $certificate -FilePath $target -Type CERT | Out-Null
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "Windows did not export the public rewrap certificate."
}
Write-Output "Certificate created. Store=$StoreLocation Thumbprint=$($certificate.Thumbprint)"
Write-Output "Public certificate exported to $target"
