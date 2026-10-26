# Keep the GPU dashboard running on the AI server

The dashboard listens on `127.0.0.1:8791`. Open <http://127.0.0.1:8791/> in a browser **on the AI server**. Its seven-day history stays outside Git at `E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History`.

This setup creates one Windows Scheduled Task, `CWAI-GPU-Dashboard`, running as Local System at startup. It starts the Python script without opening a browser or PowerShell window. Task Scheduler retries a failed process ten times, one minute apart. This changes only the dashboard; it does not restart Ollama or Open WebUI.

## First setup

1. On the AI server, confirm the Git checkout contains this file and `Manage-CW-GPU-Dashboard.ps1` in `E:\AI\ollama\OpenWebUI-Monitoring\github\GPU usage`.
2. In the old dashboard's PowerShell window, press **Ctrl+C**. Do not delete the old script or history files.
3. Open **Windows PowerShell as Administrator** on the AI server and run:

   ```powershell
   & 'E:\AI\ollama\OpenWebUI-Monitoring\github\GPU usage\Manage-CW-GPU-Dashboard.ps1' -Mode Install
   ```

   If Python is not found, rerun with `-PythonPath 'C:\path\to\python.exe'` using the installed Python 3 executable on the AI server.

4. Open <http://127.0.0.1:8791/> on the AI server. Check that live GPU/Ollama values appear, the custom minute/hour history controls work, and today's JSON file appears in the history directory. If the page shows GPU errors, check whether the Python task account can run `nvidia-smi`.

The setup refuses to register the task while another process listens on port 8791. It also refuses to replace an existing task with the same name.

## Update the code later

Review and publish a code-only commit to the approved private repository. On the AI server, confirm the checkout is clean and fast-forward it to that approved commit:

```powershell
Set-Location 'E:\AI\ollama\OpenWebUI-Monitoring\github'
git status --short
git pull --ff-only
git rev-parse --short HEAD
& '.\GPU usage\Manage-CW-GPU-Dashboard.ps1' -Mode Restart
```

Run the last command in an elevated Windows PowerShell. Restarting the task loads the updated Python file. A Git pull alone does not change the running process. Keep the history directory in place.

## Check status

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\GPU usage\Manage-CW-GPU-Dashboard.ps1' -Mode Status
```

If the link must open from a different computer, this local-only listener needs a separately reviewed access method. Do not change the bind address or firewall as part of this setup.
