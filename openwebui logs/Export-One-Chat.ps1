param(
    [string]$Database = 'C:\downloads\open-webui\data\webui.db',
    [string]$ChatLink
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ChatLink)) {
    $ChatLink = Read-Host 'Paste the exact Open WebUI chat link or chat ID'
}
if ([string]::IsNullOrWhiteSpace($chatLink)) { throw 'A chat link or ID is required.' }

$scriptPath = Join-Path $PSScriptRoot 'export_one_chat.py'
$certificate = Join-Path $PSScriptRoot 'review-public.cer'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'The chat exporter is missing.' }
if (-not (Test-Path -LiteralPath $certificate)) { throw 'The review public certificate is missing.' }

$temporary = Join-Path $PSScriptRoot ('openwebui-chat-' + [guid]::NewGuid().ToString('N') + '.json')
$encrypted = Join-Path $PSScriptRoot ('chat-review-' + [guid]::NewGuid().ToString('N') + '.p7m')
try {
    $exportSummary = & python -B $scriptPath --db $Database --chat $chatLink --output $temporary
    if ($LASTEXITCODE -ne 0) { throw 'Chat export failed.' }
    $plaintext = Get-Content -LiteralPath $temporary -Raw -Encoding UTF8
    $cms = Protect-CmsMessage -To $certificate -Content $plaintext
    [System.IO.File]::WriteAllText($encrypted, $cms, [System.Text.Encoding]::ASCII)
    Write-Host $exportSummary
    Write-Host ('Encrypted chat file: ' + $encrypted)
    Write-Host 'The plaintext temporary export is being removed.'
    Write-Output $encrypted
} finally {
    Remove-Item -LiteralPath $temporary -ErrorAction SilentlyContinue
    $plaintext = $null
}
