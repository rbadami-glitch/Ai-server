param(
    [ValidateSet('Plan', 'Apply', 'Rollback')]
    [string]$Mode = 'Plan',
    [ValidateSet('CWAI-Ollama', 'CWAI-Gemma4-Ollama')]
    [Parameter(Mandatory)]
    [string]$ServiceName
)

# Switch one Ollama service at a time. Keep the latency recorder and its
# environment intact for rollback; never restart the Windows OS.
$ErrorActionPreference = 'Stop'
$install = 'C:\Users\v_skumard\AppData\Local\Programs\Ollama'
$currentExe = Join-Path $install 'ollama-direct-usage-latency-7a6bed6.exe'
$candidateExe = Join-Path $install 'ollama-direct-usage-gemma-213d4bd.exe'
$dll = Join-Path $install 'libwinpthread-1.dll'
$zip = Join-Path $PSScriptRoot 'artifacts\ollama-direct-usage-213d4bd-windows-amd64.zip'
$usageDir = 'E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage'
$port = if ($ServiceName -eq 'CWAI-Ollama') { 11434 } else { 11435 }
$registryPath = "SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
$currentHash = '619AAF894B23884F1A862F44FC0B5DE68A4BDB486AF258E2F9C63850261884F5'
$candidateHash = 'C993DBA4E8194BC7BC0D3E7E6E34E256E9D0F78BE2BFFAA3C82BFAAAE7374B4F'
$dllHash = 'F5042E8E3D21EDCE20C1BC99445F551B89212403164E9388D2C6F48D6D6BCB78'
$zipHash = '6902E504C581FE6348F0E9DE2D5DB682908F44B70F5B065F4609A7D473F0EB72'

function Assert-Hash([string]$path, [string]$expected) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $path" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $expected) {
        throw "Unexpected SHA-256: $path"
    }
}

function Wait-State([string]$name, [System.ServiceProcess.ServiceControllerStatus]$state) {
    (Get-Service -Name $name).WaitForStatus($state, [timespan]::FromSeconds(45))
}

function Assert-Healthy([string]$name, [string]$executable, [int]$listenerPort) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $service = Get-CimInstance Win32_Service -Filter "Name='$name'"
            if ($service.State -eq 'Running') {
                foreach ($listener in @(Get-NetTCPConnection -LocalPort $listenerPort -State Listen -ErrorAction Stop)) {
                    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
                    if ($process.ParentProcessId -eq $service.ProcessId -and
                        $process.ExecutablePath -ieq $executable) {
                        $version = (Invoke-RestMethod -Uri "http://127.0.0.1:$listenerPort/api/version" -TimeoutSec 3).version
                        if ($version -eq '0.30.6') { return }
                    }
                }
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "$name did not serve Ollama 0.30.6 on port $listenerPort as $executable."
}

