param(
    [ValidateSet('Plan', 'Apply', 'Rollback')]
    [string]$Mode = 'Plan',
    [ValidateSet('CWAI-Gemma4-Ollama', 'CWAI-Ollama')]
    [string]$ServiceName = 'CWAI-Gemma4-Ollama',
    [string]$BackupFile
)

# Run on ZUSEVRAI01 in elevated Windows PowerShell. Apply restarts only the
# selected Ollama service. No Windows reboot, model load, or client URL change.
$ErrorActionPreference = 'Stop'
$expectedHost = 'ZUSEVRAI01'
$install = 'C:\Users\v_skumard\AppData\Local\Programs\Ollama'
$originalExe = Join-Path $install 'ollama.exe'
$candidateExe = Join-Path $install 'ollama-direct-usage.exe'
$runtimeDll = Join-Path $install 'libwinpthread-1.dll'
$usageDir = 'E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage'
$backupDir = 'E:\AI\ollama\OpenWebUI-Monitoring\Recorder-Staging\service-backups'
$port = if ($ServiceName -eq 'CWAI-Gemma4-Ollama') { 11435 } else { 11434 }
$url = "http://127.0.0.1:$port"
$expectedOriginalHash = 'AD4019D2CB32F959C086F8963DD0C30ABFA7AB220F11C9BA57E4149CACB7A0A7'
$expectedCandidateHash = 'D15A734F98260D366752B361031DC6C998AAA58CE16433B4ED6ABDFCF136B8B1'
$expectedDllHash = 'F5042E8E3D21EDCE20C1BC99445F551B89212403164E9388D2C6F48D6D6BCB78'
$subkey = "SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
$regOptions = [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames

function Assert-Hash([string]$path, [string]$expected) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $path" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
    if ($actual -ne $expected) { throw "Unexpected SHA-256 for $path" }
}

function Wait-ServiceState([string]$name, [System.ServiceProcess.ServiceControllerStatus]$state) {
    $controller = Get-Service -Name $name
    $controller.WaitForStatus($state, [timespan]::FromSeconds(45))
}

function Assert-Healthy([string]$name, [int]$listenerPort, [string]$baseUrl) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $service = Get-CimInstance Win32_Service -Filter "Name='$name'"
            $listener = Get-NetTCPConnection -LocalPort $listenerPort -State Listen -ErrorAction Stop |
                Where-Object { $_.LocalAddress -eq '127.0.0.1' -or $_.LocalAddress -eq '0.0.0.0' } |
                Select-Object -First 1
            if ($service.State -eq 'Running' -and $listener) {
                $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
                $version = (Invoke-RestMethod -Uri "$baseUrl/api/version" -TimeoutSec 3).version
                if ($process.ParentProcessId -eq $service.ProcessId -and $version -eq '0.30.6') {
                    return
                }
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "$name did not own port $listenerPort and answer Ollama 0.30.6 within 60 seconds."
}

