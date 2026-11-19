# Document fidelity and Open WebUI eval backlog

Status: recorded on 2026-11-19. This is a plan, not a deployed fix. Keep customer documents, extracted text, screenshots, chat exports, reports, and credentials off this public repository.

## Observed document issue

- A user attached a legacy Word `.doc` template and requested an editable LaTeX version with the original styling.
- Open WebUI displayed `Retrieved 1 source`. The model produced LaTeX text in chat, but the user reported an unformatted template.
- In a follow-up, the model explicitly said it only had extracted text and could not see the original document or rendered pages. This is consistent with Open WebUI 0.13.4 routing `.doc` files through `UnstructuredLoader` and using extracted content for RAG.
- The earlier offline spaCy fix restored *text extraction* for legacy `.doc` files. It did not add access to original page layout, images, named styles, or file conversion.
- The actual customer document has not been inspected here. Whether it contains scanned images, and how accurately its layout can be reproduced, remain unverified.

## Work now: document conversion and visual input

1. Inspect a private server-local copy of the original upload without moving it into Git. Record only format, page count, images, tables, styles, and extraction status in diagnostics. Determine whether OCR is needed for any image-based text.
   `diagnostics/locate_chat_upload.py` is the read-only first step: it finds the attached file record and reports its location and binary signature without printing chat or document content.
2. Build a separate, opt-in conversion path for document transformation requests. Keep ordinary document Q&A on its existing RAG path. The conversion path must read the original file bytes rather than only retrieved text chunks.
3. For `.doc`, use an offline local office converter to create `.docx` for structural inspection and PDF for a visual reference. Extract headings, tables, headers, footers, page settings, images, and named styles. Apply OCR only to scanned pages or embedded images that contain text.
   `diagnostics/inspect_word_layout.py` reports content-free counts from the converted `.docx`; it is a triage tool, not a conversion-fidelity score.
4. Generate a downloadable `.tex` file with any required image/assets directory. Compile it, compare rendered pages against the source PDF, and report unsupported or uncertain formatting instead of claiming an exact match.
   `openwebui-tools/word_to_latex.py` is an offline draft prototype for this step. It packages `template.tex`, copied images, and `conversion-report.json` in a ZIP using Python's standard library. It is not connected to Open WebUI and has not passed a rendered-page comparison. The report explicitly marks style, table, and section limitations.
5. Prototype with a synthetic public fixture, then validate against the customer file privately on the server. Keep the current production service untouched until the workflow has passed those checks. Rollback should disable the conversion tool without altering the existing RAG path.

Open WebUI's built-in document extraction, including layout-aware engines, is aimed at giving the model retrievable content. It does not by itself guarantee a faithful Word-to-LaTeX conversion or a downloadable `.tex` artifact. Conversion tools can also lose complex Word formatting, so rendered-page review is a release gate.

## Deferred eval work

The existing `evals/OpenWebUI-Live-Reasoning-Run.py` sends synthetic text prompts but does not grade the answers. Extend evaluation to the complete user workflow with synthetic fixtures and explicit pass/fail criteria:

| Area | Required check |
| --- | --- |
| File ingestion | Upload `.doc`, `.docx`, text PDF, scanned PDF, and image-heavy samples; verify extracted text, processing status, and source access. |
| Layout fidelity | Compare rendered output to a known styled fixture for headings, tables, images, headers, and pagination; require visual review for release. |
| File generation | Verify a real downloadable file exists, opens, and contains the requested content; fail claims of success without an artifact. |
| Retrieval | Check known facts, citations, missing evidence, long documents, and separation between users. |
| Reliability | Check long replies, interrupted streams, retries, worker restarts, and timeouts. |
| Exports | Check CSV/PDF content, filenames, download completion, and visible progress. |

Run a short synthetic suite before deployment and a broader scheduled suite. Store model answers and reports outside Git. Reuse real client reports only through an approved private route and remove identifying content from permanent fixtures.

## Separate service durability work

The offline spaCy model and NSSM worker-shutdown rollout remains pending. On 2026-11-19, diagnostics found two running services using the same launcher: `OpenWebUIService` owned port 80 while `OpenWebUI` was also running; `CWAI-OpenWebUI` was stopped. The first activation rolled back its launcher and settings, and the website answered health checks. The duplicate-service issue stopped the next staging attempt. Do not run the old activation steps until service ownership is reconciled. This service work was deferred at the user's request and is separate from document fidelity.
