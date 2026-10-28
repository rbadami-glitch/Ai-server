# Ollama request recorder: build and cutover plan

This plan is for the two existing Windows Ollama services on the AI server. It does not reboot Windows, change client URLs or ports, install a packet-capture driver, or alter Open WebUI or LiteLLM. **Do not start the service cutover until the candidate artifact and exact server preflight have been reviewed.**

## Build a candidate without touching the AI server

For the latency update, build a fresh candidate from the commit that adds `duration_ms`. The already-running recorder executable cannot emit this field merely because the dashboard or Git checkout was updated. Existing JSONL rows remain valid; their latency is unavailable. The dashboard can be updated independently before the new recorder build is deployed.

As of 28 October, both H100 NVL devices were absent from the AI server VM after a Configuration Manager initiated restart. The operator has explicitly chosen to proceed with the recorder upgrade while this GPU issue remains. The timing measurement does not require a GPU, but requests made during the outage do not represent normal GPU inference latency. Restart only one Ollama service at a time and verify it before changing the other. Do not reboot Windows.

## Upgrade the already-running recorder to capture latency

The versioned ZIP at ollama-recorder/artifacts/ollama-direct-usage-7a6bed6-windows-amd64.zip is a verified build of recorder source 7a6bed6. It was committed to this private repository at the operator's explicit request for transfer to the AI server. Its SHA-256 is 421DD04EE99F638AC41A142EC8A71EC039E37BE5E17D365E2B3E23F32418394A.

Use Upgrade-CW-Ollama-Recorder-Latency.ps1 from an elevated PowerShell on ZUSEVRAI01. Plan mode is read-only. Apply mode checks the old executable, new artifact, existing runtime DLL and NSSM settings; extracts the new executable under a unique name beside the existing Ollama executable; then stops and starts **only the selected service**. It changes only that service's NSSM Application value, leaving the environment, old executable, DLL, recorder files, ports and other service in place. It verifies the service process, Ollama version, listener and a synthetic missing-model request with a recorded duration_ms. A failed verification attempts to restore the old executable and restart the selected service. Rollback mode reverses a successful upgrade for one service. The probe is excluded from dashboard totals.

Run Plan mode for both services, then Apply mode for CWAI-Gemma4-Ollama and verify the private listener before applying to CWAI-Ollama. After both pass, make an authorized real request through each route and check that new dashboard rows show latency. Old rows remain "Not recorded". Keep the old executable for rollback. No prompt or answer text enters the recorder.

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

The guarded switch script is `Switch-CW-Ollama-Recorder.ps1`. Run `-Mode Plan -ServiceName CWAI-Gemma4-Ollama` first, then `-Mode Apply` for that service. It checks the pinned original/candidate/DLL hashes, the NSSM settings, and the private folders; saves the original `Application` and `AppEnvironmentExtra` in a private backup; stops only the selected service; adds the recorder variable without removing existing entries; and checks the original port, version, service process, and one synthetic missing-model metadata record. On a failed health or recorder check it attempts to restore the original settings and restart the same service. It prints the exact `-Mode Rollback -BackupFile` command after a successful switch. The backup may contain sensitive environment values; never commit or share it. A full `.reg` export made before the first switch is a separate recovery copy.

The private `11435` switch passed on 27 October. The first general `11434` attempt failed a narrow health check because the real Ollama listener appeared as IPv6 `::` while a separate Windows port proxy owned `127.0.0.1:11434`. The automatic rollback restored the signed `ollama.exe`; read-only checks confirmed the original service was Running and `/api/version` returned `0.30.6` on both its published and localhost addresses. The revised script selects the listener whose process is the selected NSSM service's child and whose executable matches the expected path. Run read-only `-Mode Verify -ServiceName CWAI-Ollama` against the restored original service before retrying Apply.

For the first service only: stop that Ollama service, change **only that service's** NSSM `Application` to the staged `ollama-direct-usage.exe`, add its service-specific `OLLAMA_DIRECT_USAGE_DIR=E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage` while preserving every prior environment entry, and start the same service. Keep `AppDirectory` pointing to the original installation directory. Confirm it returns to Running, owns the same port, reports version `0.30.6`, and answers a read-only `/api/version`. Use a deliberately invalid inference request to verify that one metadata record appears in its `ollama-PORT-YYYY-MM-DD.jsonl` without loading a model. Check the dashboard's **Ollama requests** page for that record and its missing-token display. Then observe a normal authorized request and confirm its model and token counts where Ollama supplies them.

Only after the first service is verified should the same steps be considered for the second. A service restart interrupts in-flight requests and unloads that service's resident models; the OS is never restarted. The recorder queues a small metadata event after the HTTP handler finishes so file I/O is outside the response path, but the production performance impact has not yet been measured.

## Rollback gate

If the service does not start, its listener is wrong, a request fails unexpectedly, the JSONL record is absent or malformed, or latency materially worsens: stop **only the changed service**, restore its NSSM `Application` to the still-present signed `ollama.exe` and its exact prior service-specific environment, and start it again. Verify the original `/api/version`, listener, and a normal authorized request. Keep the backup and collected records for investigation; do not delete histories or the other service's files. Review the failure before attempting the second service.

The dashboard shows observed records, not a guaranteed count of every HTTP request. A full queue or write failure can cause gaps; the recorder logs queue drops, and the dashboard marks recorded drops as incomplete. JSONL files are not deleted automatically. The dashboard reads up to seven days; decide retention separately after observing disk use.
