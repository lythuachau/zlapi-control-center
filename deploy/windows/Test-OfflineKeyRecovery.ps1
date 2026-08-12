[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security
function Test-FixedTimeEqual([byte[]]$Left,[byte[]]$Right) {
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    $difference = $Left.Length -bxor $Right.Length
    $length = [Math]::Min($Left.Length,$Right.Length)
    for ($index=0;$index -lt $length;$index++) { $difference = $difference -bor ($Left[$index] -bxor $Right[$index]) }
    return $difference -eq 0
}
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$root = Join-Path $tempBase ("zlapi-offline-recovery-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$password = ConvertTo-SecureString ("Test-" + [Guid]::NewGuid().ToString('N')) -AsPlainText -Force
$dataKey = [byte[]]::new(32)
$protected = $null
$verification = $null
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($dataKey)
    $protected = [Security.Cryptography.ProtectedData]::Protect($dataKey,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    $source = Join-Path $root "source.dpapi"
    $public = Join-Path $root "recovery.cer"
    $private = Join-Path $root "recovery.pfx"
    $envelope = Join-Path $root "envelope.json"
    $target = Join-Path $root "target.dpapi"
    [IO.File]::WriteAllBytes($source,$protected)
    & (Join-Path $PSScriptRoot "New-OfflineRecoveryCertificate.ps1") -PublicCertificatePath $public -PrivateKeyPath $private -PrivateKeyPassword $password -ValidDays 30
    & (Join-Path $PSScriptRoot "Export-RewrapEnvelope.ps1") -ProtectedKeyPath $source -PublicCertificatePath $public -EnvelopePath $envelope
    & (Join-Path $PSScriptRoot "Import-OfflineRecoveryKey.ps1") -EnvelopePath $envelope -PrivateKeyPath $private -PrivateKeyPassword $password -OutputKeyPath $target
    $verification = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($target),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    if (-not (Test-FixedTimeEqual $dataKey $verification)) { throw "Recovered key mismatch." }
    Write-Output "ZLAPI_FAKE_OFFLINE_KEY_RECOVERY_OK"
}
finally {
    $rng.Dispose()
    foreach ($buffer in @($dataKey,$protected,$verification)) { if ($null -ne $buffer) { [Array]::Clear($buffer,0,$buffer.Length) } }
    if (Test-Path -LiteralPath $root) {
        $resolved = (Resolve-Path -LiteralPath $root).Path
        if (-not $resolved.StartsWith($tempBase + '\',[StringComparison]::OrdinalIgnoreCase)) { throw "Refusing unsafe test cleanup." }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
