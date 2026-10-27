# Ollama HTTP 500 email alert

This monitor reads the existing metadata-only recorder files for ports 11434 and
11435 every two seconds. It sends one email for the first observed HTTP **500**
on each listener in an incident. It does not change Ollama, the proxy, or
Open WebUI. Other 5xx statuses are not triggers.
The instrumented Ollama recorder must be active and able to write to
`E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage`; a 500 generated only by the
proxy or Open WebUI will not appear in these recorder files.

The email includes time, listener, route, model, caller label, source IP,
recorded token counts, up to ten preceding request metadata rows, and a RAM/GPU
snapshot when available. It contains no prompt, answer, or credential. A 500
alone does not prove a memory problem. Recorder events are written only after a
request completes, so detection follows the completed request and Resend delivery
adds its own delay. A persisted Resend idempotency key limits duplicate emails
when a delivery is retried within Resend's 24-hour window.

## Resend information needed

Use an approved Resend account. The account owner needs to provide, on the AI
server only:

- A Resend API key with send permission and a verified sender address/domain.
- The sender and recipient email addresses.
- HTTPS access from the AI server to `https://api.resend.com`.

Do not place a password in Git. Store the real configuration at
`E:\AI\ollama\OpenWebUI-Monitoring\Ollama500Alert\config.json` on the AI server,
outside the Git checkout. The example
below uses placeholders and must be filled with approved values:

```json
{
  "resend_api_key_file": "E:\\AI\\ollama\\OpenWebUI-Monitoring\\Ollama500Alert\\resend-api-key.txt",
  "sender": "ai-alerts@example.invalid",
  "recipients": ["operator@example.invalid"],
  "rearm_mode": "quiet",
  "quiet_minutes": 30
}
```

Put the Resend API key alone in the referenced file on the AI server, with access
limited to SYSTEM and administrators. Protect the whole config/state directory
with the same access policy; it also contains alert reports and logs. The source
checkout must not contain these files. The existing LiteLLM report on another
server is independent and does not need to be accessed or changed.

If using the same Resend account as that report, its `email_from` value maps to
`sender`, its `email_to` list maps to `recipients`, and its `resend_api_key` value
belongs in the protected `resend-api-key.txt` file. Move the key through an
approved secure channel; never paste it into a ticket, chat, command history, or
the Git checkout. A separate send-only key for the AI server is preferable.

`rearm_mode` controls when a new incident can email:

- `quiet` (default): a new 500 emails after the configured number of minutes
  without a 500 on that same listener.
- `success`: a successful request on that listener rearms it after delivery.
- `manual`: the first 500 on each listener emails once until state is reset by
  an administrator.

The first monitor start records the current ends of recorder files. Earlier
errors are not emailed. The state file at
`E:\AI\ollama\OpenWebUI-Monitoring\Ollama500Alert\state.json` keeps the read position,
incident state, and unsent email across monitor restarts. If Resend fails, the
monitor retries the pending alert no more often than once per minute. Check
`alert.log` in the same directory for send failures. The log never records the
API key.

After placing and protecting the configuration, validate it without sending:

```powershell
python -B 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\ollama_500_alert.py' --check-config
```

An explicit delivery test sends one harmless test email:

```powershell
python -B 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\ollama_500_alert.py' --test-email
```

Install the independent startup task from elevated PowerShell:

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\OpenWebUi Monitor\Manage-CW-Ollama-500-Alert.ps1' -Mode Install
```

Check it with `-Mode Status`. The task is named `CWAI-Ollama-500-Alert` and
only this monitor is started or restarted by that script. It does not restart
the dashboard, either Ollama service, or Windows.
