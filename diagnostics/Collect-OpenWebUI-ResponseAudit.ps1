param(
    [string]$Repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github',
    [string]$Model = 'curtiss-wright-corporation',
    [string]$Db = 'C:\downloads\open-webui\data\webui.db',
    [string]$TempRoot = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-Response-Budget-Audit-20261111',
    [string]$Branch = 'response-audit-20261111',
    [int]$SinceDays = 7,
    [switch]$CollectOnly,
    [switch]$MetadataOnly,
    [switch]$RunProbe
)

$ErrorActionPreference = 'Stop'
if ($SinceDays -lt 1 -or $SinceDays -gt 60) { throw 'SinceDays must be 1 through 60.' }
$repoFull = [System.IO.Path]::GetFullPath($Repo)
$tempFull = [System.IO.Path]::GetFullPath($TempRoot)
if ($tempFull.StartsWith($repoFull + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Temporary data must stay outside the public Git checkout.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Repo 'diagnostics\response-audit-public.xml') -PathType Leaf)) {
    throw 'Public encryption key is missing. Pull the new commit first.'
}
if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) { throw 'Open WebUI database path does not exist. Supply -Db with the correct path.' }
if (-not (Test-Path -LiteralPath $Repo -PathType Container)) { throw 'Repository path does not exist.' }
if (Test-Path -LiteralPath $TempRoot) { throw 'Temporary audit directory already exists. Inspect it before retrying; nothing was changed.' }
if (-not $CollectOnly) {
    $status = @(git -C $Repo status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) { throw 'Git checkout is not clean.' }
    $existingBranch = @(git -C $Repo branch --list $Branch)
    if ($LASTEXITCODE -ne 0 -or $existingBranch.Count -gt 0) { throw 'Temporary branch already exists.' }
}

$null = New-Item -ItemType Directory -Path $TempRoot
$raw = Join-Path $TempRoot 'raw'
$null = New-Item -ItemType Directory -Path $raw
$manifest = [ordered]@{
    captured_at_utc = [DateTime]::UtcNow.ToString('o')
    since_days = $SinceDays
    model = $Model
    files = @()
    errors = @()
}
$cutoff = (Get-Date).AddDays(-$SinceDays)
$script:totalCaptured = [int64]0

function Copy-LogTail([string]$source, [string]$label, [int64]$maxBytes) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { return }
    try {
        $item = Get-Item -LiteralPath $source
        if ($item.LastWriteTime -lt $cutoff) { return }
        $capture = [Math]::Min([int64]$item.Length, $maxBytes)
        if ($script:totalCaptured + $capture -gt 70MB) {
            $manifest.errors += "Skipped ${label} ($source): 70 MiB collection limit reached."
            return
        }
        $destination = Join-Path $raw $label
        $inputStream = New-Object System.IO.FileStream($item.FullName, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $null = $inputStream.Seek(-$capture, [System.IO.SeekOrigin]::End)
            $outputStream = [System.IO.File]::Create($destination)
            try {
                $buffer = New-Object byte[] 65536
                $remaining = $capture
                while ($remaining -gt 0) {
                    $read = $inputStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                    if ($read -le 0) { break }
                    $outputStream.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
        $actual = (Get-Item -LiteralPath $destination).Length
        $script:totalCaptured += $actual
        $manifest.files += [ordered]@{
            source = $item.FullName
            archive_name = $label
            original_bytes = $item.Length
            captured_bytes = $actual
            tail_truncated = ($capture -lt $item.Length)
            last_write_utc = $item.LastWriteTimeUtc.ToString('o')
        }
    } catch {
        $manifest.errors += "Could not capture $label`: $($_.Exception.Message)"
    }
}

$services = @()
try {
    $services = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object {
        $_.Name -match '(?i)open.?webui|ollama|gemma|cwai' -or $_.DisplayName -match '(?i)open.?webui|ollama|gemma|cwai'
    } | Select-Object Name, DisplayName, State, ProcessId, StartName)
} catch {
    $manifest.errors += "Service inventory failed: $($_.Exception.Message)"
}
$services | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $raw 'services.json') -Encoding UTF8

