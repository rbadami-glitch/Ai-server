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

## Automatic Open WebUI pilot

Users should attach a document and ask for a LaTeX template normally. They should not select a Tool in the chat composer.

1. As an administrator, create a Workspace Tool named `Document to LaTeX pilot`. Paste the contents of the printed `tool_source` file into the Tool editor. In its Valves, enter the printed `worker_python`, `worker_script`, `libreoffice_exe`, `tectonic_exe`, and `worker_localappdata` paths. Keep access restricted to the pilot account.
2. Clone the intended Open WebUI model into a private pilot model. In the clone's **Tools** row, attach `Document to LaTeX pilot`. Use Native function calling. Leave the shared production model unchanged during the pilot.
3. Append this narrow instruction to the pilot model's system prompt: `When a user asks to convert or recreate an attached DOC, DOCX, or PDF as LaTeX, call convert_attached_document_to_latex. Return the verified download link and explain that longer edits to this fixed-layout draft may overlap. If the tool fails, report the failure; do not invent a file. For ordinary questions about a document, answer normally without conversion.`
4. In a new chat using the pilot model, attach one synthetic document and ask naturally: `Turn this document into a LaTeX template with the same layout.` Do not enable a Tool in the chat. Confirm the model called the Tool and produced a working ZIP link. Inspect `preview.pdf`, `template.tex`, and assets. Also try a second phrasing and an ordinary document question to check that conversion is triggered only when requested.
5. After the pilot succeeds, grant the intended users read access to the Tool and attach it to the shared model through **Workspace → Models → Tools**. Append the same narrow instruction to that model. Model-level attachment does not bypass Tool access control. Disable or detach the Tool if the model calls it incorrectly.

Native tool selection is decided by the model, so model-level attachment alone does not guarantee every paraphrase will trigger conversion. If pilot phrasings miss the Tool, do not describe the feature as reliable; investigate model tool-calling support or add a deterministic server-side router before broad rollout.

The live Open WebUI Workspace Tool path has not yet been tested on the AI server. The local tests verify the 0.13.4 API shape and mocked upload flow, but the pilot must confirm that attachments reach the Tool and the generated ZIP is accessible to the requesting user.

The worker runs under the Open WebUI service identity. Keep the Tool restricted to the pilot while evaluating uploaded-file parser isolation before a general rollout.

## Rollback

Detach or disable the Tool from the model. Existing document Q&A and file processing continue through their previous paths. Do not delete the server staging or converter directory during rollback; retain them for diagnosis. No Open WebUI service restart is required for this Tool rollback.
