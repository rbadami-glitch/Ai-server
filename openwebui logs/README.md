# Review one Open WebUI chat

`Export-One-Chat.ps1` asks for one chat link or ID, reads only that chat from the
Open WebUI SQLite database, and saves an encrypted `.p7m` file in this folder.
It does not copy the database or other chats. The temporary plaintext JSON is
removed after encryption. Attachment metadata may be present; attachment file
contents are not exported.

The repository at `rbadami-glitch/Ai-server` is public. Never commit plaintext
chat JSON, chat screenshots, database files, or a private decryption key here.
`review-public.cer` contains only the public encryption certificate. The
matching private key remains in the reviewer's local Windows certificate store.

On the Open WebUI server, after a clean fast-forward pull of the reviewed code:

```powershell
& 'E:\AI\ollama\OpenWebUI-Monitoring\github\openwebui logs\Export-One-Chat.ps1'
```

Paste the exact `/c/` or `/s/` chat link when asked. The script prints the path
to the encrypted file. Inspect `git status` and stage **only that `.p7m` file**
before committing and pushing. The reviewer can pull it and decrypt locally.
