# Open WebUI monitoring source

This folder is for **source code only**. Its local Git `origin` is the approved private repository `https://github.com/rbadami-glitch/Ai-server.git`. Do not add these tools to `Cw-llama-index` or commit the whole `GPU-RCA-Checks` folder. A configured remote alone does not mean any commit has been pushed or deployed.

| Source file | Purpose |
| --- | --- |
| `GPU usage/CW-GPU-Dashboard.py` | Local-only GPU/Ollama dashboard on `127.0.0.1:8791`; live status every 3 seconds, a GPU history sample every minute, custom minute/hour graph range, selected day, and last 7 days. |
| `OpenWebUi Monitor/OpenWebUI-Feedback-Dashboard.py` | Local-only Open WebUI feedback dashboard on `127.0.0.1:8789`, with a separate chat-usage page. Review its database path, permissions, and access configuration before deployment. |
| `OpenWebUi Monitor/direct_usage.py` | Third dashboard page that reads the general Ollama service's existing access logs, or metadata-only JSONL records if a recorder is later deployed. |

## Local direct Ollama usage prototype (not deployed)

The feedback dashboard now has an **Ollama request usage** page at `/direct-usage`. Without a recorder, it reads the last 8 MiB of each existing general-service access log at `E:\AI\ollama\logs\ollama-service.log` and `ollama-service-error.log`. The 27 October `/api/tags` check confirmed a real Gin access entry from the general service. The page shows observed **POST inference requests** on `11434`, with time, route, HTTP status, and source IP. These access lines do not contain model or token counts, so those fields appear as unrecorded. They do not cover the private `11435` service, and truncated or rotated logs can omit earlier requests. A proxy may make the source IP appear local. No prompt or response text is displayed.

If recorder files are present for `11434`, the page uses them instead of that service's access logs to avoid double counting. It also reads any recorder files for `11435`. Files are supplied with `--direct-usage-dir` (default `E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage` on the AI server). The dashboard does not create that folder or collect requests itself. No recorder is deployed yet, so the page cannot show private-service requests or model and token details from direct Ollama traffic today.

The proposed recorder file names are `ollama-11434-YYYY-MM-DD.jsonl` and `ollama-11435-YYYY-MM-DD.jsonl`, using UTC dates. Each line is one JSON object with schema version 1 and these fields: `at` (Unix seconds), `listener`, `route`, `model`, `source_ip`, `status`, `prompt_tokens`, `completion_tokens`, and optional `total_tokens`. Token fields may be null. No prompt, response, API key, or authorization header belongs in these files. The dashboard groups observed records by the same minute/hour/day/week controls as the Open WebUI usage page and marks missing counts as unrecorded. A source IP is not a person's name; forwarding through a local proxy can make the source appear as `127.0.0.1`.

This is a local source change until pushed to the private repository, pulled on the AI server, and the feedback dashboard task restarted. The original `ollama.exe`, its NSSM service settings, all server data, and the running dashboards have not been changed. A separate instrumented Ollama 0.30.6 executable must be built and tested before the page can show private-service requests and model/token counts. The production cutover and rollback require a separate review and explicit approval.

The manually triggered Windows candidate build and the production review steps are in [`ollama-recorder/BUILD_AND_CUTOVER.md`](ollama-recorder/BUILD_AND_CUTOVER.md). Building an artifact alone does not deploy it or restart a service.

## Open WebUI feedback and chat usage

The dashboard reads Open WebUI's SQLite database in read-only mode. It prints a fresh local access-key URL when started; keep that URL private. Two navigation buttons open **Feedback data** and **Usage data** in the same local dashboard. Usage data is a separate `/usage` page with the same custom minutes/hours, selected-day, and last-seven-days controls as the GPU dashboard. It refreshes every 15 seconds and reads saved `chat_message` rows; it does not create seven new history files because the database already contains the dated records.

