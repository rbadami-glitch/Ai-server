# Open WebUI monitoring source

This folder is for **source code only**. Its local Git `origin` is the approved private repository `https://github.com/rbadami-glitch/Ai-server.git`. Do not add these tools to `Cw-llama-index` or commit the whole `GPU-RCA-Checks` folder. A configured remote alone does not mean any commit has been pushed or deployed.

| Source file | Purpose |
| --- | --- |
| `GPU usage/CW-GPU-Dashboard.py` | Local-only GPU/Ollama dashboard on `127.0.0.1:8791`; live status every 3 seconds, a GPU history sample every minute, custom minute/hour graph range, selected day, and last 7 days. |
| `OpenWebUi Monitor/OpenWebUI-Feedback-Dashboard.py` | Separate Open WebUI feedback dashboard; review its database path, permissions, and access configuration before deployment. |

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
