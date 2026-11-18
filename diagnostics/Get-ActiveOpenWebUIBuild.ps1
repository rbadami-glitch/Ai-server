param(
    [Parameter(Mandatory = $true)]
    [string] $OutputPath,
    [string] $DatabasePath = 'C:\downloads\open-webui\data\webui.db'
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
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    unstructured_version = importlib.metadata.version("unstructured")
except importlib.metadata.PackageNotFoundError:
    unstructured_version = None

dist = importlib.metadata.distribution("open-webui")
frontend = Path(dist.locate_file("open_webui/frontend/index.html"))
metadata = dist.read_text("METADATA")
direct_url = dist.read_text("direct_url.json")

doc_probe = {"status": "not-checked"}
db_path = Path(sys.argv[1])
if db_path.is_file():
    try:
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as conn:
            records = conn.execute(
                "SELECT data, meta, created_at FROM file "
                "WHERE lower(filename) LIKE '%.doc' ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        for data_json, meta_json, created_at in records:
            meta = json.loads(meta_json or "{}")
            if int(meta.get("size") or 0) < 1024:
                continue
            data = json.loads(data_json or "{}")
            error = str(data.get("error") or "").lower()
            if "unstructured" in error and ("install" in error or "module" in error):
                category = "missing-unstructured"
            elif "soffice" in error or "libreoffice" in error:
                category = "office-conversion"
            elif "no module named" in error or "importerror" in error:
                category = "missing-python-dependency"
            elif "empty" in error or "no content" in error:
                category = "empty-extraction"
            elif "permission" in error or "access denied" in error:
                category = "permission"
            elif error:
                category = "other-processing-error"
            else:
                category = None
            doc_probe = {
                "status": data.get("status") or "not-recorded",
                "content_characters": len(str(data.get("content") or "")),
                "error_category": category,
                "content_type": meta.get("content_type"),
                "created_utc": datetime.fromtimestamp(created_at, timezone.utc).isoformat() if created_at else None,
            }
            break
        else:
            doc_probe = {"status": "no-legacy-doc-over-1kb-found"}
    except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        doc_probe = {"status": "probe-failed", "error_type": type(exc).__name__}
else:
    doc_probe = {"status": "database-not-found"}

print(json.dumps({
    "distribution": "open-webui",
    "version": dist.version,
    "frontend_index_sha256": hashlib.sha256(frontend.read_bytes()).hexdigest() if frontend.is_file() else None,
    "metadata_sha256": hashlib.sha256(metadata.encode("utf-8")).hexdigest() if metadata else None,
    "install_origin": "direct-url" if direct_url else "package-index",
    "unstructured_version": unstructured_version,
    "libreoffice_installed": Path("C:/Program Files/LibreOffice/program/soffice.exe").is_file(),
    "latest_legacy_doc": doc_probe,
}, sort_keys=True))
'@

$result = & $process.ExecutablePath -c $probe $DatabasePath 2>&1
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
    unstructured_version = $details.unstructured_version
    libreoffice_installed = $details.libreoffice_installed
    latest_legacy_doc = $details.latest_legacy_doc
}

$parent = Split-Path -Parent $OutputPath
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Active Open WebUI: $($details.version)"
Write-Host "Frontend SHA256: $($details.frontend_index_sha256); package metadata SHA256: $($details.metadata_sha256)"
Write-Host "Unstructured version: $($details.unstructured_version); LibreOffice installed: $($details.libreoffice_installed)"
Write-Host "Latest real .doc: status=$($details.latest_legacy_doc.status); content characters=$($details.latest_legacy_doc.content_characters); error category=$($details.latest_legacy_doc.error_category)"
Write-Host "Private report saved on server: $OutputPath"
