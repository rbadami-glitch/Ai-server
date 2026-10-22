# Agent instructions for Open WebUI monitoring source

Scope: this code-only folder and its future private Git repository. The AI server and its existing monitoring data are production resources, not a scratch workspace.

## Do

- Read `README.md` before changing deployment paths or dashboard behavior.
- Make focused code changes locally and test with synthetic/temporary data. For GPU changes, run `python -B tests\test_gpu_dashboard.py`. Review `git diff`, `git status`, and the staged file list before any commit or push.
- Keep a separate, approved private repository for these tools. Its AI-server checkout is `E:\AI\ollama\OpenWebUI-Monitoring\github`; use fast-forward-only updates of a clean checkout and identify the exact approved commit.
- Keep GPU history at `E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History` **on the AI server**, outside the Git checkout. Preserve existing JSON files when updating the script.
- Treat feedback, logs, model-test captures, SQLite databases, backups, API keys, and user chat content as sensitive. Ask before publishing any code or examples that may contain company information.
- Verify that the new dashboard starts on the AI server, serves `127.0.0.1:8791`, shows live data and custom minute/hour history, and writes to the server history folder before retiring the old copy.

## Do not

- Do not commit runtime data, credentials, `.env` files, feedback records, `Model-Test-*.jsonl`, `KeepAlive-Backups`, or `CW-GPU-Dashboard-History`. Check staged contents even when `.gitignore` exists.
- Do not run the GPU dashboard on the local computer or redirect its history to the local workspace.
- Do not add these tools to the unrelated `Cw-llama-index` repository or alter that repository's dirty worktree.
- Do not delete or overwrite the old server scripts, history, or database during setup; do not use `git clean -fdx`, `git reset --hard`, or force-push as a shortcut.
- Do not restart Open WebUI or Ollama, change service settings, load/unload models, change file permissions/ownership, or open firewall access as part of a dashboard source update without an explicit request and reviewed plan.
- Do not assume that a Git push/pull restarts a running dashboard. Stop and start only the intended dashboard process during an approved cutover.
