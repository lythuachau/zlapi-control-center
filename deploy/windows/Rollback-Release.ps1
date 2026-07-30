[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseName,
    [string]$ServiceRoot = "C:\Services\ZlapiControl"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Remove-VerifiedJunction([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Path is not a junction; refusing to replace it."
    }
    [IO.Directory]::Delete($item.FullName)
}

$releases = (Resolve-Path -LiteralPath (Join-Path $ServiceRoot "releases")).Path
$target = (Resolve-Path -LiteralPath (Join-Path $releases $ReleaseName)).Path
if (-not $target.StartsWith($releases, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release target is outside the releases directory."
}
if (-not (Test-Path -LiteralPath (Join-Path $target ".venv\Scripts\python.exe"))) {
    throw "Release is incomplete."
}

$current = Join-Path $ServiceRoot "current"
if (Test-Path -LiteralPath $current) {
    $currentItem = Get-Item -LiteralPath $current -Force
    if (-not ($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$current is not a junction; refusing to replace it."
    }
    Remove-VerifiedJunction $current
}
New-Item -ItemType Junction -Path $current -Target $target | Out-Null

foreach ($serviceId in @("zlapi-web", "zlapi-worker", "zlapi-listener")) {
    if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) {
        Restart-Service -Name $serviceId -Force
    }
}

Write-Host "Rolled back to $ReleaseName."
