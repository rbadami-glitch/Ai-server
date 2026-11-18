param(
    [ValidateSet('Apply', 'VerifyRestart', 'Rollback')]
    [Parameter(Mandatory = $true)] [string] $Mode,
    [string] $TempRoot = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-OpenWebUI-Durability-20261119'
)

$ErrorActionPreference = 'Stop'
$serviceName = 'OpenWebUIService'
$manifestPath = Join-Path $TempRoot 'stage.json'
$backupPath = Join-Path $TempRoot 'start-openwebui.ps1.backup'
$candidatePath = Join-Path $TempRoot 'start-openwebui.ps1.candidate'
$regKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\OpenWebUIService\Parameters'
$expectedLauncher = 'E:\apps\open-webui\start-openwebui.ps1'
$expectedWheel = 'E:\apps\open-webui\model-wheels\en_core_web_sm-3.8.0-py3-none-any.whl'
$expectedWheelHash = '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'

function FileHash([string] $Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Save-Manifest {
    $script:manifest | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

function Get-Listener {
    $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -gt 1) { throw 'More than one port 80 owner was found.' }
    if ($owners.Count -eq 0) { return $null }
    # The listener may exit between the TCP and process queries during shutdown.
    # Treat that as a transient absence; the caller polls again before proceeding.
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])" -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    return [pscustomobject]@{
        Pid = [int]$proc.ProcessId
        CreatedUtc = $proc.CreationDate.ToUniversalTime().ToString('o')
    }
}

function Assert-ServiceOwnsListener($Listener) {
    $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if (-not $svc -or $svc.State -ne 'Running' -or -not $svc.ProcessId) {
        throw 'OpenWebUIService is not running.'
    }
    $other = Get-CimInstance Win32_Service -Filter "Name='CWAI-OpenWebUI'"
    if ($other -and $other.State -ne 'Stopped') {
        throw 'CWAI-OpenWebUI is not stopped.'
    }
    $duplicate = Get-CimInstance Win32_Service -Filter "Name='OpenWebUI'"
    if ($duplicate -and
        ($duplicate.State -ne 'Stopped' -or $duplicate.StartMode -eq 'Auto')) {
        throw 'Duplicate OpenWebUI service must stay stopped and set to Manual.'
    }
    $current = $Listener.Pid
    for ($depth = 0; $depth -lt 16 -and $current; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current"
        if (-not $proc) { break }
        if ($proc.ProcessId -eq $svc.ProcessId) { return [int]$svc.ProcessId }
        $current = $proc.ParentProcessId
    }
    throw 'Port 80 listener is not owned by the OpenWebUI service tree.'
}

function Stop-ServiceAndClearListener($Known) {
    $svc = Get-Service -Name $serviceName
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $serviceName -ErrorAction Stop
        (Get-Service -Name $serviceName).WaitForStatus(
            'Stopped', [TimeSpan]::FromMinutes(2))
    }
    $leftover = Get-Listener
    if ($leftover) {
        $matched = @($Known | Where-Object {
            $_.Pid -eq $leftover.Pid -and $_.CreatedUtc -eq $leftover.CreatedUtc
        })
        if ($matched.Count -ne 1) {
            throw "Unknown process $($leftover.Pid) still owns port 80; stopped without killing it."
        }
        Write-Host "Clearing confirmed old worker PID $($leftover.Pid)..."
        Stop-Process -Id $leftover.Pid -Force -ErrorAction Stop
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Listener)) { return }
        Start-Sleep -Seconds 1
    }
    throw 'Port 80 did not become free after stopping OpenWebUI.'
}

function Wait-HealthyListener {
    # A cold uvx launch can take longer than three minutes, including rollback.
    for ($i = 0; $i -lt 72; $i++) {
        $listener = Get-Listener
        if ($listener) {
            try {
                $health = Invoke-RestMethod 'http://127.0.0.1/health' -TimeoutSec 3
                if ($health.status -eq $true) {
                    $servicePid = Assert-ServiceOwnsListener $listener
                    return [pscustomobject]@{
                        Pid = $listener.Pid
                        CreatedUtc = $listener.CreatedUtc
                        ServicePid = $servicePid
                    }
                }
            } catch {
                # The service may still be starting. Retry until the deadline.
            }
        }
        Start-Sleep -Seconds 5
    }
    throw 'OpenWebUI did not return a healthy port 80 response in six minutes.'
}

