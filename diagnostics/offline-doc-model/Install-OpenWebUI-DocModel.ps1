param(
    [Parameter(Mandatory = $true)] [string] $WheelPath,
    [switch] $InspectOnly,
    [switch] $Rollback
)

$ErrorActionPreference = 'Stop'
$expectedHash = '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
$wheel = (Resolve-Path -LiteralPath $WheelPath).Path
$bundle = Split-Path -Parent $wheel
$manifestPath = Join-Path $bundle 'OpenWebUI-DocModel-Install.json'

$listenerPids = @(Get-NetTCPConnection -LocalPort 80 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($listenerPids.Count -ne 1) {
    throw "Expected one port 80 listener process; found $($listenerPids.Count)."
}
$currentPid = $listenerPids[0]
$appProcess = $null
for ($depth = 0; $depth -lt 16 -and $currentPid; $depth++) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$currentPid"
    if (-not $process) { break }
    if ($process.Name -ieq 'open-webui.exe') { $appProcess = $process; break }
    $currentPid = $process.ParentProcessId
}
if (-not $appProcess -or -not $appProcess.ExecutablePath) {
    throw 'Could not trace port 80 to an open-webui.exe process.'
}
$scripts = Split-Path -Parent $appProcess.ExecutablePath
if ((Split-Path -Leaf $scripts) -ine 'Scripts') {
    throw 'Unexpected Open WebUI executable layout.'
}
$archive = Split-Path -Parent $scripts
$site = Join-Path $archive 'Lib\site-packages'
$python = Join-Path $scripts 'python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $site 'open_webui') -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $site 'spacy') -PathType Container)) {
    throw 'Active archive does not have the expected Open WebUI, spaCy, and Python layout.'
}
$modelDir = Join-Path $site 'en_core_web_sm'
$metadataDir = Join-Path $site 'en_core_web_sm-3.8.0.dist-info'
$modelPresent = Test-Path -LiteralPath $modelDir
$metadataPresent = Test-Path -LiteralPath $metadataDir
$otherMetadata = @(Get-ChildItem -LiteralPath $site -Directory -Filter 'en_core_web_sm-*.dist-info')
if ($otherMetadata.Count -gt 0 -and -not $metadataPresent) {
    throw 'Different model version found. Nothing changed.'
}
if ($modelPresent -ne $metadataPresent -and -not $Rollback) {
    throw 'Partial model installation found. Nothing changed.'
}
$actualHash = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "Wheel hash mismatch: $actualHash" }

Write-Host "Port80Pid=$($listenerPids[0])"
Write-Host "OpenWebUIExe=$($appProcess.ExecutablePath)"
Write-Host "SitePackages=$site"
Write-Host "ModelFolder=$modelPresent"
Write-Host "ModelMetadata=$metadataPresent"

