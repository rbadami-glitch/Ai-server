# Open WebUI chat review instructions

This file applies to reviews of a **specific Open WebUI chat**. When the user
says "analyze the chat", begin the review with the chat link, ID, or newest
unambiguously identified export already supplied in the conversation. If more
than one chat could be meant, ask which one. Do not scan other users' chats.

## Handle the export

- This repository's GitHub origin is **public**. Never commit plaintext chat,
  screenshots, a database, credentials, or a private decryption key. An
  encrypted `.p7m` file can still remain in Git history after normal deletion;
  do not call the repository private or promise complete erasure.
- If the user has already provided an export, pull with `--ff-only` from a
  clean checkout, identify the exact new commit and file, and analyze only
  that file. Do not ask for permission to perform the requested read-only review.
- Prefer in-memory decryption. Keep full decrypted text out of Git, shell
  history, command output, screenshots, reports, and work logs. If a temporary
  plaintext file is unavoidable, keep it outside Git, restrict access, remove
  it after processing, and disclose that action. Retain the encrypted export
  until the user answers the deletion question below.
- Do not push another chat export to the public repository by default. Use an
  approved private transfer route for future chats unless the user knowingly
  requests encrypted transfer through this public origin.

## Analyze in conversation order

1. Confirm the selected `chat.id` or `share_id`, title, message count, and
   whether the export contains attachment **contents** or only metadata.
2. Rebuild the visible path from `current_message_id` and each message's
   `parent_id`. `chat_message.id` may be prefixed with `chat.id` while
   `parent_id` is bare; normalize before matching. Inspect branches separately.
   Do not trust `ORDER BY created_at, id` as the conversation order.
3. Pair every user message with its actual assistant child. Record the
   per-message `model_id`; one chat can switch models. Do not infer who changed
   the model selector without evidence.
4. **Present a turn-by-turn table first**: user request, assistant/model, saved
   reply or lack of reply, and any specific issue. Include the final turns and
   repeated attempts. Then summarize patterns and prioritized improvements.
5. Compare `chat_message` rows with the chat JSON snapshot for apparent
   missing or partial replies. Distinguish **saved in the database** from
   **visible in the browser**. A `done` flag does not prove the user saw a
   complete answer. An error written as answer text can have `error = null`.
6. Check file/source counts, tool outputs, usage and timestamps before saying
   a tool, RAG, or data source was used. Saved timestamps are approximate
   evidence of wait time, not necessarily full user-visible latency.

## Make claims carefully

- Separate **observed**, **inferred**, and **unverified** findings. Quote only
  short excerpts needed to explain a defect; mask invoice numbers, personal
  details, tokens, and internal URLs in the review.
- A model's statement that an invoice is absent from a knowledge base is not
  evidence that a knowledge base was searched. Verify attachments and source
  records. No source record means no source was saved, not proof that no
  backend lookup occurred.
- `Processed`, `Paid`, and `payment-closed` are different fields or states.
  Do not equate them without a validated business definition. `No rows
  returned` does not prove an invoice does not exist.
- Do not claim a Wren result is correct without the generated query, source
  rows, authorization context, and data freshness. Do not generalize one chat
  to all users or attribute Wren/coding-assistant behavior to Curtiss-Wright.
- If the browser appears blank but the export has a saved reply, state both
  facts and request UI evidence before diagnosing the display failure.

## Required ending and deletion

Finish **every** chat review by asking the user whether to delete that chat's
retained export. Do not delete it merely because the review is complete, and
do not treat silence as approval. The question must name the exact export and
the places where it is retained. If the user says to delete, remove the local
and server working copies that are accessible, and verify their absence.
Normal `git rm` plus a new commit does **not** erase the export from public
GitHub history. Before any history rewrite, force push, or wider purge, show
the concrete affected commits/branches and obtain explicit authorization.
State honestly what copies or backups cannot be verified or removed.