function Assert-ActiveModelLoads($Listener) {
    $current = $Listener.Pid
    $appExe = $null
    for ($depth = 0; $depth -lt 16 -and $current; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current"
        if (-not $proc) { break }
        if ($proc.Name -ieq 'open-webui.exe') { $appExe = $proc.ExecutablePath; break }
        $current = $proc.ParentProcessId
    }
    if (-not $appExe) { throw 'Active OpenWebUI executable not found.' }
    $scripts = Split-Path -Parent $appExe
    $site = Join-Path (Split-Path -Parent $scripts) 'Lib\site-packages'
    $python = Join-Path $scripts 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Active OpenWebUI Python executable not found.'
    }
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $site
        $output = & $python -c "import spacy, en_core_web_sm, importlib.metadata as m; en_core_web_sm.load(); print(spacy.__version__, en_core_web_sm.__version__, m.version('open-webui'))"
        if ($LASTEXITCODE -ne 0) { throw 'spaCy model failed to load in the active archive.' }
        $expectedLine = "^3\.8\.\d+\s+3\.8\.0\s+$([regex]::Escape($manifest.open_webui_version))$"
        if (-not @($output | Where-Object { $_ -match $expectedLine }).Count) {
            throw "Active model or OpenWebUI version differs from preflight: $($output -join ' ')"
        }
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    Write-Host "ActiveModel=$($output -join ' ')"
}

function Get-KnownListeners {
    $known = @([pscustomobject]@{
        Pid = [int]$manifest.listener_pid
        CreatedUtc = [string]$manifest.listener_created_utc
    })
    if ($manifest.new_listener_pid) {
        $known += [pscustomobject]@{
            Pid = [int]$manifest.new_listener_pid
            CreatedUtc = [string]$manifest.new_listener_created_utc
        }
    }
    if ($manifest.verified_listener_pid) {
        $known += [pscustomobject]@{
            Pid = [int]$manifest.verified_listener_pid
            CreatedUtc = [string]$manifest.verified_listener_created_utc
        }
    }
    return $known
}

function Set-RegistryOriginal {
    if ($manifest.registry_stop_skip_exists) {
        New-ItemProperty -LiteralPath $regKey -Name AppStopMethodSkip -PropertyType DWord -Value ([int]$manifest.registry_stop_skip) -Force | Out-Null
    } else {
        Remove-ItemProperty -LiteralPath $regKey -Name AppStopMethodSkip -ErrorAction SilentlyContinue
    }
    if ($manifest.registry_kill_tree_exists) {
        New-ItemProperty -LiteralPath $regKey -Name AppKillProcessTree -PropertyType DWord -Value ([int]$manifest.registry_kill_tree) -Force | Out-Null
    } else {
        Remove-ItemProperty -LiteralPath $regKey -Name AppKillProcessTree -ErrorAction SilentlyContinue
    }
    $actual = Get-ItemProperty -LiteralPath $regKey
    $names = $actual.PSObject.Properties.Name
    $skipExists = $names -contains 'AppStopMethodSkip'
    $treeExists = $names -contains 'AppKillProcessTree'
    if ($skipExists -ne $manifest.registry_stop_skip_exists -or
        $treeExists -ne $manifest.registry_kill_tree_exists -or
        ($skipExists -and [int]$actual.AppStopMethodSkip -ne [int]$manifest.registry_stop_skip) -or
        ($treeExists -and [int]$actual.AppKillProcessTree -ne [int]$manifest.registry_kill_tree)) {
        throw 'Original NSSM shutdown settings were not restored.'
    }
}

