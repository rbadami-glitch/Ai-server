param(
    [Parameter(Mandatory = $true)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'

$listeners = @(Get-NetTCPConnection -LocalPort 80 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($listeners.Count -ne 1) {
    throw "Expected one process listening on port 80; found $($listeners.Count)."
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0])"
if (-not $process -or -not $process.ExecutablePath) {
    throw 'Could not identify the Python process serving port 80.'
}

$probe = @'
import hashlib
import importlib.metadata
import json
from pathlib import Path

dist = importlib.metadata.distribution("open-webui")
frontend = Path(dist.locate_file("open_webui/frontend/index.html"))
metadata = dist.read_text("METADATA")
direct_url = dist.read_text("direct_url.json")

print(json.dumps({
    "distribution": "open-webui",
    "version": dist.version,
    "frontend_index_sha256": hashlib.sha256(frontend.read_bytes()).hexdigest() if frontend.is_file() else None,
    "metadata_sha256": hashlib.sha256(metadata.encode("utf-8")).hexdigest() if metadata else None,
    "install_origin": "direct-url" if direct_url else "package-index",
}, sort_keys=True))
'@

$result = & $process.ExecutablePath -c $probe 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "The active process's Python could not inspect its Open WebUI package: $result"
}

$details = ($result | Select-Object -Last 1 | ConvertFrom-Json)
if (-not $details.version) { throw 'The package probe returned no version.' }

$report = [ordered]@{
    observed_utc = (Get-Date).ToUniversalTime().ToString('o')
    source = 'python-process-listening-on-port-80'
    distribution = $details.distribution
    version = $details.version
    frontend_index_sha256 = $details.frontend_index_sha256
    metadata_sha256 = $details.metadata_sha256
    install_origin = $details.install_origin
}

$parent = Split-Path -Parent $OutputPath
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Active Open WebUI: $($details.version)"
Write-Host "Sanitized report: $OutputPath"
