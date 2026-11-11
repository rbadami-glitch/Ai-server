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

The existing GitHub repository is public. The local private key for this audit is kept outside Git; only `response-audit-public.xml` is committed. `Collect-OpenWebUI-ResponseAudit.ps1` creates a temporary server folder containing the settings audit, service inventory, service-to-log mapping, port 80 listener ownership, recent Open WebUI/Ollama service logs, Ollama recorder files, a collection manifest, and recent Windows application crash events. It captures the last 7 days, prioritizes the most recently updated service logs, and captures at most 15 MiB per service log, 5 MiB per recorder file, and 70 MiB total. The manifest records any omitted or truncated material. `-MetadataOnly` skips log and recorder copies for a small follow-up capture. No services or settings are changed.

`-RunProbe -MetadataOnly` also sends one synthetic streaming request to the local Open WebUI API. It prompts for an API key without displaying or saving it, omits `max_tokens` from the request, and records HTTP status, timing, usage counts, stream completion marker, and `finish_reason` in `live-probe.json` inside the encrypted archive. It saves no prompt or response text. The prompt asks for 120 integers and may consume a few hundred output tokens. This measures the direct API path; browser chat may send different account or chat controls. The probe does not modify services or model settings.

`-SavedReplies -MetadataOnly` reads up to 5,000 recent chat records and aggregates assistant finish reasons and incomplete/error flags by day, account limit group, and chat limit group. It exports no chat text, identifiers, names, emails, or exact message times. Missing saved finish reasons are reported as missing metadata, not as proven interruptions. This path makes no inference request.

`-HistoricalDetails -MetadataOnly -SinceDays 14` creates a separate read-only list of saved assistant reply timestamps, numeric completion usage, one-way correlation hashes, and incomplete/error flags. It excludes chat text, user details, raw IDs, and error text. This supports comparison against previously collected service logs. A close timestamp match is circumstantial when multiple chats overlap, and absent finish reasons remain absent.

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

## Temporary browser response metadata capture

`Manage-ResponseMetadataCapture.ps1` installs a small Open WebUI Filter through the local administrator API. It records only UTC time, a short one-way request correlation hash, numeric output budgets, stream setting, terminal reason, token count, and completion/error flags. It reads no message content or user identity. The filter returns every request and event unchanged, records only the specified model with a browser session, stops recording after 24 hours by default, and caps its log at 5 MiB. It remains installed after expiry until disabled. It does not restart services or change model settings. The request hook sees Open WebUI's merged request settings just before provider normalization; later filters or provider translation could still change what reaches the model.

On the AI server, pull the approved source commit into the clean checkout, then run in **Administrator Windows PowerShell**:

```powershell
$repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github'
git -C $repo pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'diagnostics\Manage-ResponseMetadataCapture.ps1') -Action Install -Model 'curtiss-wright-corporation'
```

Enter an Open WebUI administrator API key at the hidden prompt. A lower privilege key fails without changing anything. After installation, use the model in the browser for a few normal replies, including a reply that stalls if it occurs. The capture will not retroactively explain old replies.

When the observation window is complete, disable the filter and collect the metadata. Both commands are PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'diagnostics\Manage-ResponseMetadataCapture.ps1') -Action Disable
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'diagnostics\Collect-ResponseMetadataCapture.ps1')
```

The collector validates and copies only the documented fields, encrypts them with the existing public key, and uploads only the encrypted archive to the temporary `response-metadata-capture-20261111` branch. Raw files stay outside Git under `E:\AI\ollama\OpenWebUI-Monitoring\Temp-Response-Metadata-Capture-20261111`. Keep all temporary data and the branch until analysis ends. If the push fails, the folder and branch remain for a retry. The source repository is public; never put the raw log, chat exports, or API key in it.
