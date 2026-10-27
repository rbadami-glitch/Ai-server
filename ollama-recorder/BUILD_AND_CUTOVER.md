# Ollama request recorder: build and cutover plan

This plan is for the two existing Windows Ollama services on the AI server. It does not reboot Windows, change client URLs or ports, install a packet-capture driver, or alter Open WebUI or LiteLLM. **Do not start the service cutover until the candidate artifact and exact server preflight have been reviewed.**

## Build a candidate without touching the AI server

1. Run the private repository's **Build Ollama request recorder** workflow manually. It checks out Ollama source commit `87cff95af86a1d8be1d81ce75fafc9bb4e67d002` (tag `v0.30.6`), copies these two recorder files, applies `routes.patch`, runs the synthetic recorder tests, and builds a Windows amd64 `ollama-direct-usage.exe`.
2. Download the workflow artifact. Check that its `BUILD.txt` source commit and SHA-256 match the executable. Preserve the artifact outside the Git checkout. The workflow uploads no AI-server data.
3. The build succeeding is only a candidate. Its compatibility with the two installed service directories and their native runner files must be checked on the AI server before any service change.

## Read-only AI-server preflight

Run as an administrator and record these results privately for each of `CWAI-Ollama` and `CWAI-Gemma4-Ollama`:

- Actual NSSM `Application`, `AppParameters`, `AppDirectory`, service account, and service status. The two services have historically used separate per-user Ollama installations; never assume one executable path serves both.
- SHA-256, size, and file version of each current `ollama.exe`, plus `/api/version` on `10.104.74.10:11434` and `127.0.0.1:11435`. Both must be compatible with the pinned `0.30.6` candidate.
- Owner of each listening port, current model residency from `/api/ps`, current GPU memory, available disk space, and effective service environment configuration. Do not print credentials or full environment values into chat.
- Whether `E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage` exists, its access control, and whether the service accounts and the feedback dashboard task can respectively write and read it. The recorder stores source IP, model, route, HTTP status, and available token counts; it never stores prompt or response text.

Do not change permissions as part of this preflight. If the directory's access control is unsuitable, revise and review the plan before enabling capture.

## Proposed production cutover, one service at a time

During a quiet period, stage a hash-verified copy of the candidate executable and a backup of the exact original executable **outside** the Git checkout. Preserve the sibling Ollama native runner files. Save the existing service-specific environment settings before editing them. The recorder is off by default; only `OLLAMA_DIRECT_USAGE_DIR` enables it.

For the first service only: stop that Ollama service, replace only its `ollama.exe` with the staged candidate, set its service-specific `OLLAMA_DIRECT_USAGE_DIR` to `E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage`, and start the same service. Confirm it returns to Running, owns the same port, reports version `0.30.6`, and answers a read-only `/api/version`. Use a deliberately invalid inference request to verify that one metadata record appears in its `ollama-PORT-YYYY-MM-DD.jsonl` without loading a model. Check the dashboard's **Ollama requests** page for that record and its missing-token display. Then observe a normal authorized request and confirm its model and token counts where Ollama supplies them.

Only after the first service is verified should the same steps be considered for the second. A service restart interrupts in-flight requests and unloads that service's resident models; the OS is never restarted. The recorder queues a small metadata event after the HTTP handler finishes so file I/O is outside the response path, but the production performance impact has not yet been measured.

## Rollback gate

If the service does not start, its listener is wrong, a request fails unexpectedly, the JSONL record is absent or malformed, or latency materially worsens: stop **only the changed service**, restore its original executable and service-specific environment, and start it again. Verify the original `/api/version`, listener, and a normal authorized request. Keep the backup and collected records for investigation; do not delete histories or the other service's files. Review the failure before attempting the second service.

The dashboard shows observed records, not a guaranteed count of every HTTP request. A full queue or write failure can cause gaps; the recorder logs queue drops, and the dashboard marks recorded drops as incomplete. JSONL files are not deleted automatically. The dashboard reads up to seven days; decide retention separately after observing disk use.