function Ensure-Candidate {
    if (Test-Path -LiteralPath $candidateExe -PathType Leaf) {
        Assert-Hash $candidateExe $candidateHash
        return
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $created = $false
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName -ceq 'ollama-direct-usage.exe' } | Select-Object -First 1
        if (-not $entry -or $entry.Length -ne 35374080) { throw 'Unexpected executable in artifact.' }
        $inputStream = $entry.Open()
        try {
            $outputStream = [System.IO.File]::Open($candidateExe, [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            $created = $true
            try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
        Assert-Hash $candidateExe $candidateHash
    } catch {
        if ($created -and (Test-Path -LiteralPath $candidateExe -PathType Leaf)) {
            Remove-Item -LiteralPath $candidateExe -ErrorAction SilentlyContinue
        }
        throw
    } finally { $archive.Dispose() }
}

function Assert-RoutingRecord([int]$listenerPort) {
    # Invalid top_logprobs is rejected before model loading or inference.
    $before = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 2
    $body = @{ model = 'cw-recorder-probe-' + [guid]::NewGuid().ToString('N');
               prompt = ''; stream = $false; top_logprobs = -1 } | ConvertTo-Json -Compress
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$listenerPort/api/generate" -Method Post `
            -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 10 | Out-Null
        throw 'The invalid-options probe unexpectedly succeeded.'
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 400) { throw }
    }
    $day = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
    $file = Join-Path $usageDir "ollama-$listenerPort-$day.jsonl"
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            foreach ($line in @(Get-Content -LiteralPath $file -Tail 50)) {
                try { $event = $line | ConvertFrom-Json } catch { continue }
                if ($event.at -ge $before -and $event.route -eq '/api/generate' -and
                    $event.model -eq 'gemma4:31b' -and [int]$event.status -eq 400 -and
                    $null -ne $event.duration_ms) {
                    Write-Output "Port $listenerPort rewrote a chat model to gemma4:31b; HTTP 400 was expected."
                    return
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "No Gemma routing probe appeared in $file within 10 seconds."
}

if ((hostname) -ine 'ZUSEVRAI01') { throw 'Run only on ZUSEVRAI01.' }
if ($Mode -ne 'Plan') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Apply and Rollback require Administrator PowerShell.'
    }
}
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service -or $service.StartName -ne 'LocalSystem' -or
    $service.PathName -notmatch 'nssm\.exe' -or $service.State -ne 'Running') {
    throw 'Unexpected service account, wrapper, or state; no change made.'
}
$key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($registryPath, $Mode -ne 'Plan')
if (-not $key) { throw "Missing registry key: HKLM\$registryPath" }
try {
    $names = @($key.GetValueNames())
    if ($names -notcontains 'Application' -or $names -notcontains 'AppDirectory' -or
        $names -notcontains 'AppParameters' -or $names -notcontains 'AppEnvironmentExtra') {
        throw 'Expected NSSM values are missing.'
    }
    $app = [string]$key.GetValue('Application')
    $appKind = $key.GetValueKind('Application')
    $directory = [string]$key.GetValue('AppDirectory')
    $parameters = [string]$key.GetValue('AppParameters')
    $extra = [string[]]$key.GetValue('AppEnvironmentExtra')
    if ($directory -ine $install -or $parameters -ne 'serve' -or
        $key.GetValueKind('AppEnvironmentExtra') -ne [Microsoft.Win32.RegistryValueKind]::MultiString -or
        @($extra | Where-Object { $_ -eq "OLLAMA_DIRECT_USAGE_DIR=$usageDir" }).Count -ne 1 -or
        @($extra | Where-Object { $_ -like 'OLLAMA_FORCE_CHAT_MODEL=*' }).Count) {
        throw 'Unexpected NSSM configuration; no change made.'
    }
    Assert-Hash $currentExe $currentHash
    Assert-Hash $dll $dllHash
    Assert-Hash $zip $zipHash
    if (-not (Test-Path -LiteralPath $usageDir -PathType Container)) { throw 'Recorder directory is missing.' }
    if ($Mode -eq 'Plan') {
        if ($app -ine $currentExe -and $app -ine $candidateExe) { throw 'Unexpected service executable.' }
        if (Test-Path -LiteralPath $candidateExe -PathType Leaf) { Assert-Hash $candidateExe $candidateHash }
        [pscustomobject]@{
            Service = $ServiceName; State = $service.State; Port = $port
            CurrentExecutable = $app; Candidate = $candidateExe
            CandidateRouting = 'Built in; no environment change'
            ArtifactHashVerified = $true; RuntimeDllHashVerified = $true
        } | Format-List
        return
    }
    if ($Mode -eq 'Apply') {
        if ($app -ine $currentExe) { throw 'Expected latency recorder was not found.' }
        Assert-Healthy $ServiceName $currentExe $port
        Ensure-Candidate
        $version = & $candidateExe --version
        if ($LASTEXITCODE -ne 0 -or ($version -join ' ') -notmatch '0\.30\.6') {
            throw 'Candidate executable did not report Ollama 0.30.6.'
        }
        $changed = $false
        try {
            Stop-Service -Name $ServiceName -ErrorAction Stop
            Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
            $changed = $true
            $key.SetValue('Application', $candidateExe, $appKind)
            Start-Service -Name $ServiceName -ErrorAction Stop
            Assert-Healthy $ServiceName $candidateExe $port
            Assert-RoutingRecord $port
            Write-Output "$ServiceName now routes chat and generation to gemma4:31b on port $port."
            Write-Output "Rollback: & '$PSCommandPath' -Mode Rollback -ServiceName '$ServiceName'"
        } catch {
            $failure = $_.Exception.Message
            try {
                if ((Get-Service -Name $ServiceName).Status -ne 'Stopped') {
                    Stop-Service -Name $ServiceName -Force -ErrorAction Stop
                    Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
                }
                if ($changed) {
                    $key.SetValue('Application', $currentExe, $appKind)
                }
                Start-Service -Name $ServiceName -ErrorAction Stop
                Assert-Healthy $ServiceName $currentExe $port
            } catch {
                throw "Routing change failed: $failure. Automatic rollback also failed: $($_.Exception.Message)"
            }
            throw "Routing change failed; latency recorder restored: $failure"
        }
        return
    }
    if ($app -ine $candidateExe) {
        throw 'Service is not running the expected Gemma routing candidate.'
    }
    Assert-Hash $candidateExe $candidateHash
    $changed = $false
    try {
        Stop-Service -Name $ServiceName -ErrorAction Stop
        Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
        $changed = $true
        $key.SetValue('Application', $currentExe, $appKind)
        Start-Service -Name $ServiceName -ErrorAction Stop
        Assert-Healthy $ServiceName $currentExe $port
        Write-Output "$ServiceName restored to the latency recorder on port $port."
    } catch {
        $failure = $_.Exception.Message
        try {
            if ((Get-Service -Name $ServiceName).Status -ne 'Stopped') {
                Stop-Service -Name $ServiceName -Force -ErrorAction Stop
                Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
            }
            if ($changed) {
                $key.SetValue('Application', $candidateExe, $appKind)
            }
            Start-Service -Name $ServiceName -ErrorAction Stop
            Assert-Healthy $ServiceName $candidateExe $port
        } catch {
            throw "Rollback failed: $failure. Restoring candidate also failed: $($_.Exception.Message)"
        }
        throw "Rollback failed; Gemma routing candidate restored: $failure"
    }
} finally {
    $key.Close()
}
