# Review one Open WebUI chat

## Windows export UI

On the Open WebUI server, double-click `Open-Chat-Export-UI.cmd` in this folder.
Paste one complete `/c/` or `/s/` chat link and click **Encrypt and push this chat**.
The UI checks that the Git checkout is clean and points to the expected GitHub
repository, fast-forwards `main`, exports only the selected chat through the
same script as the terminal workflow, encrypts it, stages only the new `.p7m`
file, commits, and pushes. It shows the new commit ID for the reviewer.

The export includes saved chat messages and metadata, but not attachment file
contents. The GitHub repository is public and keeps encrypted files in commit
history even after a normal delete. The UI never commits plaintext or the
decryption key. If a step fails after encryption, the status names the local
encrypted file; it does not retry or commit other files automatically.

For LLM review, order reconstruction, evidence rules, and the required
end-of-review deletion question, read [AGENTS.md](AGENTS.md).

`Export-One-Chat.ps1` asks for one chat link or ID, reads only that chat from the
Open WebUI SQLite database, and saves an encrypted `.p7m` file in this folder.
It does not copy the database or other chats. The temporary plaintext JSON is
removed after encryption. Attachment metadata may be present; attachment file
contents are not exported.

The repository at `rbadami-glitch/Ai-server` is public. Never commit plaintext
chat JSON, chat screenshots, database files, or a private decryption key here.
`review-public.cer` contains only the public encryption certificate. The
matching private key remains in the reviewer's local Windows certificate store.

The established server checkout is `E:\AI\ollama\OpenWebUI-Monitoring\github`.
The exporter creates a temporary plaintext file beside the script, encrypts it,
and removes the plaintext file immediately afterward.

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\openwebui logs\Export-One-Chat.ps1'
```

Paste the exact `/c/` or `/s/` chat link when asked. The script prints the path
to the encrypted file. Inspect `git status` and stage **only that `.p7m` file**
before committing and pushing **only if encrypted transfer through this public
repository is intended**. Git history can retain an encrypted export after
normal deletion. Prefer an approved private transfer route for future chats.