$candidateLogs = New-Object 'System.Collections.Generic.List[string]'
$serviceMap = @()
foreach ($service in $services) {
    $name = $service.Name
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$name\Parameters"
    $entry = [ordered]@{
        name = $name
        state = $service.State
        service_pid = $service.ProcessId
        application = $null
        stdout = $null
        stderr = $null
        configured_port = $null
        timeout_settings = @{}
    }
    if (Test-Path -LiteralPath $regPath) {
        try {
            $settings = Get-ItemProperty -LiteralPath $regPath -ErrorAction Stop
            $entry.application = $settings.Application
            $entry.stdout = $settings.AppStdout
            $entry.stderr = $settings.AppStderr
            if ([string]$settings.AppParameters -match '(?:--port\s+|PORT=)(\d+)') {
                $entry.configured_port = [int]$Matches[1]
            }
            foreach ($line in @($settings.AppEnvironmentExtra) -split "`0|`n") {
                if ($line -match '^(AIOHTTP_CLIENT_TIMEOUT|AIOHTTP_CLIENT_STREAM_IDLE_TIMEOUT|PORT|WEBUI_PORT)=(\d*)$') {
                    $entry.timeout_settings[$Matches[1]] = $Matches[2]
                }
            }
            foreach ($field in @('AppStdout', 'AppStderr')) {
                $value = $settings.$field
                if ($value -and [System.IO.Path]::IsPathRooted([string]$value)) { $candidateLogs.Add([string]$value) }
            }
        } catch {
            $manifest.errors += "Could not inspect log paths for service $name`: $($_.Exception.Message)"
        }
    }
    $serviceMap += $entry
}
$port80 = @()
try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId, ParentProcessId, Name)
    $byPid = @{}
    foreach ($process in $processes) { $byPid[[int]$process.ProcessId] = $process }
    foreach ($listener in @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction Stop)) {
        $pidValue = [int]$listener.OwningProcess
        $chain = @()
        for ($depth = 0; $depth -lt 20 -and $byPid.ContainsKey($pidValue); $depth++) {
            $process = $byPid[$pidValue]
            $chain += [ordered]@{ pid = $pidValue; name = $process.Name }
            $pidValue = [int]$process.ParentProcessId
        }
        $chainPids = @($chain | ForEach-Object { $_.pid })
        $owners = @($services | Where-Object { $chainPids -contains [int]$_.ProcessId } | Select-Object -ExpandProperty Name)
        $port80 += [ordered]@{
            local_address = $listener.LocalAddress
            process_chain = $chain
            matching_services = $owners
        }
    }
} catch {
    $manifest.errors += "Could not map port 80 owner: $($_.Exception.Message)"
}
([ordered]@{ services = $serviceMap; port_80_listeners = $port80 }) |
    ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $raw 'service-map.json') -Encoding UTF8
foreach ($path in @(
    'C:\ProgramData\OpenWebUI\logs\stdout.log',
    'C:\ProgramData\OpenWebUI\logs\stderr.log',
    'E:\AI\ollama\logs\ollama-service.log',
    'E:\AI\ollama\logs\ollama-service-error.log'
)) { $candidateLogs.Add($path) }
$recorderDir = 'E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage'
if (-not $MetadataOnly) {
    $logIndex = 0
    $recentLogs = @($candidateLogs | Select-Object -Unique | ForEach-Object {
        if (Test-Path -LiteralPath $_ -PathType Leaf) { Get-Item -LiteralPath $_ }
    } | Where-Object { $_.LastWriteTime -ge $cutoff } | Sort-Object LastWriteTime -Descending)
    foreach ($item in $recentLogs) {
        $logIndex++
        Copy-LogTail $item.FullName ('service-log-{0:D2}.txt' -f $logIndex) 15MB
    }
    if (Test-Path -LiteralPath $recorderDir -PathType Container) {
        $recorderIndex = 0
        try {
            foreach ($file in (Get-ChildItem -LiteralPath $recorderDir -File -Filter 'ollama-*.jsonl' -ErrorAction Stop |
                Where-Object { $_.LastWriteTime -ge $cutoff } | Sort-Object LastWriteTime -Descending)) {
                $recorderIndex++
                Copy-LogTail $file.FullName ('recorder-{0:D2}.jsonl' -f $recorderIndex) 5MB
            }
        } catch {
            $manifest.errors += "Could not enumerate recorder files: $($_.Exception.Message)"
        }
    }
}

