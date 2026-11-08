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
   the model selector without evidence. Establish the **intended model for the
   task** before assigning blame. In this deployment, Wren AI is the intended
   assistant for live finance/ERP questions; Curtiss-Wright and the coding
   assistant are not expected to answer those questions. If they receive such
   a question, classify it as wrong-model selection and assess their handoff
   behavior separately from Wren's answer quality.
4. **Present a turn-by-turn evaluation first**: user request, assistant/model,
   intended versus wrong model, what the reply supplied, whether the user's
   task was answered, the saved Open WebUI evidence, and the specific fix or
   follow-up test within the user's ownership. Include the final turns and
   repeated attempts.
   Immediately after the table, add **Causes and fixes** pointers covering
   every turn number. For each pointer, separate a confirmed cause from a
   plausible cause, say what evidence would decide, and give a specific fix.
   Group turns only when their cause and fix truly match. Then summarize
   patterns and prioritized improvements.
5. Compare `chat_message` rows with the chat JSON snapshot for apparent
   missing or partial replies. Distinguish **saved in the database** from
   **visible in the browser**. A `done` flag does not prove the user saw a
   complete answer. An error written as answer text can have `error = null`.
6. Check file/source counts, tool outputs, usage and timestamps before saying
   a tool, RAG, or data source was used. Saved timestamps are approximate
   evidence of wait time, not necessarily full user-visible latency.

## Grade every answer against the user's need

- Give a **task-result** verdict for each turn: `Answered`, `Partly answered`,
  or `Not answered`. A truthful statement that the model lacks ERP access is
  still `Not answered` when the user asked for an invoice status. Assess
  whether Open WebUI routed, saved, and displayed the response properly.
- Factual verification of invoice values is outside this review unless the
  user specifically requests it. Mark data values `not assessed` rather than
  investigating ERP truth, assigning a correctness score, or asking for ERP
  records. A plausible table or `done` flag never proves factual correctness.
- Grade against the user's **actual question in that turn**, using prior
  context only where it clearly carries forward. If a user asks for "status"
  and a `taskstatus` value is returned, classify it as `Answered` unless the
  business definition or explicit wording shows that a different status was
  required. Missing source, timestamp, or identifiers can make the answer
  hard to verify without making it `Partly answered`. Reserve `Partly answered`
  for an explicitly requested component that is missing or a demonstrably
  truncated answer. Record verification and UX weaknesses separately.
- Also record **routing:** `Intended model` or `Wrong model`. A truthful
  no-access response from a wrong model can be appropriate model behavior
  while the user's task remains unanswered. Do not count that response as a
  Wren failure or recommend giving the wrong model ERP access. For wrong-model
  turns, the product fix is a clear model label, a finance-task warning, or an
  easy switch to Wren that preserves the user's question.
- Identify unsupported claims separately. For example, saying an invoice was
  absent from an attached knowledge base is wrong if the model had no attached
  knowledge base, even when its lack of ERP access is true.
- For each `Partly answered` or `Not answered` turn, state the precise failure
  type (no data access, timeout, ambiguous match, missing fields, empty
  result, display problem, etc.), the smallest credible fix, and a test that
  would prove the fix worked. Do not propose model training for live-changing
  data that requires a current, authorized system query.
- In the **Causes and fixes** section, never promote a symptom into a root
  cause. A Wren `GENERATING` timeout proves the job did not finish by its
  deadline; it does not prove whether model generation, SQL, queueing, or the
  database was slow. Missing output columns prove only what the user received;
  inspect generated SQL and raw rows before blaming query generation.
- Summarize repeated failures as an issue list with priority and acceptance
  criteria. Focus on Open WebUI-owned model selection, pipe formatting,
  response/error handling, logging, and browser display. For Wren-owned query
  or data issues, collect the task ID and evidence for the owning team; do not
  propose changing their service as this user's fix.
- If the user says an issue is already fixed, label it historical and stop
  recommending a fix for it unless current evidence shows a regression.

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
  returned` establishes only that Wren presented an empty result. It does not
  prove an invoice does not exist or reveal whether supplier matching, query
  generation, data coverage, permissions, or filtering caused the empty result.
  Inspect the Open WebUI pipe output and the saved Wren task trace before
  naming a cause. If Wren supplied an empty result, pass its task ID to the
  Wren owner rather than attempting to fix their query in Open WebUI.
- Do not claim a Wren result is factually correct without evidence, and do not
  fact-check Wren's invoice data unless asked. Do not generalize one chat to
  all users or attribute Wren/coding-assistant behavior to Curtiss-Wright.
- If the browser appears blank but the export has a saved reply, state both
  facts and request UI evidence before diagnosing the display failure.

## Required ending and deletion

Finish **every completed** chat review, including answer-level grading and
requested corrections, by asking the user whether to delete that chat's
retained export. Do not ask after a preliminary summary while the review is
still being corrected. Do not delete it merely because the review is complete,
and do not treat silence as approval. The question must name the exact export and
the places where it is retained. If the user says to delete, remove the local
and server working copies that are accessible, and verify their absence.
Normal `git rm` plus a new commit does **not** erase the export from public
GitHub history. Before any history rewrite, force push, or wider purge, show
the concrete affected commits/branches and obtain explicit authorization.
State honestly what copies or backups cannot be verified or removed.
