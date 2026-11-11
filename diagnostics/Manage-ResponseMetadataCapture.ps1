param(
    [ValidateSet('Install', 'Status', 'Disable')][string]$Action = 'Status',
    [string]$Repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github',
    [string]$BaseUrl = 'http://127.0.0.1',
    [string]$Model,
    [string]$LogPath = 'C:\ProgramData\OpenWebUI\logs\response-metadata-capture.jsonl',
    [int]$Hours = 24
)

$ErrorActionPreference = 'Stop'
$id = 'temporary_response_metadata_capture'
if ($Hours -lt 1 -or $Hours -gt 48) { throw 'Hours must be between 1 and 48.' }
if ($Action -eq 'Install' -and [string]::IsNullOrWhiteSpace($Model)) { throw 'Supply -Model.' }
if ($BaseUrl -notmatch '^http://127\.0\.0\.1(?::\d+)?/?$') { throw 'Use the loopback Open WebUI URL only.' }
$sourcePath = Join-Path $Repo 'diagnostics\response_metadata_filter.py'
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw 'Filter source is missing. Pull the approved commit first.' }
if ($Action -eq 'Install') {
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $LogPath))
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Log directory does not exist: $parent" }
    if (Test-Path -LiteralPath $LogPath) { throw 'Capture log already exists. Inspect it before installing another capture.' }
}

$secret = Read-Host 'Open WebUI administrator API key' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$headers = @{ Authorization = "Bearer $plain" }
$plain = $null
$root = $BaseUrl.TrimEnd('/') + '/api/v1/functions'

try {
    $list = @(Invoke-RestMethod -Uri "$root/list" -Headers $headers -Method Get -TimeoutSec 20)
    $existing = @($list | Where-Object { $_.id -eq $id })
    if ($existing.Count -gt 1) { throw 'Multiple capture functions with the same ID were returned.' }

    if ($Action -eq 'Status') {
        if ($existing.Count -eq 0) { Write-Output 'Capture function is absent.'; return }
        $item = $existing[0]
        [pscustomobject]@{ id = $item.id; active = $item.is_active; global = $item.is_global; log_exists = (Test-Path -LiteralPath $LogPath) }
        return
    }

    if ($Action -eq 'Disable') {
        if ($existing.Count -eq 0) { Write-Output 'Capture function is absent.'; return }
        $item = $existing[0]
        if ($item.is_active) {
            $item = Invoke-RestMethod -Uri "$root/id/$id/toggle" -Headers $headers -Method Post -TimeoutSec 20
            if ($item.is_active) { throw 'Could not disable the capture function.' }
        }
        if ($item.is_global) {
            $item = Invoke-RestMethod -Uri "$root/id/$id/toggle/global" -Headers $headers -Method Post -TimeoutSec 20
            if ($item.is_global) { throw 'Could not remove global scope.' }
        }
        Write-Output 'Capture disabled. The function and log remain for review.'
        return
    }

    if ($existing.Count -gt 0) { throw 'Capture function already exists. Use -Action Status or -Action Disable; no existing function was changed.' }
    $until = [DateTime]::UtcNow.AddHours($Hours).ToString('o')
    $source = [System.IO.File]::ReadAllText($sourcePath)
    $source = $source.Replace('__MODEL_JSON__', (ConvertTo-Json -InputObject $Model -Compress))
    $source = $source.Replace('__LOG_PATH_JSON__', (ConvertTo-Json -InputObject $LogPath -Compress))
    $source = $source.Replace('__UNTIL_JSON__', (ConvertTo-Json -InputObject $until -Compress))
    $payload = @{ id = $id; name = 'Temporary response metadata capture'; content = $source; meta = @{ description = "Metadata only; expires $until" } } | ConvertTo-Json -Depth 6 -Compress
    $created = Invoke-RestMethod -Uri "$root/create" -Headers $headers -Method Post -ContentType 'application/json' -Body $payload -TimeoutSec 30
    if ($created.id -ne $id -or $created.type -ne 'filter' -or $created.is_active -or $created.is_global) {
        throw 'Created function did not have the expected inactive filter state. Inspect it before continuing.'
    }
    $global = Invoke-RestMethod -Uri "$root/id/$id/toggle/global" -Headers $headers -Method Post -TimeoutSec 20
    if (-not $global.is_global) { throw 'Could not set global scope; function remains inactive.' }
    $active = Invoke-RestMethod -Uri "$root/id/$id/toggle" -Headers $headers -Method Post -TimeoutSec 20
    if (-not $active.is_active) { throw 'Could not activate filter. Use -Action Disable to clear global scope.' }
    Write-Output "Capture active until $until UTC. It records metadata for the selected model only at $LogPath."
} finally {
    $headers.Authorization = $null
}
