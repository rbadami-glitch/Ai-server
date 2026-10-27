# Ollama request recorder: build and cutover plan

This plan is for the two existing Windows Ollama services on the AI server. It does not reboot Windows, change client URLs or ports, install a packet-capture driver, or alter Open WebUI or LiteLLM. **Do not start the service cutover until the candidate artifact and exact server preflight have been reviewed.**

## Build a candidate without touching the AI server

1. Run the private repository's **Build Ollama request recorder** workflow manually. It checks out Ollama source commit `87cff95af86a1d8be1d81ce75fafc9bb4e67d002` (tag `v0.30.6`), copies these two recorder files, applies `routes.patch`, runs the synthetic recorder tests, and builds a Windows amd64 `ollama-direct-usage.exe`.
2. Download the workflow artifact. Check that its `BUILD.txt` source commit and SHA-256 match the executable. Preserve the artifact outside the Git checkout. The workflow uploads no AI-server data.
3. The build succeeding is only a candidate. Run #2 at recorder commit `df88217` produced a 35,371,520-byte executable whose SHA-256 is `D15A734F98260D366752B361031DC6C998AAA58CE16433B4ED6ABDFCF136B8B1`. The downloaded ZIP passed its integrity check and matched `BUILD.txt`, but the executable exited on the AI server with `0xC0000135` before printing its version. Its Windows import table includes `libwinpthread-1.dll`, which was present on the GitHub runner but not packaged. Do not deploy run #2. The revised workflow packages that DLL, records both file hashes, and checks that the executable starts with the toolchain removed from `PATH`. Verify the new artifact on the AI server before any service change. The candidate remains unsigned; compatibility and local execution policy must still be checked.

## Read-only AI-server preflight

Run as an administrator and record these results privately for each of `CWAI-Ollama` and `CWAI-Gemma4-Ollama`:

- Actual NSSM `Application`, `AppParameters`, `AppDirectory`, service account, and service status. On 27 October both Running services were verified to use the **same** signed `C:\Users\v_skumard\AppData\Local\Programs\Ollama\ollama.exe`, SHA-256 `AD4019D2CB32F959C086F8963DD0C30ABFA7AB220F11C9BA57E4149CACB7A0A7`, signature status Valid. Recheck immediately before cutover.
- SHA-256, size, and file version of each current `ollama.exe`, plus `/api/version` on `10.104.74.10:11434` and `127.0.0.1:11435`. Both must be compatible with the pinned `0.30.6` candidate.
- Owner of each listening port, current model residency from `/api/ps`, current GPU memory, available disk space, and effective service environment configuration. Do not print credentials or full environment values into chat.
- Whether the installed `lib\ollama\llama-server.exe` and GPU libraries exist beside the signed executable, and whether the unsigned candidate can run `--version` from that directory without an execution-policy block. Running as administrator does not prove that the LocalSystem service account is allowed to execute it.
- Whether `E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage` exists, its access control, and whether the service accounts and the feedback dashboard task can respectively write and read it. The recorder stores source IP, model, route, HTTP status, and available token counts; it never stores prompt or response text.

Do not change permissions as part of this preflight. If the directory's access control is unsuitable, revise and review the plan before enabling capture.

## Proposed production cutover, one service at a time

Transfer the artifact to the AI server through an approved local file-transfer path, verify both executable and runtime-DLL SHA-256 values there, and stage `ollama-direct-usage.exe` and `libwinpthread-1.dll` **beside** the installed signed `ollama.exe`. Ollama 0.30.6 finds native runner files relative to the executable, so the side-by-side location preserves the installed `lib\ollama` tree. Do not replace or rename the signed original. Verify the candidate's `--version` there before any service edit. If the unsigned candidate is blocked or fails to start, stop the pilot.

During a quiet period, save the exact original NSSM `Application`, `AppDirectory`, parameters, and service-specific environment for both services in a private backup **outside** the Git checkout. The recorder is off by default; only `OLLAMA_DIRECT_USAGE_DIR` enables it.

For the first service only: stop that Ollama service, change **only that service's** NSSM `Application` to the staged `ollama-direct-usage.exe`, add its service-specific `OLLAMA_DIRECT_USAGE_DIR=E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage` while preserving every prior environment entry, and start the same service. Keep `AppDirectory` pointing to the original installation directory. Confirm it returns to Running, owns the same port, reports version `0.30.6`, and answers a read-only `/api/version`. Use a deliberately invalid inference request to verify that one metadata record appears in its `ollama-PORT-YYYY-MM-DD.jsonl` without loading a model. Check the dashboard's **Ollama requests** page for that record and its missing-token display. Then observe a normal authorized request and confirm its model and token counts where Ollama supplies them.

Only after the first service is verified should the same steps be considered for the second. A service restart interrupts in-flight requests and unloads that service's resident models; the OS is never restarted. The recorder queues a small metadata event after the HTTP handler finishes so file I/O is outside the response path, but the production performance impact has not yet been measured.

## Rollback gate

If the service does not start, its listener is wrong, a request fails unexpectedly, the JSONL record is absent or malformed, or latency materially worsens: stop **only the changed service**, restore its NSSM `Application` to the still-present signed `ollama.exe` and its exact prior service-specific environment, and start it again. Verify the original `/api/version`, listener, and a normal authorized request. Keep the backup and collected records for investigation; do not delete histories or the other service's files. Review the failure before attempting the second service.

The dashboard shows observed records, not a guaranteed count of every HTTP request. A full queue or write failure can cause gaps; the recorder logs queue drops, and the dashboard marks recorded drops as incomplete. JSONL files are not deleted automatically. The dashboard reads up to seven days; decide retention separately after observing disk use.
