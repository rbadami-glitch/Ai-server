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

The script deliberately cannot prove the budget of a particular chat: chat controls and caller-supplied request parameters may override saved defaults. It cannot identify why a response stopped without the affected request's finish reason, browser/network result, and corresponding service logs. Treat a missing recorder event as missing evidence, not proof that Ollama received nothing.
