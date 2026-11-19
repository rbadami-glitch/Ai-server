# Document to LaTeX pilot for Open WebUI 0.13.4

This opt-in Workspace Tool accepts a DOC, DOCX, or selectable PDF attached to the current chat. It reads the user's original uploaded file, makes a fixed-layout LaTeX draft, renders it offline, compares the rendering with the source PDF, and uploads a ZIP only after verification. The ZIP contains `template.tex`, `preview.pdf`, images, and a report. Longer text edits can overlap because the layout does not reflow. This is a draft for visual review, not a fully semantic template or an OCR solution.

The normal chat retrieval path is unchanged. The tool does not call a model or a network service. Scanned PDFs, unsupported PDF drawing types, too many pages, excessive input size, missing dependencies, and failed comparisons return a clear failure without a download.

## Source and private asset

- Source branch: `fix/openwebui-document-fidelity-audit`. Use the exact reviewed commit printed by `git rev-parse HEAD` after fetch.
- Private offline asset, transferred separately to the AI server: `C:\Users\HP\Downloads\openwebui-latex-offline-assets-20261119.zip`.
- Asset SHA-256: `A8E23A6254D5B2B1148D6D9DB43BA408563D3CF1EEA7CF2E61967D6BCB21C9B3`.
- The asset contains a portable Tectonic binary, cached TeX resources, and a Windows Python 3.11-compatible PyMuPDF wheel. It contains no customer document. Do not commit the asset or customer documents to public Git.

## Server staging, no service restart

Copy the private asset ZIP to a private server folder such as `E:\AI\ollama\OpenWebUI-Monitoring\offline-assets`. In an elevated PowerShell window, stage the source from the existing clean Git checkout in a separate worktree. Verify that the branch and hash are the intended ones before setup:

```powershell
$repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github'
$source = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-Document-LaTeX-20261119'
git -C $repo fetch origin fix/openwebui-document-fidelity-audit
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
git -C $repo worktree add --detach $source origin/fix/openwebui-document-fidelity-audit
if ($LASTEXITCODE -ne 0) { throw 'Worktree staging failed' }
git -C $source rev-parse HEAD
```

Run the isolated setup with the copied asset. Choose a `TargetRoot` that does not already exist. This only creates a new directory and a virtual environment, then converts a synthetic PDF. It does not edit or restart Open WebUI:

```powershell
$asset = 'E:\AI\ollama\OpenWebUI-Monitoring\offline-assets\openwebui-latex-offline-assets-20261119.zip'
$target = 'E:\apps\open-webui\document-converter-v1'
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $source 'openwebui-tools\Setup-DocumentConverter.ps1') -AssetsZip $asset -TargetRoot $target
if ($LASTEXITCODE -ne 0) { throw 'Setup or synthetic preflight failed; do not enable the tool' }
```

The setup prints the five absolute paths for the Tool Valves. Confirm that it reports `"ok": true` and `Preflight passed`. If LibreOffice uses a different path, pass `-SofficeExe` explicitly. Do not use a Python executable in place of LibreOffice in production.

Tectonic requires `TECTONIC_CACHE_DIR` to point directly to the copied `localappdata\TectonicProject\Tectonic\cache` folder. The worker sets this environment variable for every render. Changing `LOCALAPPDATA` alone does not redirect Tectonic's cache on Windows.

## Open WebUI pilot

1. As an administrator, create a new Workspace Tool named `Document to LaTeX pilot`. Paste the contents of the printed `tool_source` file into the Tool editor. Do not attach it to the shared production model yet.
2. In the Tool's Valves, enter the printed `worker_python`, `worker_script`, `libreoffice_exe`, `tectonic_exe`, and `worker_localappdata` paths. Keep the tool restricted to the pilot user.
3. Start a new pilot chat and enable this Tool for that chat. Attach a synthetic DOC, DOCX, or PDF whose contents and expected appearance are known. Ask: `Use the document conversion tool on the attached file. Give me the downloadable LaTeX draft and state its limitations.`
4. Download the ZIP, open `preview.pdf`, and compare each page against the original. Verify that `template.tex` and any image assets exist. Check an unsupported scanned PDF returns `ocr_required` without a download. Only then consider enabling the Tool on the intended model.
5. For routine use, add a model instruction that tells the assistant to call this Tool when the user asks to convert an attached document to LaTeX, and to report a tool failure without inventing a file or claiming layout fidelity. Do not call it for ordinary document questions.

The live Open WebUI Workspace Tool path has not yet been tested on the AI server. The local tests verify the 0.13.4 API shape and mocked upload flow, but the pilot must confirm that attachments reach the Tool and the generated ZIP is accessible to the requesting user.

The worker runs under the Open WebUI service identity. Keep the Tool restricted to the pilot while evaluating uploaded-file parser isolation before a general rollout.

## Rollback

Disable or remove the pilot Tool from the model/chat. Existing document Q&A and file processing continue through their previous paths. Do not delete the server staging or converter directory during rollback; retain them for diagnosis. No Open WebUI service restart is required for this Tool rollback.
