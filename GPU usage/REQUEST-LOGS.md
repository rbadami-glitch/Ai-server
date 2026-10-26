# Ollama request records in GPU Watch

**Current decision:** Keep this dashboard read-only. Do not build or deploy an Ollama request recorder, change client routes, or restart Ollama for request logging. The request table shows records only if a separately authorized source already supplies them.

GPU Watch now has a **Recent Ollama requests** table. It is a read-only view. It does not intercept API traffic or create request records. Until a recorder is deployed on both Ollama ingress listeners, the table must show that no recorder files were found. Zero visible rows is not evidence of zero requests.

The reader expects daily UTF-8 JSONL files outside Git:

```text
E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage\ollama-11434-YYYY-MM-DD.jsonl
E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage\ollama-11435-YYYY-MM-DD.jsonl
```

Dates are UTC. Each completed request record uses schema `1` and contains `at` (Unix completion time in seconds), `listener` (`"11434"` or `"11435"`), `route`, `model`, `source_ip`, `status`, and optionally `prompt_tokens`, `completion_tokens`, `total_tokens`, and `duration_ms`. The page never returns other fields from a record. Missing token or duration values appear as **Not recorded**. A recorder can report loss with `{"schema":1,"kind":"dropped","listener":"11434","at":<Unix seconds>,"dropped":<count>}`.

The page shows at most 200 recent rows from the selected last hour, 24 hours, or seven days. It reads only the last 4 MiB of each applicable file and flags partially read files, invalid records, and reported drops. These limits keep the dashboard responsive, so the table is a recent view rather than a complete log browser. Preserve the JSONL files on the AI server according to a separately approved retention policy; never commit them.

## Why the table may be empty

The current AI server has a general Ollama listener at `11434` and a private one at `11435`. The `18002` proxy and the Open WebUI database do not see every request to both listeners. Ollama's `/api/ps` shows loaded models, not request history. Response usage fields can provide token counts for completed inference calls, but they need to be captured at the Ollama ingress points. Some failed or interrupted calls may have no token counts; they must still have a request record with null counts.

There is no request recorder in this dashboard, and none is deployed by this change. The current Ollama service logs and `/api/ps` do not provide a complete per-request model and token history. Consequently, the table can remain empty even while models are serving traffic. It must never present an empty table as proof that no requests occurred. No Ollama service is changed by this dashboard update.
