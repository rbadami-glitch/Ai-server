# General document-to-LaTeX rollout

## Product contract

Users attach a DOC, DOCX, or PDF and ask for LaTeX in ordinary chat. They do not select a tool. The result must say which output was actually produced:

- **Editable reconstruction:** paragraphs, tables, and pictures can reflow. Visual details can differ from the source. Give a compiled preview and a project archive.
- **Visual draft:** page appearance is checked against the source. Words have fixed positions; longer edits can overlap. Give a compiled preview and a project archive.
- **Cannot convert reliably:** give the specific limit and no success claim.

An arbitrary input cannot be guaranteed to be both visually identical and freely reflowable. The user-facing response must not call a fixed-position draft an easy-to-edit template.

## Current evidence

The existing visual route passed a synthetic DOC, synthetic PDF, and one private five-page DOC with text and image placement checked against a PDF reference. A private 30-page DOC with mixed page sizes exceeds its current limits. The semantic Word prototype renders headings, body text, a table, and a header image; a synthetic 25-page DOCX also rendered to 25 pages. When the visual route rejects that synthetic document for page count, the experimental Word fallback returns a 25-page editable draft with complete text coverage. However, a private five-page evaluation had minimum page ink overlap near 0.05 despite 0.99 text coverage. The semantic route is therefore behind an explicit experimental flag and is not enabled for the shared model.

A synthetic scanned PDF was recognized locally using PyMuPDF and a separately supplied offline English Tesseract language file. The OCR draft rendered with the expected text, but OCR accuracy and layout remain unverified. The language file is not in this public source repository. The server must obtain and verify it separately before an OCR trial.

The Tool now stores a verified project ZIP and separately attempts a direct preview-PDF upload. If preview storage fails, the verified project ZIP remains available and contains the preview. Both downloads are checked against the requesting user's ownership before being returned.

The private test model exposed a separate failure: after a successful conversion Tool call, the language model wrote an unrelated inline LaTeX template and claimed it was easy to edit. The two verified file cards were present, but the model-authored prose was misleading. `verified_latex_reply_filter.py` is a private-model outlet guard that replaces the final prose with the exact Tool result whenever this Tool ran. It preserves the Tool's file cards. Test this guard on the private model before considering wider use. It does not guarantee that the model will call the Tool for every conversion request; that still needs a routing solution and evaluation.

## Required gates before shared-model rollout

1. Build synthetic evaluation documents for short and long DOC/DOCX, mixed orientation, headings, lists, merged/nested tables, images, headers/footers, non-ASCII text, and malformed/oversized files. Include selectable and scanned PDFs.
2. Run the visual and editable routes independently. A failure in one route must not suppress a verified result from the other.
3. Measure text coverage, page count, image presence, and visual similarity. Review each supported case manually and document unsupported features. Treat OCR text as uncertain and require a verified offline OCR dependency.
4. Verify file ownership and isolation for two different users. Ensure each user can download only their own outputs; test upload and worker failures.
5. Produce a direct preview PDF and a separately labeled LaTeX project ZIP. Check the project compiles after ordinary edits, including longer text and image replacement.
6. Test ordinary document questions on the same shared model. The converter must run only for conversion requests, and a failed tool call must not be described as success.
7. Deploy with a versioned isolated worker, existing configuration backup, canary users, health check, and a rollback that restores the prior Tool/model configuration without restarting unrelated services.

## Current deployment state

The Open WebUI pilot Tool is attached only to a private test model. This source branch is not a production rollout. The public source repository must contain no customer documents, extracted text, previews, chat logs, or credentials.
