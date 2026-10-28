param(
    [ValidateSet('Plan', 'Apply', 'Rollback')]
    [string]$Mode = 'Plan',
    [ValidateSet('CWAI-Ollama', 'CWAI-Gemma4-Ollama')]
    [Parameter(Mandatory)]
    [string]$ServiceName
)

# Upgrade one existing recorder service at a time. Only its NSSM Application
# value changes; the original executable, DLL, environment and data remain.
$ErrorActionPreference = 'Stop'
$install = 'C:\Users\v_skumard\AppData\Local\Programs\Ollama'
$oldExe = Join-Path $install 'ollama-direct-usage.exe'
$newExe = Join-Path $install 'ollama-direct-usage-latency-7a6bed6.exe'
$dll = Join-Path $install 'libwinpthread-1.dll'
$zip = Join-Path $PSScriptRoot 'artifacts\ollama-direct-usage-7a6bed6-windows-amd64.zip'
$usageDir = 'E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage'
$port = if ($ServiceName -eq 'CWAI-Ollama') { 11434 } else { 11435 }
$keyPath = "SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
$oldHash = 'D15A734F98260D366752B361031DC6C998AAA58CE16433B4ED6ABDFCF136B8B1'
$newHash = '619AAF894B23884F1A862F44FC0B5DE68A4BDB486AF258E2F9C63850261884F5'
$dllHash = 'F5042E8E3D21EDCE20C1BC99445F551B89212403164E9388D2C6F48D6D6BCB78'
$zipHash = '421DD04EE99F638AC41A142EC8A71EC039E37BE5E17D365E2B3E23F32418394A'

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
                $listeners = @(Get-NetTCPConnection -LocalPort $listenerPort -State Listen -ErrorAction Stop)
                foreach ($listener in $listeners) {
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

function Assert-LatencyRecord([int]$listenerPort) {
    $probeModel = 'cw-recorder-probe-' + [guid]::NewGuid().ToString('N')
    $body = @{ model = $probeModel; prompt = ''; stream = $false } | ConvertTo-Json -Compress
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$listenerPort/api/generate" -Method Post -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 10 | Out-Null
        throw 'The missing-model probe unexpectedly succeeded.'
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw }
    }
    $day = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
    $file = Join-Path $usageDir "ollama-$listenerPort-$day.jsonl"
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            foreach ($line in @(Get-Content -LiteralPath $file -Tail 50)) {
                try { $event = $line | ConvertFrom-Json } catch { continue }
                if ($event.model -eq $probeModel -and $event.route -eq '/api/generate' -and
                    [int]$event.status -eq 404 -and
                    $event.PSObject.Properties.Name -contains 'duration_ms' -and
                    $null -ne $event.duration_ms -and [long]$event.duration_ms -ge 0) {
                    Write-Output "Port $listenerPort recorded HTTP 404 and latency $($event.duration_ms) ms."
                    return
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "No latency probe appeared in $file within 10 seconds."
}

function Ensure-Candidate {
    if (Test-Path -LiteralPath $newExe -PathType Leaf) {
        Assert-Hash $newExe $newHash
        return
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $created = $false
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName -ceq 'ollama-direct-usage.exe' } | Select-Object -First 1
        if (-not $entry -or $entry.Length -ne 35371520) { throw 'Unexpected executable in artifact.' }
        $inputStream = $entry.Open()
        try {
            $outputStream = [System.IO.File]::Open($newExe, [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            $created = $true
            try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
        Assert-Hash $newExe $newHash
    } catch {
        if ($created -and (Test-Path -LiteralPath $newExe -PathType Leaf)) {
            Remove-Item -LiteralPath $newExe -ErrorAction SilentlyContinue
        }
        throw
    } finally { $archive.Dispose() }
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
$key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($keyPath, $Mode -ne 'Plan')
if (-not $key) { throw "Missing registry key: HKLM\$keyPath" }
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
        @($extra | Where-Object { $_ -eq "OLLAMA_DIRECT_USAGE_DIR=$usageDir" }).Count -ne 1) {
        throw 'Unexpected NSSM configuration; no change made.'
    }
    Assert-Hash $oldExe $oldHash
    Assert-Hash $dll $dllHash
    Assert-Hash $zip $zipHash
    if (-not (Test-Path -LiteralPath $usageDir -PathType Container)) { throw 'Recorder directory is missing.' }
    if ($Mode -eq 'Plan') {
        if ($app -ine $oldExe -and $app -ine $newExe) { throw 'Unexpected service executable.' }
        if (Test-Path -LiteralPath $newExe -PathType Leaf) { Assert-Hash $newExe $newHash }
        [pscustomobject]@{
            Service = $ServiceName; State = $service.State; Port = $port
            CurrentExecutable = $app; Candidate = $newExe
            ArtifactHashVerified = $true; RuntimeDllHashVerified = $true
        } | Format-List
        return
    }
    if ($Mode -eq 'Apply') {
        if ($app -ine $oldExe) { throw 'Service is not running the expected old recorder.' }
        Assert-Healthy $ServiceName $oldExe $port
        Ensure-Candidate
        $candidateVersion = & $newExe --version
        if ($LASTEXITCODE -ne 0 -or ($candidateVersion -join ' ') -notmatch '0\.30\.6') {
            throw 'Candidate executable did not report Ollama 0.30.6.'
        }
        $changed = $false
        try {
            Stop-Service -Name $ServiceName -ErrorAction Stop
            Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
            $key.SetValue('Application', $newExe, $appKind)
            $changed = $true
            Start-Service -Name $ServiceName -ErrorAction Stop
            Assert-Healthy $ServiceName $newExe $port
            Assert-LatencyRecord $port
            Write-Output "$ServiceName now records latency on port $port."
            Write-Output "Rollback: & '$PSCommandPath' -Mode Rollback -ServiceName '$ServiceName'"
        } catch {
            $failure = $_.Exception.Message
            try {
                if ((Get-Service -Name $ServiceName).Status -ne 'Stopped') {
                    Stop-Service -Name $ServiceName -Force -ErrorAction Stop
                    Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
                }
                if ($changed) { $key.SetValue('Application', $oldExe, $appKind) }
                Start-Service -Name $ServiceName -ErrorAction Stop
                Assert-Healthy $ServiceName $oldExe $port
            } catch {
                throw "Upgrade failed: $failure. Automatic rollback also failed: $($_.Exception.Message)"
            }
            throw "Upgrade failed; old recorder restored: $failure"
        }
        return
    }
    if ($app -ine $newExe) { throw 'Service is not running the latency recorder.' }
    Assert-Hash $newExe $newHash
    Stop-Service -Name $ServiceName -ErrorAction Stop
    Wait-State $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
    $key.SetValue('Application', $oldExe, $appKind)
    Start-Service -Name $ServiceName -ErrorAction Stop
    Assert-Healthy $ServiceName $oldExe $port
    Write-Output "$ServiceName restored to the old recorder on port $port."
} finally {
    $key.Close()
}