"Chat prompts" counts saved `user` messages and "assistant responses" counts saved `assistant` messages. These are **not** all HTTP/API requests to Open WebUI, and a request that never became a saved message may be absent. Token figures come only from assistant messages with usable saved `usage` fields. The page shows coverage so missing token counts are not misrepresented as zero. It breaks down recorded input, output, and total tokens by model, caller, and time period (minutes for ranges up to an hour, hours for longer ranges or a selected day, and days for a week). The caller uses the saved message user ID, falling back to the chat owner when needed. It shows recent message metadata but no chat text. The database may retain more than seven days; the page intentionally limits its date selector to the last seven days.

Run it on the AI server after confirming the database path, using a separate PowerShell window from the GPU dashboard:

```powershell
python -B 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\OpenWebUI-Feedback-Dashboard.py' --db 'C:\downloads\open-webui\data\webui.db'
```

If the actual `webui.db` is elsewhere, pass that exact path to `--db`. The script does not restart Open WebUI or change its database. The new Git commit must be pulled on the AI server and this Python process restarted before its new page appears. Offline tests use a synthetic database: `python -B -m unittest discover -s tests -p 'test_*.py'`.

## Proposed direct Ollama capture pilot

Direct calls to Ollama are outside the Open WebUI chat-usage page. The operator requires unchanged client URLs and no added wait in the user request/answer path. A proxy or Ollama port cutover is not part of this pilot.

`tests/probe_npcap_loopback.py` is a **count-only feasibility probe**, not a running monitor. Its pilot is suspended: the operator cannot shut down, restart, or reboot the Windows AI-server OS, and Npcap installation may require a reboot or briefly interrupt networking. Do not install Npcap or run this probe under the current constraint. The script passed a local syntax check but has not been run with Npcap or on the AI server.

The built-in Windows WFP capture provider is the next feasibility check. Its availability and loopback visibility on this server have not yet been verified. A packet-counting result would not establish exact request counts, HTTP status extraction, token parsing, deduplication across forwarding rules, or unchanged model-response latency. Those need separate validation before a long-running collector or dashboard page is considered ready.

## Where GPU history lives

Run the GPU dashboard **on the AI server**. Its history is written to:

```text
E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History
```

That path is on the AI server, **outside the planned Git checkout** at `E:\AI\ollama\OpenWebUI-Monitoring\github`. It is not a local-computer history folder and must never be copied to GitHub. The dashboard creates the folder when it starts if needed, keeps one JSON file per local calendar day for up to seven days, and records while its Python process runs, even if the browser closes. It cannot recover time before it started or while it was stopped. The old standalone server copy previously used the same history path, so leave that folder in place during migration. Never delete it as part of a source update.

The local source can be edited and tested without running the dashboard or accessing production services. Do not run `CW-GPU-Dashboard.py` on the local computer; running it starts a monitor and attempts to write to the server-specific `E:` path.

Run the offline GPU checks from this folder on the local computer with `python -B tests\test_gpu_dashboard.py`. They use temporary synthetic data, not the AI server's history.

## Planned Git workflow

1. Use the approved private GitHub repository. Do not put credentials in the URL or in committed files.
2. On the local computer, review and test **code only**, inspect staged changes, and push an approved commit. Keep history JSON, feedback databases, logs, captures, backups, `.env` files, and secrets out of Git. `.gitignore` is a safeguard, not a substitute for checking staged files.
3. On the AI server, clone the approved repository into a new empty `E:\AI\ollama\OpenWebUI-Monitoring\github` folder. If it already exists, inspect it first; do not overwrite or delete it. Update a clean checkout with `git pull --ff-only` only after reviewing the intended commit.
4. Stop the old dashboard process with Ctrl+C before starting the new copy, because both use port `8791`. Keep the old standalone script and history folder until the new dashboard and its history are verified. Pulling code alone does not restart a running Python process.

After the new checkout is approved and the old dashboard process is stopped, run this **on the AI server**:

```powershell
python -B 'E:\AI\ollama\OpenWebUI-Monitoring\github\GPU usage\CW-GPU-Dashboard.py'
```

Open `http://127.0.0.1:8791/` in the AI-server browser. Confirm the custom minute/hour options and that a current-day JSON file appears in the server history folder. Only then retire the old script if it is no longer needed; keep the history folder.