function Restore-Original {
    $currentHash = FileHash $expectedLauncher
    if ($currentHash -ne $manifest.launcher_original_sha256 -and
        $currentHash -ne $manifest.launcher_candidate_sha256) {
        throw 'Launcher changed outside this rollout; automatic rollback refused.'
    }
    $known = @(Get-KnownListeners)
    $currentListener = Get-Listener
    if ($currentListener -and
        -not @($known | Where-Object {
            $_.Pid -eq $currentListener.Pid -and
            $_.CreatedUtc -eq $currentListener.CreatedUtc
        }).Count) {
        Assert-ServiceOwnsListener $currentListener | Out-Null
        $known += $currentListener
    }
    Stop-ServiceAndClearListener $known
    Copy-Item -LiteralPath $backupPath -Destination $expectedLauncher -Force
    if ((FileHash $expectedLauncher) -ne $manifest.launcher_original_sha256) {
        throw 'Original launcher could not be restored byte for byte.'
    }
    Set-RegistryOriginal
    Start-Service -Name $serviceName -ErrorAction Stop
    $recovered = Wait-HealthyListener
    $manifest.state = 'rolled_back'
    $manifest.rollback_utc = (Get-Date).ToUniversalTime().ToString('o')
    Save-Manifest
    Write-Host "RollbackHealthy=True Port80Pid=$($recovered.Pid)"
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell window.'
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Stage manifest not found.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.launcher -ine $expectedLauncher -or
    $manifest.stable_wheel -ine $expectedWheel -or
    $manifest.wheel_sha256 -ine $expectedWheelHash -or
    (FileHash $backupPath) -ne $manifest.launcher_original_sha256 -or
    (FileHash $candidatePath) -ne $manifest.launcher_candidate_sha256 -or
    (FileHash $expectedWheel) -ne $expectedWheelHash) {
    throw 'Stage files or verified wheel changed; no action taken.'
}

if ($Mode -eq 'Rollback') {
    if ($manifest.state -notin @('activating', 'applied_pending_verification', 'verified', 'needs_manual_recovery')) {
        throw "Rollback is not applicable to manifest state '$($manifest.state)'."
    }
    Restore-Original
    exit 0
}

if ($Mode -eq 'Apply') {
    if ($manifest.state -ne 'preflight_ok') {
        throw 'The staged uvx/model preflight must pass before activation.'
    }
    if ((FileHash $expectedLauncher) -ne $manifest.launcher_original_sha256) {
        throw 'Live launcher changed after staging; no action taken.'
    }
    $params = Get-ItemProperty -LiteralPath $regKey
    $names = $params.PSObject.Properties.Name
    $skipExists = $names -contains 'AppStopMethodSkip'
    $treeExists = $names -contains 'AppKillProcessTree'
    if ($skipExists -ne $manifest.registry_stop_skip_exists -or
        $treeExists -ne $manifest.registry_kill_tree_exists -or
        ($skipExists -and [int]$params.AppStopMethodSkip -ne [int]$manifest.registry_stop_skip) -or
        ($treeExists -and [int]$params.AppKillProcessTree -ne [int]$manifest.registry_kill_tree)) {
        throw 'NSSM shutdown settings changed after staging; no action taken.'
    }
    $old = Get-Listener
    if (-not $old -or $old.Pid -ne [int]$manifest.listener_pid -or
        $old.CreatedUtc -ne [string]$manifest.listener_created_utc -or
        (Assert-ServiceOwnsListener $old) -ne [int]$manifest.service_pid) {
        throw 'Live worker changed after staging; no action taken.'
    }
    $manifest.state = 'activating'
    Save-Manifest
    try {
        Copy-Item -LiteralPath $candidatePath -Destination $expectedLauncher -Force
        if ((FileHash $expectedLauncher) -ne $manifest.launcher_candidate_sha256) {
            throw 'Candidate launcher copy failed.'
        }
        New-ItemProperty -LiteralPath $regKey -Name AppStopMethodSkip -PropertyType DWord -Value 0 -Force | Out-Null
        New-ItemProperty -LiteralPath $regKey -Name AppKillProcessTree -PropertyType DWord -Value 1 -Force | Out-Null
        $updated = Get-ItemProperty -LiteralPath $regKey
        if ([int]$updated.AppStopMethodSkip -ne 0 -or
            [int]$updated.AppKillProcessTree -ne 1) {
            throw 'NSSM shutdown settings did not save as 0/1.'
        }
        Stop-ServiceAndClearListener @($old)
        Start-Service -Name $serviceName -ErrorAction Stop
        $new = Wait-HealthyListener
        if ($new.Pid -eq $old.Pid -and $new.CreatedUtc -eq $old.CreatedUtc) {
            throw 'Old worker still serves port 80 after activation.'
        }
        Assert-ActiveModelLoads $new
        $manifest.new_listener_pid = $new.Pid
        $manifest.new_listener_created_utc = $new.CreatedUtc
        $manifest.state = 'applied_pending_verification'
        Save-Manifest
        Write-Host "Activated=True OldPid=$($old.Pid) NewPid=$($new.Pid) Health=True"
        Write-Host 'Run -Mode VerifyRestart to confirm future restarts replace the worker.'
    } catch {
        $problem = $_.Exception.Message
        Write-Warning "Activation failed: $problem"
        try {
            Restore-Original
        } catch {
            $manifest.state = 'needs_manual_recovery'
            Save-Manifest
            throw "Activation failed: $problem. Automatic rollback also failed: $($_.Exception.Message)"
        }
        throw "Activation failed: $problem. Original launcher and service were restored."
    }
    exit 0
}

