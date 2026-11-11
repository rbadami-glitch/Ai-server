param(
    [Parameter(Mandatory = $true)][string]$PublicKeyPath,
    [Parameter(Mandatory = $true)][string]$PrivateKeyPath
)

$ErrorActionPreference = 'Stop'
if ((Test-Path -LiteralPath $PublicKeyPath) -or (Test-Path -LiteralPath $PrivateKeyPath)) {
    throw 'A key file already exists; no file was changed.'
}
$publicParent = Split-Path -Parent $PublicKeyPath
$privateParent = Split-Path -Parent $PrivateKeyPath
if (-not (Test-Path -LiteralPath $publicParent -PathType Container) -or
    -not (Test-Path -LiteralPath $privateParent -PathType Container)) {
    throw 'Both destination directories must already exist.'
}

$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider(3072)
try {
    $rsa.PersistKeyInCsp = $false
    [System.IO.File]::WriteAllText($PrivateKeyPath, $rsa.ToXmlString($true),
        [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($PublicKeyPath, $rsa.ToXmlString($false),
        [System.Text.Encoding]::UTF8)
    Write-Output 'Created the public key and local private key. Keep the private key outside Git.'
} finally {
    $rsa.Dispose()
}
