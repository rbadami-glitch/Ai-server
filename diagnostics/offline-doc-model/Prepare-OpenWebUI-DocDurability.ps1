param(
    [ValidateSet('Inspect', 'Stage', 'Preflight')]
    [string] $Mode = 'Inspect',
    [string] $TempRoot = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-OpenWebUI-Durability-20261119',
    [switch] $AllowNetwork
)

$ErrorActionPreference = 'Stop'
$serviceName = 'OpenWebUIService'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$stableWheel = 'E:\apps\open-webui\model-wheels\en_core_web_sm-3.8.0-py3-none-any.whl'
$sourceWheel = Join-Path $PSScriptRoot 'en_core_web_sm-3.8.0-py3-none-any.whl'
$expectedWheelHash = '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
$regKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\OpenWebUIService\Parameters'
$manifestPath = Join-Path $TempRoot 'stage.json'
$backupPath = Join-Path $TempRoot 'start-openwebui.ps1.backup'
$candidatePath = Join-Path $TempRoot 'start-openwebui.ps1.candidate'

function FileHash([string] $Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-LiveState {
    $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if (-not $svc -or $svc.State -ne 'Running' -or -not $svc.ProcessId) {
        throw 'OpenWebUIService is not running.'
    }
    $other = Get-CimInstance Win32_Service -Filter "Name='CWAI-OpenWebUI'"
    if ($other -and $other.State -ne 'Stopped') {
        throw 'CWAI-OpenWebUI is not stopped; service ownership needs review.'
    }
    $duplicate = Get-CimInstance Win32_Service -Filter "Name='OpenWebUI'"
    if ($duplicate -and $duplicate.State -ne 'Stopped') {
        throw 'Duplicate OpenWebUI service is not stopped; service ownership needs review.'
    }
    $params = Get-ItemProperty -LiteralPath $regKey
    if ($params.AppParameters -notmatch [regex]::Escape($launcher)) {
        throw 'OpenWebUI NSSM service does not point to the expected launcher.'
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 80 -State Listen |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listeners.Count -ne 1) {
        throw "Expected one port 80 owner; found $($listeners.Count)."
    }
    $current = $listeners[0]
    $ancestry = @()
    $appExe = $null
    $belongs = $false
    for ($depth = 0; $depth -lt 16 -and $current; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current"
        if (-not $proc) { break }
        $ancestry += $proc
        if ($proc.Name -ieq 'open-webui.exe') { $appExe = $proc.ExecutablePath }
        if ($proc.ProcessId -eq $svc.ProcessId) { $belongs = $true; break }
        $current = $proc.ParentProcessId
    }
    if (-not $belongs -or -not $appExe) {
        throw 'Port 80 is not owned by the expected OpenWebUI service tree.'
    }
    $scripts = Split-Path -Parent $appExe
    $archiveV0 = Split-Path -Parent (Split-Path -Parent $scripts)
    if ((Split-Path -Leaf $archiveV0) -ne 'archive-v0') {
        throw 'Unexpected uvx archive layout behind port 80.'
    }
    $cacheDir = Split-Path -Parent $archiveV0
    $site = Join-Path (Split-Path -Parent $scripts) 'Lib\site-packages'
    $python = Join-Path $scripts 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Active archive Python executable not found.'
    }
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $site
        $webuiVersion = & $python -c "import importlib.metadata as m; print(m.version('open-webui'))"
        if ($LASTEXITCODE -ne 0 -or -not $webuiVersion) {
            throw 'Cannot read current OpenWebUI version.'
        }
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    $names = $params.PSObject.Properties.Name
    $registryItem = Get-Item -LiteralPath $regKey
    foreach ($name in @('AppStopMethodSkip', 'AppKillProcessTree')) {
        if ($names -contains $name -and
            $registryItem.GetValueKind($name).ToString() -ne 'DWord') {
            throw "$name is not a REG_DWORD; stopped without changing it."
        }
    }
    return [pscustomobject]@{
        ServicePid = [int]$svc.ProcessId
        ListenerPid = [int]$listeners[0]
        ListenerCreatedUtc = $ancestry[0].CreationDate.ToUniversalTime().ToString('o')
        AppExe = $appExe
        CacheDir = $cacheDir
        WebUIVersion = $webuiVersion
        StopSkipExists = $names -contains 'AppStopMethodSkip'
        StopSkip = if ($names -contains 'AppStopMethodSkip') { [int]$params.AppStopMethodSkip } else { $null }
        KillTreeExists = $names -contains 'AppKillProcessTree'
        KillTree = if ($names -contains 'AppKillProcessTree') { [int]$params.AppKillProcessTree } else { $null }
    }
}

