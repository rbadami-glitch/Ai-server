param(
    [Parameter(Mandatory=$true)][string]$AssetsZip,
    [Parameter(Mandatory=$true)][string]$TargetRoot,
    [string]$PythonExe = 'C:\Program Files\Python311\python.exe',
    [string]$SofficeExe = 'C:\Program Files\LibreOffice\program\soffice.com'
)

$ErrorActionPreference = 'Stop'
$expectedSha256 = 'A8E23A6254D5B2B1148D6D9DB43BA408563D3CF1EEA7CF2E61967D6BCB21C9B3'
$assets = (Resolve-Path -LiteralPath $AssetsZip).Path
$root = [System.IO.Path]::GetFullPath($TargetRoot)
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

if (-not [System.IO.Path]::IsPathRooted($TargetRoot)) { throw 'TargetRoot must be an absolute path.' }
if (Test-Path -LiteralPath $root) { throw 'TargetRoot already exists. Choose a new empty path; nothing was changed.' }
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python 3.11 executable was not found.' }
if (-not (Test-Path -LiteralPath $SofficeExe -PathType Leaf)) { throw 'LibreOffice soffice.com was not found.' }
if ((Get-FileHash -LiteralPath $assets -Algorithm SHA256).Hash -ne $expectedSha256) {
    throw 'Offline asset ZIP hash does not match the locally verified artifact.'
}

New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null
Expand-Archive -LiteralPath $assets -DestinationPath $root -Force
New-Item -ItemType Directory -Path (Join-Path $root 'diagnostics') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $root 'openwebui-tools') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'openwebui-tools\document_conversion_worker.py') -Destination (Join-Path $root 'openwebui-tools\document_conversion_worker.py')
Copy-Item -LiteralPath (Join-Path $sourceRoot 'openwebui-tools\pdf_to_latex_layout.py') -Destination (Join-Path $root 'openwebui-tools\pdf_to_latex_layout.py')
Copy-Item -LiteralPath (Join-Path $sourceRoot 'openwebui-tools\document_to_latex_tool.py') -Destination (Join-Path $root 'openwebui-tools\document_to_latex_tool.py')
Copy-Item -LiteralPath (Join-Path $sourceRoot 'diagnostics\compare_pdf_layout.py') -Destination (Join-Path $root 'diagnostics\compare_pdf_layout.py')

& $PythonExe -m venv (Join-Path $root 'venv')
if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
$workerPython = Join-Path $root 'venv\Scripts\python.exe'
$wheel = Get-ChildItem -LiteralPath (Join-Path $root 'wheelhouse') -Filter 'pymupdf-*.whl' -File
if ($wheel.Count -ne 1) { throw 'Offline asset ZIP must contain exactly one PyMuPDF wheel.' }
& $workerPython -m pip install --no-index --no-deps $wheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Could not install the offline PyMuPDF wheel.' }

$sampleDir = Join-Path ([System.IO.Path]::GetTempPath()) ('openwebui-latex-preflight-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $sampleDir | Out-Null
$sample = Join-Path $sampleDir 'synthetic.pdf'
$createSample = @'
import pymupdf, sys
doc = pymupdf.open()
page = doc.new_page(width=612, height=792)
page.insert_font(fontname="Arial", fontfile="C:/Windows/Fonts/arial.ttf")
page.insert_text((72, 90), "Synthetic document conversion check", fontsize=16, fontname="Arial")
page.insert_text((72, 125), "This is a synthetic test and contains no user content.", fontsize=11, fontname="Arial")
doc.save(sys.argv[1])
'@
$createSample | & $workerPython - $sample
if ($LASTEXITCODE -ne 0) { throw 'Could not create the synthetic preflight document.' }

$env:LOCALAPPDATA = Join-Path $root 'localappdata'
$env:TECTONIC_CACHE_DIR = Join-Path $env:LOCALAPPDATA 'TectonicProject\Tectonic\cache'
$testZip = Join-Path $sampleDir 'draft.zip'
& $workerPython (Join-Path $root 'openwebui-tools\document_conversion_worker.py') `
    --source $sample --output $testZip --soffice $SofficeExe `
    --tectonic (Join-Path $root 'tectonic\tectonic.exe')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $testZip -PathType Leaf)) {
    throw 'Synthetic conversion preflight failed. The live Open WebUI service was not changed.'
}

Write-Output "Preflight passed. TargetRoot=$root"
Write-Output "worker_python=$workerPython"
Write-Output "worker_script=$(Join-Path $root 'openwebui-tools\document_conversion_worker.py')"
Write-Output "libreoffice_exe=$SofficeExe"
Write-Output "tectonic_exe=$(Join-Path $root 'tectonic\tectonic.exe')"
Write-Output "worker_localappdata=$(Join-Path $root 'localappdata')"
Write-Output "tool_source=$(Join-Path $root 'openwebui-tools\document_to_latex_tool.py')"
Write-Output 'No Open WebUI service or model settings were changed.'