function Assert-RecorderEvent([int]$listenerPort, [string]$baseUrl, [string]$directory) {
    $probeModel = "cw-recorder-probe-$(Get-Date -Format 'yyyyMMddHHmmss')"
    $body = @{ model = $probeModel; prompt = ''; stream = $false } | ConvertTo-Json -Compress
    try {
        Invoke-WebRequest -Uri "$baseUrl/api/generate" -Method Post -ContentType 'application/json' `
            -Body $body -UseBasicParsing -TimeoutSec 10 | Out-Null
    } catch {
        # A missing synthetic model should produce an HTTP error, without loading a model.
        if (-not $_.Exception.Response) { throw }
    }
    $day = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
    $recordFile = Join-Path $directory "ollama-$listenerPort-$day.jsonl"
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-Path -LiteralPath $recordFile -PathType Leaf) {
            $events = @(Get-Content -LiteralPath $recordFile -Tail 30 |
                ForEach-Object { $_ | ConvertFrom-Json })
            $match = $events | Where-Object { $_.model -eq $probeModel -and $_.route -eq '/api/generate' } |
                Select-Object -First 1
            if ($match) {
                Write-Output "Metadata probe recorded on port $listenerPort with HTTP status $($match.status)."
                return
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "No metadata probe appeared in $recordFile within 10 seconds."
}

function Restore-Settings($key, $backup) {
    $appKind = [System.Enum]::Parse([Microsoft.Win32.RegistryValueKind], [string]$backup.ApplicationKind)
    $key.SetValue('Application', [string]$backup.Application, $appKind)
    if ($backup.ExtraExists) {
        $extraKind = [System.Enum]::Parse([Microsoft.Win32.RegistryValueKind], [string]$backup.ExtraKind)
        $key.SetValue('AppEnvironmentExtra', [string[]]@($backup.ExtraValues), $extraKind)
    } else {
        $key.DeleteValue('AppEnvironmentExtra', $false)
    }
}

if ((hostname) -ine $expectedHost) { throw "Run this only on $expectedHost." }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($Mode -ne 'Plan' -and -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Apply and Rollback require Administrator PowerShell.'
}

$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service -or $service.StartName -ne 'LocalSystem' -or $service.PathName -notmatch 'nssm\.exe') {
    throw 'Unexpected service account or wrapper; no change made.'
}
$key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($subkey, $Mode -ne 'Plan')
if (-not $key) { throw "Missing registry key: HKLM\$subkey" }
try {
    $names = @($key.GetValueNames())
    if ($names -notcontains 'Application' -or $names -notcontains 'AppEnvironmentExtra') {
        throw 'Expected NSSM Application and AppEnvironmentExtra values are missing.'
    }
    if ($key.GetValueKind('AppEnvironmentExtra') -ne [Microsoft.Win32.RegistryValueKind]::MultiString) {
        throw 'Unexpected AppEnvironmentExtra registry type.'
    }
    $app = [string]$key.GetValue('Application', $null, $regOptions)
    $appKind = $key.GetValueKind('Application')
    $extra = [string[]]$key.GetValue('AppEnvironmentExtra', $null, $regOptions)
    $directory = [string]$key.GetValue('AppDirectory', $null, $regOptions)
    $parameters = [string]$key.GetValue('AppParameters', $null, $regOptions)
    if ($directory -ine $install -or $parameters -ne 'serve') {
        throw 'Unexpected NSSM directory or parameters; no change made.'
    }
    if ($Mode -eq 'Apply' -and ($extra | Where-Object { $_ -match '^OLLAMA_DIRECT_USAGE_DIR=' })) {
        throw 'Service already has an OLLAMA_DIRECT_USAGE_DIR entry; review before changing it.'
    }
    Assert-Hash $originalExe $expectedOriginalHash
    if ($Mode -ne 'Rollback') {
        Assert-Hash $candidateExe $expectedCandidateHash
        Assert-Hash $runtimeDll $expectedDllHash
    }
    if (-not (Test-Path -LiteralPath $usageDir -PathType Container) -or
        -not (Test-Path -LiteralPath $backupDir -PathType Container)) {
        throw 'Private recorder or backup directory is missing.'
    }

    if ($Mode -eq 'Plan') {
        [pscustomobject]@{
            Service = $ServiceName
            State = $service.State
            Port = $port
            CurrentExecutable = $app
            ExpectedOriginal = ($app -ieq $originalExe)
            ExtraVariableNames = ((@($extra) | ForEach-Object { ($_ -split '=', 2)[0] }) -join ', ')
            CandidateHashVerified = $true
            RuntimeHashVerified = $true
            RecorderFolder = $usageDir
        } | Format-List
        return
    }

    if ($Mode -eq 'Rollback') {
        if (-not $BackupFile -or -not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
            throw 'Rollback requires an existing -BackupFile.'
        }
        $backup = Get-Content -LiteralPath $BackupFile -Raw | ConvertFrom-Json
        if ($backup.ServiceName -ne $ServiceName -or $backup.Application -ine $originalExe -or
            $app -ine $candidateExe -or -not ($extra -contains "OLLAMA_DIRECT_USAGE_DIR=$usageDir")) {
            throw 'Backup or current service settings do not match this recorder cutover.'
        }
        Stop-Service -Name $ServiceName -ErrorAction Stop
        Wait-ServiceState $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
        Restore-Settings $key $backup
        Start-Service -Name $ServiceName -ErrorAction Stop
        Assert-Healthy $ServiceName $port $url
        Write-Output "$ServiceName restored to the signed executable; version and port verified."
        return
    }

    if ($app -ine $originalExe -or $service.State -ne 'Running') {
        throw 'Service is not running the expected signed executable; no change made.'
    }
    $backup = [pscustomobject]@{
        ServiceName = $ServiceName
        Application = $app
        ApplicationKind = [string]$appKind
        ExtraExists = $true
        ExtraKind = 'MultiString'
        ExtraValues = @($extra)
    }
    $backupPath = Join-Path $backupDir ("$ServiceName-settings-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    if (Test-Path -LiteralPath $backupPath) { throw 'Backup path already exists.' }
    $backup | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $backupPath -Encoding UTF8
    Write-Output "Private rollback settings saved to $backupPath"

    $changed = $false
    $stopped = $false
    try {
        Stop-Service -Name $ServiceName -ErrorAction Stop
        Wait-ServiceState $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
        $stopped = $true
        $changed = $true
        $key.SetValue('Application', $candidateExe, $appKind)
        $key.SetValue('AppEnvironmentExtra', [string[]]@($extra + "OLLAMA_DIRECT_USAGE_DIR=$usageDir"),
            [Microsoft.Win32.RegistryValueKind]::MultiString)
        Start-Service -Name $ServiceName -ErrorAction Stop
        Assert-Healthy $ServiceName $port $url
        Assert-RecorderEvent $port $url $usageDir
        Write-Output "$ServiceName now runs the recorder on unchanged port $port."
        Write-Output "Rollback command: & '$PSCommandPath' -Mode Rollback -ServiceName '$ServiceName' -BackupFile '$backupPath'"
    } catch {
        $failure = $_.Exception.Message
        $rollbackFailure = $null
        try {
            $current = Get-Service -Name $ServiceName
            if (($changed -or $stopped) -and $current.Status -ne 'Stopped') {
                Stop-Service -Name $ServiceName -Force -ErrorAction Stop
                Wait-ServiceState $ServiceName ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
            }
            if ($changed) { Restore-Settings $key $backup }
            $current = Get-Service -Name $ServiceName
            if ($current.Status -eq 'Stopped') {
                Start-Service -Name $ServiceName -ErrorAction Stop
            }
            Assert-Healthy $ServiceName $port $url
        } catch {
            $rollbackFailure = $_.Exception.Message
        }
        if ($rollbackFailure) {
            throw "Cutover failed: $failure. Automatic rollback failed: $rollbackFailure. Check private backup $backupPath."
        }
        throw "Cutover failed; original service settings restored: $failure"
    }
} finally {
    $key.Close()
}