$auditPath = Join-Path $raw 'settings.json'
$auditError = Join-Path $raw 'settings-error.txt'
try {
    & python -B (Join-Path $Repo 'diagnostics\openwebui_response_budget_audit.py') `
        --db $Db --model $Model --output $auditPath `
        --ollama-url 'http://127.0.0.1:11434' --ollama-url 'http://127.0.0.1:11435' `
        --recorder-dir $recorderDir 2>&1 | Out-File -LiteralPath $auditError -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { $manifest.errors += "Settings audit exited $LASTEXITCODE; see settings-error.txt." }
} catch {
    $manifest.errors += "Settings audit failed: $($_.Exception.Message)"
}
if ($RunProbe) {
    & python -B (Join-Path $Repo 'diagnostics\openwebui_live_response_probe.py') `
        --url 'http://127.0.0.1' --model $Model `
        --output (Join-Path $raw 'live-probe.json')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $raw 'live-probe.json'))) {
        throw 'Live probe did not finish. Temporary files were kept; no upload was attempted.'
    }
}

if (-not $MetadataOnly) {
    try {
        $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $cutoff; Id = 1000,1001 } -MaxEvents 100 -ErrorAction Stop |
            Select-Object TimeCreated, Id, ProviderName, Message)
        $events | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $raw 'application-crashes.json') -Encoding UTF8
    } catch { $manifest.errors += "Application event query failed: $($_.Exception.Message)" }
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $raw 'manifest.json') -Encoding UTF8

$zip = Join-Path $TempRoot 'response-audit.zip'
Compress-Archive -Path (Join-Path $raw '*') -DestinationPath $zip -CompressionLevel Optimal
if ((Get-Item -LiteralPath $zip).Length -gt 90MB) { throw 'Archive exceeds encryption limit. Raw collection was kept for review.' }
$encrypted = Join-Path $TempRoot 'response-audit.zip.enc'
& (Join-Path $Repo 'diagnostics\Protect-DiagnosticArchive.ps1') -Mode Encrypt `
    -InputPath $zip -OutputPath $encrypted `
    -KeyPath (Join-Path $Repo 'diagnostics\response-audit-public.xml')
if (-not (Test-Path -LiteralPath $encrypted -PathType Leaf)) { throw 'Encryption failed; no Git operation was attempted.' }
if ($CollectOnly) {
    Write-Output "Encrypted audit created at $encrypted. Upload was skipped for collection test."
    return
}

$worktree = Join-Path $TempRoot 'upload-worktree'
git -C $Repo worktree add -b $Branch $worktree HEAD
if ($LASTEXITCODE -ne 0) { throw 'Could not create temporary upload worktree.' }
Copy-Item -LiteralPath $encrypted -Destination (Join-Path $worktree 'response-audit.zip.enc')
git -C $worktree add -- response-audit.zip.enc
if ($LASTEXITCODE -ne 0) { throw 'Could not stage encrypted archive.' }
$staged = @(git -C $worktree diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne 'response-audit.zip.enc') { throw 'Unexpected staged file. Push was stopped.' }
git -C $worktree commit -m 'Add encrypted temporary response audit archive'
if ($LASTEXITCODE -ne 0) { throw 'Could not commit encrypted archive.' }
git -C $worktree push -u origin $Branch
if ($LASTEXITCODE -ne 0) { throw "Push failed. Temporary worktree and encrypted archive remain at $TempRoot for retry." }
Write-Output "Encrypted audit uploaded to branch $Branch. Raw files remain at $TempRoot until analysis is complete."
