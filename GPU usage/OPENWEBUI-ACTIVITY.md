# Open WebUI activity in GPU Watch

The **Open WebUI chat activity** table reads saved assistant-response metadata from `C:\downloads\open-webui\data\webui.db` on the AI server. The operator confirmed that file exists. The dashboard opens SQLite with `mode=ro` and `query_only=ON`. It selects creation time, model ID and saved usage only; it does not load prompt or answer text.

The table follows the GPU graph's selected range and refreshes every 15 seconds. It shows the newest 500 saved assistant responses in that range; prompt rows are omitted because they do not carry model or usage data in this view. Token counts appear only when Open WebUI saved them on a response. A missing count is shown as **Not recorded**, not zero. The model column is Open WebUI's saved model ID and may be a public alias; it does not prove which Ollama model actually ran.

These rows are **saved chat messages**, not a complete HTTP request log. Failed, canceled, unsaved, direct Ollama, LiteLLM, and other clients' requests may be absent. A GPU memory spike can be model loading or another process; it cannot be assigned to an Open WebUI row from time proximity alone. This dashboard does not add a recorder, change routes, or restart Ollama.

After a code update, restart only the `CWAI-GPU-Dashboard` scheduled task. Verify `http://127.0.0.1:8791/api/openwebui-activity?start=<ISO-time>&end=<ISO-time>` responds and that the page displays the table. If the task account cannot read the database or its schema differs, the table shows a read error while the GPU monitoring remains available.
