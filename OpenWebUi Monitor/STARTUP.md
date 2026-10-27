# Keep the Open WebUI feedback dashboard running on the AI server

The feedback dashboard listens only on `127.0.0.1:8789` on the AI server. The `CWAI-Feedback-Dashboard` Scheduled Task runs as Local System at Windows startup, independently of a PowerShell window or user login. Task Scheduler retries a failed process up to ten times, one minute apart. This setup does not restart or reconfigure Open WebUI, Ollama, or GPU Watch.

The task uses a persistent private access token in `C:\ProgramData\CWAI\FeedbackDashboard\access-token.txt`, outside Git. The installation script creates the directory with access limited to Local System and administrators. It does not print the token. The browser link is stable across task restarts and logout; keep that link private because it contains the token. The dashboard reads `C:\downloads\open-webui\data\webui.db` without modifying it.

## First setup

1. On the AI server, update a clean Git checkout to the reviewed commit containing `Run-CW-Feedback-Dashboard.py` and `Manage-CW-Feedback-Dashboard.ps1` under `E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor`. Do not copy the database or token into Git.
2. If a manually started feedback dashboard is listening on port `8789`, stop **that** Python process with Ctrl+C in its PowerShell window. Leave GPU Watch, Open WebUI, and Ollama running.
3. Open **Windows PowerShell as Administrator** on the AI server and run this full-path command from any directory:

   ```powershell
   & 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Manage-CW-Feedback-Dashboard.ps1' -Mode Install
   ```

   The script first tries the Python executable used by `CWAI-GPU-Dashboard`. If it cannot find a real Python 3 executable, supply its full path with `-PythonPath 'C:\path\to\python.exe'`. Installation refuses to replace an existing task or take over an occupied port.

4. In the same elevated interactive PowerShell, open the private page:

   ```powershell
   & 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Manage-CW-Feedback-Dashboard.ps1' -Mode Open
   ```

   Verify the feedback and usage pages load real data. You can bookmark the private browser URL on the AI server. Closing PowerShell or logging out will not stop the Scheduled Task. Startup after an OS reboot still needs observation; do not reboot the server merely to test this.

## Check and update

The read-only status check works from any PowerShell directory:

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Manage-CW-Feedback-Dashboard.ps1' -Mode Status
```

After reviewing and pulling a code-only update, reload only this dashboard with an **elevated** PowerShell:

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Manage-CW-Feedback-Dashboard.ps1' -Mode Restart
```

The restart preserves the private link. Git pull alone does not reload the running Python process. If the task reports an error, inspect `Get-ScheduledTaskInfo -TaskName 'CWAI-Feedback-Dashboard'` and confirm the task account can read the Open WebUI database. Keep the token file, database, and runtime data outside Git. The Ollama request page can read existing general-service access logs without an Ollama restart; model and token fields, and private-service requests, still require a separate recorder.