$state = Get-LiveState
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw 'Active launcher not found.'
}
if (-not (Test-Path -LiteralPath $sourceWheel -PathType Leaf) -or
    (FileHash $sourceWheel) -ne $expectedWheelHash) {
    throw 'Source wheel missing or SHA256 mismatch.'
}
$raw = [System.IO.File]::ReadAllText($launcher)
$launchLines = @([regex]::Matches($raw, '(?m)^.*uvx\.exe.*open-webui@latest serve.*$'))
if ($launchLines.Count -ne 1) { throw 'Expected one uvx OpenWebUI launch line.' }
$oldLine = $launchLines[0].Value
$oldSegment = "--with 'unstructured[doc]'"
if (-not $oldLine.Contains($oldSegment) -or
    $oldLine -notmatch '--python 3\.11' -or
    $oldLine -notmatch '--host 0\.0\.0\.0 --port 80' -or
    $oldLine -match 'en_core_web_sm|spacy>=') {
    throw 'Launcher line is different from the reviewed OpenWebUI configuration.'
}
$newSegment = "$oldSegment --with 'spacy>=3.8,<3.9' --with '$stableWheel'"
$candidate = $raw.Replace($oldLine, $oldLine.Replace($oldSegment, $newSegment))
$uvx = 'C:\Program Files\Python311\Scripts\uvx.exe'
if (-not (Test-Path -LiteralPath $uvx -PathType Leaf)) { throw 'uvx.exe not found.' }

if ($Mode -eq 'Inspect') {
    Write-Host "ServicePid=$($state.ServicePid) Port80Pid=$($state.ListenerPid)"
    Write-Host "OpenWebUIVersion=$($state.WebUIVersion)"
    Write-Host "StopMethodSkip=$($state.StopSkip) KillProcessTree=$($state.KillTree)"
    Write-Host "Launcher=$launcher"
    Write-Host "LauncherSHA256=$(FileHash $launcher)"
    Write-Host "WheelSHA256=$(FileHash $sourceWheel)"
    Write-Host "StableWheelExists=$(Test-Path -LiteralPath $stableWheel)"
    Write-Host "ProposedLaunchLine=$($oldLine.Replace($oldSegment, $newSegment))"
    Write-Host 'InspectOnly=True'
    exit 0
}