if ($manifest.state -ne 'applied_pending_verification') {
    throw "VerifyRestart requires applied_pending_verification, found '$($manifest.state)'."
}
if ((FileHash $expectedLauncher) -ne $manifest.launcher_candidate_sha256) {
    throw 'Launcher changed after activation; restart verification stopped.'
}
$params = Get-ItemProperty -LiteralPath $regKey
if ([int]$params.AppStopMethodSkip -ne 0 -or [int]$params.AppKillProcessTree -ne 1) {
    throw 'NSSM shutdown settings are no longer 0/1; restart verification stopped.'
}
$before = Get-Listener
if (-not $before -or $before.Pid -ne [int]$manifest.new_listener_pid -or
    $before.CreatedUtc -ne [string]$manifest.new_listener_created_utc) {
    throw 'Worker changed after activation; restart verification stopped.'
}
Assert-ServiceOwnsListener $before | Out-Null
try {
    Write-Host "Restarting OpenWebUI once to verify worker replacement; old PID $($before.Pid)..."
    Restart-Service -Name $serviceName -ErrorAction Stop
    $after = Wait-HealthyListener
    if ($after.Pid -eq $before.Pid -and $after.CreatedUtc -eq $before.CreatedUtc) {
        throw 'Restart kept the old port 80 worker.'
    }
    Assert-ActiveModelLoads $after
    $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($before.Pid)"
    if ($oldProcess -and
        $oldProcess.CreationDate.ToUniversalTime().ToString('o') -eq $before.CreatedUtc) {
        throw 'Previous OpenWebUI worker survived the restart.'
    }
    $manifest.verified_listener_pid = $after.Pid
    $manifest.verified_listener_created_utc = $after.CreatedUtc
    $manifest.state = 'verified'
    $manifest.verified_utc = (Get-Date).ToUniversalTime().ToString('o')
    Save-Manifest
    Write-Host "RestartVerified=True PreviousPid=$($before.Pid) CurrentPid=$($after.Pid) Health=True"
} catch {
    $problem = $_.Exception.Message
    Write-Warning "Restart verification failed: $problem"
    try {
        Restore-Original
    } catch {
        $manifest.state = 'needs_manual_recovery'
        Save-Manifest
        throw "Restart verification failed: $problem. Automatic rollback also failed: $($_.Exception.Message)"
    }
    throw "Restart verification failed: $problem. Original launcher and service were restored."
}
