param(
    [string]$Repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github',
    [string]$LogPath = 'C:\ProgramData\OpenWebUI\logs\response-metadata-capture.jsonl',
    [string]$TempRoot = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-Response-Metadata-Capture-20261111',
    [string]$Branch = 'response-metadata-capture-20261111'
)

$ErrorActionPreference = 'Stop'
$repoFull = [System.IO.Path]::GetFullPath($Repo)
$tempFull = [System.IO.Path]::GetFullPath($TempRoot)
if ($tempFull.StartsWith($repoFull + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Temporary data must stay outside Git.' }
if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) { throw 'No capture log exists yet. Use Open WebUI after enabling the filter.' }
if ((Get-Item -LiteralPath $LogPath).Length -gt 6MB) { throw 'Capture log is larger than the expected cap; inspect it first.' }
if (Test-Path -LiteralPath $TempRoot) { throw 'Temporary result folder already exists. Inspect it before retrying.' }
$status = @(git -C $Repo status --porcelain)
if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) { throw 'Git checkout is not clean.' }
$branchExists = @(git -C $Repo branch --list $Branch)
if ($LASTEXITCODE -ne 0 -or $branchExists.Count -gt 0) { throw 'Temporary branch already exists.' }

$null = New-Item -ItemType Directory -Path $TempRoot
$raw = Join-Path $TempRoot 'raw'
$null = New-Item -ItemType Directory -Path $raw
$sanitized = Join-Path $raw 'capture.jsonl'
$allowedStages = @('request', 'stream_finish', 'ui_completion', 'outlet')
$allowedReasons = @('stop', 'length', 'tool_calls', 'content_filter', 'error', 'cancelled', 'other', 'response.completed', 'response.failed', 'response.incomplete')
$count = 0
foreach ($line in [System.IO.File]::ReadLines($LogPath)) {
    if (-not $line.Trim()) { continue }
    $item = $line | ConvertFrom-Json
    if ($item.stage -notin $allowedStages -or $item.trace -notmatch '^[0-9a-f]{20}$') { throw 'Unexpected log schema; no upload was attempted.' }
    $null = [DateTimeOffset]::Parse($item.utc)
    if ($null -ne $item.reason -and $item.reason -notin $allowedReasons) { throw 'Unexpected finish reason; no upload was attempted.' }
    foreach ($field in @('max_tokens', 'num_predict', 'completion_tokens')) {
        $value = $item.$field
        if ($null -ne $value -and ($value -isnot [ValueType] -or $value -lt 0 -or $value -gt 1000000)) { throw "Unexpected $field; no upload was attempted." }
    }
    $row = [ordered]@{
        utc = [string]$item.utc; stage = [string]$item.stage; trace = [string]$item.trace
        max_tokens = $item.max_tokens; num_predict = $item.num_predict
        stream = ($item.stream -eq $true); reason = $item.reason
        completion_tokens = $item.completion_tokens
        done = ($item.done -eq $true); error = ($item.error -eq $true)
    }
    ($row | ConvertTo-Json -Compress -Depth 3) | Add-Content -LiteralPath $sanitized -Encoding UTF8
    $count++
}
if ($count -eq 0) { throw 'Capture log has no complete records. Temporary folder remains for review.' }
@{ captured_at_utc = [DateTime]::UtcNow.ToString('o'); records = $count; source_bytes = (Get-Item -LiteralPath $LogPath).Length } |
    ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $raw 'manifest.json') -Encoding UTF8
$zip = Join-Path $TempRoot 'response-metadata.zip'
Compress-Archive -Path (Join-Path $raw '*') -DestinationPath $zip -CompressionLevel Optimal
$encrypted = Join-Path $TempRoot 'response-metadata.zip.enc'
& (Join-Path $Repo 'diagnostics\Protect-DiagnosticArchive.ps1') -Mode Encrypt -InputPath $zip -OutputPath $encrypted -KeyPath (Join-Path $Repo 'diagnostics\response-audit-public.xml')
if (-not (Test-Path -LiteralPath $encrypted -PathType Leaf)) { throw 'Encryption failed; no upload was attempted.' }
$worktree = Join-Path $TempRoot 'upload-worktree'
git -C $Repo worktree add -b $Branch $worktree HEAD
if ($LASTEXITCODE -ne 0) { throw 'Could not create upload worktree.' }
Copy-Item -LiteralPath $encrypted -Destination (Join-Path $worktree 'response-metadata.zip.enc')
git -C $worktree add -- response-metadata.zip.enc
if ($LASTEXITCODE -ne 0) { throw 'Could not stage encrypted archive.' }
$staged = @(git -C $worktree diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne 'response-metadata.zip.enc') { throw 'Unexpected staged file. Push was stopped.' }
git -C $worktree commit -m 'Add encrypted temporary response metadata capture'
if ($LASTEXITCODE -ne 0) { throw 'Could not commit encrypted archive.' }
git -C $worktree push -u origin $Branch
if ($LASTEXITCODE -ne 0) { throw "Push failed. Temporary data remains at $TempRoot for retry." }
Write-Output "Uploaded $count metadata records to branch $Branch. Temporary files remain at $TempRoot."
