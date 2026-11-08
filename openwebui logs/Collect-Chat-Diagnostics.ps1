param(
    [string]$LogDirectory = 'C:\ProgramData\OpenWebUI\logs'
)

$ErrorActionPreference = 'Stop'

# Chat #16 ran on 2026-11-05 17:55-17:57 UTC (23:25-23:27 IST).
# Match both clock formats because the log timezone has not been established.
$incidentPattern = '2026-11-05[ T](?:17:(?:[45][0-9])|18:0[0-9]|23:(?:1[5-9]|2[0-9]|3[0-5]))'
$certificate = Join-Path $PSScriptRoot 'review-public.cer'

function Open-SharedLogReader([string]$Path) {
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    return [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8, $true)
}

if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) {
    throw "Open WebUI log directory was not found: $LogDirectory"
}
if (-not (Test-Path -LiteralPath $certificate -PathType Leaf)) {
    throw 'The review public certificate is missing.'
}

$files = @(Get-ChildItem -LiteralPath $LogDirectory -File |
    Where-Object { $_.Name -match '(?i)(?:stdout|stderr).*\.log(?:\..*)?$' } |
    Sort-Object Name)
if ($files.Count -eq 0) { throw 'No stdout/stderr log files were found.' }

$report = New-Object System.Text.StringBuilder
[void]$report.AppendLine('Open WebUI chat #16 log evidence')
[void]$report.AppendLine('Task ID: 8715e7d8-a51a-4ac5-84c0-f6566257a458')
[void]$report.AppendLine('Incident: 2026-11-05 17:55-17:57 UTC / 23:25-23:27 IST')
[void]$report.AppendLine('Collected at UTC: ' + [DateTime]::UtcNow.ToString('o'))
[void]$report.AppendLine('Source: ' + $LogDirectory)
[void]$report.AppendLine('Only incident-window matches and 5 lines before / 60 lines after each match are included.')
[void]$report.AppendLine('No match means the logs did not expose this incident in the searched time formats.')
[void]$report.AppendLine()

foreach ($file in $files) {
    [void]$report.AppendLine(('FILE {0} | bytes={1} | modified UTC={2:o}' -f $file.Name, $file.Length, $file.LastWriteTimeUtc))
    if ($file.Name -ieq 'stderr.log') {
        [void]$report.AppendLine('Full current stderr.log follows because its lines have no matching incident timestamps.')
        $reader = Open-SharedLogReader $file.FullName
        try {
            $number = 0
            while (($line = $reader.ReadLine()) -ne $null) {
                $number++
                [void]$report.AppendLine(('{0}: {1}' -f $number, $line))
                if ($report.Length -gt 4MB) {
                    throw 'Log excerpts exceed 4 MiB. No encrypted file was written.'
                }
            }
        } finally {
            $reader.Dispose()
        }
        [void]$report.AppendLine()
        continue
    }
    $hits = New-Object 'System.Collections.Generic.List[int]'
    $reader = Open-SharedLogReader $file.FullName
    try {
        $number = 0
        while (($line = $reader.ReadLine()) -ne $null) {
            $number++
            if ($line -match $incidentPattern) { $hits.Add($number) }
        }
    } finally {
        $reader.Dispose()
    }
    [void]$report.AppendLine('Incident timestamp matches: ' + $hits.Count)
    if ($hits.Count -gt 0) {
        $wanted = New-Object 'System.Collections.Generic.HashSet[int]'
        foreach ($hit in $hits) {
            for ($number = [Math]::Max(1, $hit - 5); $number -le $hit + 60; $number++) {
                [void]$wanted.Add($number)
            }
        }
        $number = 0
        $reader = Open-SharedLogReader $file.FullName
        try {
            while (($line = $reader.ReadLine()) -ne $null) {
                $number++
                if ($wanted.Contains($number)) {
                    [void]$report.AppendLine(('{0}: {1}' -f $number, $line))
                    if ($report.Length -gt 4MB) {
                        throw 'Incident excerpts exceed 4 MiB. No encrypted file was written; narrow the time window.'
                    }
                }
            }
        } finally {
            $reader.Dispose()
        }
    }
    [void]$report.AppendLine()
}

$encrypted = Join-Path $PSScriptRoot ('chat-diagnostics-' + [guid]::NewGuid().ToString('N') + '.p7m')
$cms = Protect-CmsMessage -To $certificate -Content $report.ToString()
[System.IO.File]::WriteAllText($encrypted, $cms, [System.Text.Encoding]::ASCII)
Write-Host ('Encrypted diagnostic file: ' + $encrypted)
Write-Output $encrypted
