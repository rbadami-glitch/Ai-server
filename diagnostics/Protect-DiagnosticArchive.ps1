param(
    [Parameter(Mandatory = $true)][ValidateSet('Encrypt', 'Decrypt')][string]$Mode,
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$KeyPath
)

$ErrorActionPreference = 'Stop'
$maximumBytes = 90MB
$magic = [System.Text.Encoding]::ASCII.GetBytes('CWDIAG01')
if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw 'Input file was not found.'
}
if (Test-Path -LiteralPath $OutputPath) {
    throw 'Output file already exists; no file was changed.'
}
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw 'Key file was not found.'
}
$inputBytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $InputPath).Path)
if ($inputBytes.Length -gt $maximumBytes) {
    throw 'Input exceeds 90 MiB. Narrow the log collection before encrypting.'
}

$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
$rsa.FromXmlString([System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $KeyPath).Path))
$rsa.PersistKeyInCsp = $false
try {
    if ($Mode -eq 'Encrypt') {
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $keyMaterial = New-Object byte[] 64
        $iv = New-Object byte[] 16
        $random.GetBytes($keyMaterial)
        $random.GetBytes($iv)
        $random.Dispose()
        $aesKey = New-Object byte[] 32
        $macKey = New-Object byte[] 32
        [System.Array]::Copy($keyMaterial, 0, $aesKey, 0, 32)
        [System.Array]::Copy($keyMaterial, 32, $macKey, 0, 32)
        $wrapped = $rsa.Encrypt($keyMaterial, $true)
        if ($wrapped.Length -gt [UInt16]::MaxValue) { throw 'Wrapped key is too long.' }
        $aes = [System.Security.Cryptography.Aes]::Create()
        try {
            $aes.KeySize = 256
            $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
            $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
            $aes.Key = $aesKey
            $aes.IV = $iv
            $encryptor = $aes.CreateEncryptor()
            try { $cipher = $encryptor.TransformFinalBlock($inputBytes, 0, $inputBytes.Length) }
            finally { $encryptor.Dispose() }
        } finally { $aes.Dispose() }
        $headerLength = $magic.Length + 2 + $wrapped.Length + $iv.Length
        $signed = New-Object byte[] ($headerLength + $cipher.Length)
        [System.Array]::Copy($magic, 0, $signed, 0, $magic.Length)
        $wrappedLength = [System.BitConverter]::GetBytes([UInt16]$wrapped.Length)
        [System.Array]::Copy($wrappedLength, 0, $signed, $magic.Length, 2)
        [System.Array]::Copy($wrapped, 0, $signed, $magic.Length + 2, $wrapped.Length)
        [System.Array]::Copy($iv, 0, $signed, $magic.Length + 2 + $wrapped.Length, 16)
        [System.Array]::Copy($cipher, 0, $signed, $headerLength, $cipher.Length)
        $hmac = New-Object System.Security.Cryptography.HMACSHA256 -ArgumentList (,$macKey)
        try { $tag = $hmac.ComputeHash($signed) }
        finally { $hmac.Dispose() }
        $result = New-Object byte[] ($signed.Length + $tag.Length)
        [System.Array]::Copy($signed, 0, $result, 0, $signed.Length)
        [System.Array]::Copy($tag, 0, $result, $signed.Length, $tag.Length)
        [System.IO.File]::WriteAllBytes($OutputPath, $result)
        Write-Output 'Encrypted diagnostic archive. Only the private key can decrypt it.'
    } else {
        if ($inputBytes.Length -lt ($magic.Length + 2 + 16 + 16 + 32)) {
            throw 'Encrypted file is too short.'
        }
        for ($i = 0; $i -lt $magic.Length; $i++) {
            if ($inputBytes[$i] -ne $magic[$i]) { throw 'Encrypted file has the wrong format.' }
        }
        $wrappedSize = [System.BitConverter]::ToUInt16($inputBytes, $magic.Length)
        $wrappedStart = $magic.Length + 2
        $ivStart = $wrappedStart + $wrappedSize
        $cipherStart = $ivStart + 16
        $cipherSize = $inputBytes.Length - $cipherStart - 32
        if ($wrappedSize -lt 1 -or $cipherSize -lt 16) { throw 'Encrypted file is incomplete.' }
        $wrapped = New-Object byte[] $wrappedSize
        $iv = New-Object byte[] 16
        $cipher = New-Object byte[] $cipherSize
        [System.Array]::Copy($inputBytes, $wrappedStart, $wrapped, 0, $wrappedSize)
        [System.Array]::Copy($inputBytes, $ivStart, $iv, 0, 16)
        [System.Array]::Copy($inputBytes, $cipherStart, $cipher, 0, $cipherSize)
        $keyMaterial = $rsa.Decrypt($wrapped, $true)
        if ($keyMaterial.Length -ne 64) { throw 'Private key did not unwrap the archive keys.' }
        $aesKey = New-Object byte[] 32
        $macKey = New-Object byte[] 32
        [System.Array]::Copy($keyMaterial, 0, $aesKey, 0, 32)
        [System.Array]::Copy($keyMaterial, 32, $macKey, 0, 32)
        $hmac = New-Object System.Security.Cryptography.HMACSHA256 -ArgumentList (,$macKey)
        try { $expected = $hmac.ComputeHash($inputBytes, 0, $inputBytes.Length - 32) }
        finally { $hmac.Dispose() }
        $difference = 0
        for ($i = 0; $i -lt 32; $i++) {
            $difference = $difference -bor ($expected[$i] -bxor $inputBytes[$inputBytes.Length - 32 + $i])
        }
        if ($difference -ne 0) { throw 'Archive authentication failed; no plaintext was written.' }
        $aes = [System.Security.Cryptography.Aes]::Create()
        try {
            $aes.KeySize = 256
            $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
            $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
            $aes.Key = $aesKey
            $aes.IV = $iv
            $decryptor = $aes.CreateDecryptor()
            try { $plain = $decryptor.TransformFinalBlock($cipher, 0, $cipher.Length) }
            finally { $decryptor.Dispose() }
        } finally { $aes.Dispose() }
        [System.IO.File]::WriteAllBytes($OutputPath, $plain)
        Write-Output 'Authenticated and decrypted diagnostic archive.'
    }
} finally {
    $rsa.Dispose()
}
