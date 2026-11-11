# Open WebUI response budget audit

`openwebui_response_budget_audit.py` reads selected generation settings from an Open WebUI SQLite database in read-only mode. It can also query local Ollama model metadata and summarize recent request metadata from recorder files. It makes no inference request and changes no settings or services.

The report contains model identifiers and numeric settings. It excludes prompts, answers, API keys, connection URLs, user identifiers, and source IPs. Keep reports outside this public Git checkout and do not commit them.

Example for an operator who has confirmed the database path and model ID:

```powershell
$repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github'
$out = 'E:\AI\ollama\OpenWebUI-Monitoring\Temp-Response-Budget-Audit-20261111'
New-Item -ItemType Directory -Path $out -Force | Out-Null
python -B (Join-Path $repo 'diagnostics\openwebui_response_budget_audit.py') `
  --db 'C:\downloads\open-webui\data\webui.db' `
  --model '<exact-model-id>' `
  --output (Join-Path $out 'settings.json') `
  --ollama-url 'http://127.0.0.1:11434' `
  --ollama-url 'http://127.0.0.1:11435' `
  --recorder-dir 'E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage'
```

The script samples metadata-only overrides from up to 500 recent chats. It still cannot prove the budget of a particular request: caller-supplied parameters and later edits may differ from the saved chat controls. It cannot identify why a response stopped without the affected request's finish reason, browser/network result, and corresponding service logs. Treat a missing recorder event as missing evidence, not proof that Ollama received nothing.

## Encrypted response investigation

The existing GitHub repository is public. The local private key for this audit is kept outside Git; only `response-audit-public.xml` is committed. `Collect-OpenWebUI-ResponseAudit.ps1` creates a temporary server folder containing the settings audit, service inventory, recent Open WebUI/Ollama service logs, Ollama recorder files, a collection manifest, and recent Windows application crash events. It captures the last 7 days, prioritizes the most recently updated service logs, and captures at most 15 MiB per service log, 5 MiB per recorder file, and 70 MiB total. The manifest records any omitted or truncated material. No services or settings are changed.

On the AI server, after pulling the source commit into the clean checkout, run this single command in **Administrator Windows PowerShell**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'E:\AI\ollama\OpenWebUI-Monitoring\github\diagnostics\Collect-OpenWebUI-ResponseAudit.ps1'
```

The script uploads only `response-audit.zip.enc` to the temporary `response-audit-20261111` branch. The raw archive and logs remain under `E:\AI\ollama\OpenWebUI-Monitoring\Temp-Response-Budget-Audit-20261111` until the investigation is finished. If the database or repository lives elsewhere, supply `-Db` or `-Repo` with the verified path. A failed upload leaves the temporary worktree and encrypted file available for inspection and retry. Do not commit the raw folder, `settings.json`, or the private key.

On the local computer, fetch the temporary branch for analysis:

```powershell
git -C 'C:\Users\HP\Documents\etag\Documentation\Openwebui\github' fetch origin response-audit-20261111
```

Delete the temporary data and branch only after analysis and any follow-up capture are complete. Removing a Git branch does not guarantee that GitHub immediately erases its old objects, so protect the private key even after cleanup.