# The adjacent Python is not activated for this uvx archive. Supply its
# package directory only during the read-only verification processes.
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $site
    $spacyVersion = & $python -c "import spacy; print(spacy.__version__)"
    if ($LASTEXITCODE -ne 0 -or $spacyVersion -notmatch '^3\.8\.') {
        throw "Expected spaCy 3.8; found '$spacyVersion'."
    }
    Write-Host "SpacyVersion=$spacyVersion"
    if ($modelPresent -and -not $Rollback) {
        $loadedVersion = & $python -c "import en_core_web_sm; en_core_web_sm.load(); print(en_core_web_sm.__version__)"
        if ($LASTEXITCODE -ne 0 -or $loadedVersion -ne '3.8.0') {
            throw 'Existing spaCy model could not load. Nothing changed.'
        }
        Write-Host 'ModelAlreadyLoads=True'
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
if ($InspectOnly) { Write-Host 'Inspection complete; nothing changed.'; exit 0 }

if ($Rollback) {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'No installation manifest exists. Nothing removed.'
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.state -notin @('installed', 'copying') -or $manifest.site_packages -ine $site -or
        $manifest.model_folder -ine $modelDir -or $manifest.metadata_folder -ine $metadataDir -or
        $manifest.wheel_sha256 -ine $expectedHash) {
        throw 'Manifest does not match this active archive. Nothing removed.'
    }
    $parentFull = [System.IO.Path]::GetFullPath($site).TrimEnd('\') + '\'
    foreach ($target in @($modelDir, $metadataDir)) {
        $targetFull = [System.IO.Path]::GetFullPath($target)
        if (-not $targetFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Rollback path outside the active package directory: $targetFull"
        }
    }
    if ($modelPresent) { Remove-Item -LiteralPath $modelDir -Recurse -Force }
    if ($metadataPresent) { Remove-Item -LiteralPath $metadataDir -Recurse -Force }
    $manifest.state = 'rolled-back'
    $manifest.rolled_back_utc = (Get-Date).ToUniversalTime().ToString('o')
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host 'Removed only the installed model directories.'
    exit 0
}

if ($modelPresent) { Write-Host 'Model already loads; nothing changed.'; exit 0 }
if (Test-Path -LiteralPath $manifestPath) {
    throw 'Previous installation manifest exists. Inspect it before retrying.'
}
$uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
$uvPath = if ($uvCommand) { $uvCommand.Source } else { 'C:\Program Files\Python311\Scripts\uv.exe' }
if (-not (Test-Path -LiteralPath $uvPath -PathType Leaf)) { throw 'uv.exe not found.' }
$staging = Join-Path $bundle 'model-install-staging'
if (Test-Path -LiteralPath $staging) {
    throw 'Model staging directory already exists. Inspect it before retrying.'
}
New-Item -ItemType Directory -Path $staging | Out-Null
& $uvPath pip install --target $staging --offline --no-deps $wheel
if ($LASTEXITCODE -ne 0) { throw 'Offline staging failed. Active archive was not changed.' }
$stagedNames = @(Get-ChildItem -LiteralPath $staging | Select-Object -ExpandProperty Name)
$expectedNames = @('en_core_web_sm', 'en_core_web_sm-3.8.0.dist-info')
if (@($stagedNames | Where-Object { $_ -notin $expectedNames }).Count -gt 0 -or
    @($expectedNames | Where-Object { $_ -notin $stagedNames }).Count -gt 0) {
    throw "Unexpected staged files: $($stagedNames -join ', ')"
}
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = "$staging;$site"
    $loadedVersion = & $python -c "import en_core_web_sm; en_core_web_sm.load(); print(en_core_web_sm.__version__)"
    if ($LASTEXITCODE -ne 0 -or $loadedVersion -ne '3.8.0') {
        throw 'Staged model did not load. Active archive was not changed.'
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
if ((Test-Path -LiteralPath $modelDir) -or (Test-Path -LiteralPath $metadataDir)) {
    throw 'Model appeared during staging. Nothing copied.'
}
$stillListening = @(Get-NetTCPConnection -LocalPort 80 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($stillListening.Count -ne 1 -or $stillListening[0] -ne $listenerPids[0]) {
    throw 'Port 80 worker changed during staging. Nothing copied.'
}
$manifest = [ordered]@{
    state = 'copying'
    installed_utc = (Get-Date).ToUniversalTime().ToString('o')
    site_packages = $site
    model_folder = $modelDir
    metadata_folder = $metadataDir
    wheel_sha256 = $actualHash
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $staging 'en_core_web_sm') -Destination $modelDir -Recurse -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $staging 'en_core_web_sm-3.8.0.dist-info') -Destination $metadataDir -Recurse -ErrorAction Stop
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $site
    $loadedVersion = & $python -c "import en_core_web_sm; en_core_web_sm.load(); print(en_core_web_sm.__version__)"
    if ($LASTEXITCODE -ne 0 -or $loadedVersion -ne '3.8.0') {
        throw 'Copied model failed to load. Use -Rollback; manifest state is copying.'
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
$manifest.state = 'installed'
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host 'Model installed. Open WebUI was not restarted.'
Write-Host "Rollback: powershell -NoProfile -ExecutionPolicy Bypass -File '$PSCommandPath' -WheelPath '$wheel' -Rollback"
