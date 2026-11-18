param(
    [Parameter(Mandatory = $true)]
    [string] $WheelPath,
    [switch] $Rollback
)

$ErrorActionPreference = 'Stop'
$expectedHash = '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
$wheel = (Resolve-Path -LiteralPath $WheelPath).Path
$manifestPath = Join-Path (Split-Path -Parent $wheel) 'OpenWebUI-DocModel-Install.json'

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
    if ($process.Name -ieq 'open-webui.exe') {
        $appProcess = $process
        break
    }
    $currentPid = $process.ParentProcessId
}
if (-not $appProcess -or -not $appProcess.ExecutablePath) {
    throw 'Could not trace the port 80 worker to an open-webui.exe environment.'
}

$venvPython = Join-Path (Split-Path -Parent $appProcess.ExecutablePath) 'python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'The active Open WebUI environment has no adjacent python.exe.'
}

$webuiVersion = & $venvPython -c 'import importlib.metadata as m; print(m.version("open-webui"))'
if ($LASTEXITCODE -ne 0 -or -not $webuiVersion) {
    throw 'The identified environment does not contain Open WebUI.'
}
$spacyVersion = & $venvPython -c 'import spacy; print(spacy.__version__)'
if ($LASTEXITCODE -ne 0 -or $spacyVersion -notmatch '^3\.8\.') {
    throw "Expected spaCy 3.8 in the active environment; found '$spacyVersion'."
}

$uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
$uvPath = if ($uvCommand) { $uvCommand.Source } else { 'C:\Program Files\Python311\Scripts\uv.exe' }
if (-not (Test-Path -LiteralPath $uvPath)) { throw 'uv.exe not found.' }

if ($Rollback) {
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'No installation manifest exists.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.state -ne 'installed' -or $manifest.python_path -ne $venvPython) {
        throw 'Manifest does not match the active environment; no package was removed.'
    }
    & $uvPath pip uninstall --python $venvPython 'en-core-web-sm'
    if ($LASTEXITCODE -ne 0) { throw 'Rollback uninstall failed.' }
    $manifest.state = 'rolled-back'
    $manifest.rolled_back_utc = (Get-Date).ToUniversalTime().ToString('o')
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host 'Rolled back the spaCy model package from the active Open WebUI environment.'
    exit 0
}

$actualHash = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "Model wheel SHA256 mismatch: $actualHash" }

$installedModel = & $venvPython -c 'import importlib.metadata as m; print(m.version("en-core-web-sm") if next(m.distributions(name="en-core-web-sm"), None) else "missing")'
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect installed model package.' }
if ($installedModel -ne 'missing') {
    if ($installedModel -ne '3.8.0') { throw "A different spaCy model version is already installed: $installedModel" }
    & $venvPython -c 'import spacy; spacy.load("en_core_web_sm"); print("Model already loads correctly")'
    if ($LASTEXITCODE -ne 0) { throw 'Model is installed but cannot load; stopped without changing it.' }
    exit 0
}

if (Test-Path -LiteralPath $manifestPath) {
    $prior = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($prior.state -eq 'installed') { throw 'An installation manifest already exists; stopped without changing anything.' }
}

Write-Host "Installing verified spaCy model into active Open WebUI $webuiVersion environment..."
& $uvPath pip install --python $venvPython --offline --no-deps $wheel
if ($LASTEXITCODE -ne 0) { throw 'Offline model installation failed.' }

$manifest = [ordered]@{
    state = 'installed'
    installed_utc = (Get-Date).ToUniversalTime().ToString('o')
    python_path = $venvPython
    open_webui_version = $webuiVersion
    model_version = '3.8.0'
    wheel_sha256 = $actualHash
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

& $venvPython -c 'import spacy; nlp = spacy.load("en_core_web_sm"); print("spaCy model ready: " + nlp.meta["version"])'
if ($LASTEXITCODE -ne 0) { throw "Model installed but failed to load. Roll back with -Rollback. Manifest: $manifestPath" }
Write-Host 'Installation verified. Open WebUI was not restarted; upload the real document again.'
Write-Host "Rollback: powershell -NoProfile -ExecutionPolicy Bypass -File '$PSCommandPath' -WheelPath '$wheel' -Rollback"