if ($Mode -eq 'Stage') {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run Stage from an Administrator PowerShell window.'
    }
    if (Test-Path -LiteralPath $TempRoot) {
        throw 'Temporary backup directory already exists; inspect it before retrying.'
    }
    New-Item -ItemType Directory -Path $TempRoot | Out-Null
    Copy-Item -LiteralPath $launcher -Destination $backupPath
    $stableDir = Split-Path -Parent $stableWheel
    New-Item -ItemType Directory -Path $stableDir -Force | Out-Null
    if (Test-Path -LiteralPath $stableWheel) {
        if ((FileHash $stableWheel) -ne $expectedWheelHash) {
            throw 'Stable wheel exists but has a different hash; nothing replaced.'
        }
        $wheelCreated = $false
    } else {
        $wheelTemp = Join-Path $stableDir 'en_core_web_sm-3.8.0-py3-none-any.whl.staging'
        if (Test-Path -LiteralPath $wheelTemp) { throw 'Wheel staging file already exists.' }
        Copy-Item -LiteralPath $sourceWheel -Destination $wheelTemp
        if ((FileHash $wheelTemp) -ne $expectedWheelHash) { throw 'Copied wheel hash mismatch.' }
        Move-Item -LiteralPath $wheelTemp -Destination $stableWheel
        $wheelCreated = $true
    }
    [System.IO.File]::WriteAllText($candidatePath, $candidate, (New-Object System.Text.UTF8Encoding($false)))
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($candidatePath, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw "Candidate launcher has $($errors.Count) syntax errors." }
    $manifest = [ordered]@{
        state = 'staged'
        created_utc = (Get-Date).ToUniversalTime().ToString('o')
        preflight_utc = $null
        verified_utc = $null
        rollback_utc = $null
        launcher = $launcher
        launcher_original_sha256 = FileHash $backupPath
        launcher_candidate_sha256 = FileHash $candidatePath
        stable_wheel = $stableWheel
        wheel_sha256 = $expectedWheelHash
        wheel_created = $wheelCreated
        registry_stop_skip_exists = $state.StopSkipExists
        registry_stop_skip = $state.StopSkip
        registry_kill_tree_exists = $state.KillTreeExists
        registry_kill_tree = $state.KillTree
        service_pid = $state.ServicePid
        listener_pid = $state.ListenerPid
        listener_created_utc = $state.ListenerCreatedUtc
        new_listener_pid = $null
        new_listener_created_utc = $null
        verified_listener_pid = $null
        verified_listener_created_utc = $null
        cache_dir = $state.CacheDir
        open_webui_version = $state.WebUIVersion
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "StagedBackup=$TempRoot"
    Write-Host "StableWheel=$stableWheel"
    Write-Host 'LiveLauncherChanged=False ServiceChanged=False'
    exit 0
}

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Stage manifest not found. Run -Mode Stage first.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.state -notin @('staged', 'preflight_ok') -or
    $manifest.launcher -ine $launcher -or
    $manifest.stable_wheel -ine $stableWheel -or
    $manifest.wheel_sha256 -ine $expectedWheelHash -or
    (FileHash $launcher) -ne $manifest.launcher_original_sha256 -or
    (FileHash $backupPath) -ne $manifest.launcher_original_sha256 -or
    (FileHash $candidatePath) -ne $manifest.launcher_candidate_sha256 -or
    (FileHash $stableWheel) -ne $expectedWheelHash) {
    throw 'Stage files or live launcher changed; preflight stopped.'
}
$uvArgs = @('--python', '3.11',
    '--with', 'unstructured[doc]',
    '--with', 'spacy>=3.8,<3.9',
    '--with', $stableWheel,
    '--from', 'open-webui@latest',
    'python', '-c',
    "import spacy, en_core_web_sm, importlib.metadata as m; en_core_web_sm.load(); print(spacy.__version__, en_core_web_sm.__version__, m.version('open-webui'))")
if (-not $AllowNetwork) { $uvArgs = @('--offline') + $uvArgs }
$previousCache = $env:UV_CACHE_DIR
try {
    $env:UV_CACHE_DIR = $manifest.cache_dir
    Write-Host 'Checking the proposed uvx environment without starting OpenWebUI...'
    $output = & $uvx @uvArgs
    if ($LASTEXITCODE -ne 0) { throw "uvx preflight failed with exit code $LASTEXITCODE." }
    $expectedLine = "^3\.8\.\d+\s+3\.8\.0\s+$([regex]::Escape($manifest.open_webui_version))$"
    if (-not @($output | Where-Object { $_ -match $expectedLine }).Count) {
        throw "Model or OpenWebUI version differs from the active service: $($output -join ' ')"
    }
} finally {
    $env:UV_CACHE_DIR = $previousCache
}
$manifest.state = 'preflight_ok'
$manifest.preflight_utc = (Get-Date).ToUniversalTime().ToString('o')
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "ModelPreflight=$($output -join ' ')"
Write-Host 'ReadyForActivation=True LiveLauncherChanged=False ServiceChanged=False'
