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
When the visual ZIP also contains a reflowing Word draft, the Tool now identifies `editable.tex` and `editable-preview.pdf` explicitly and says that the editable version's appearance was not verified. An inline LaTeX block written by the model is not an evaluated deliverable.
The first reply guard version did not alter the private model's answer. A content-free inspection of three recent saved assistant messages found only `reasoning` and `message` output items, with no native `function_call_output`. The guard now also checks Open WebUI's legacy Tool-result citation source. The updated guard still needs an end-to-end private-model check before it can be credited as working.
The legacy-source guard passed a private chat canary: the final answer exactly matched the verified Tool result, while an ordinary question about an attached synthetic document answered without creating conversion files. The five-page private Word document's reflowing preview then showed severe overlapping text and tables. A conservative rendered-text collision check withheld that reflowing alternative in a server preflight and reported `editable_text_overlap`; the fixed-layout result still passed its visual and text comparisons. This gate prevents a broken draft from being presented as editable; it does not reconstruct complex Word layout.
The private canary then exposed a streaming UX defect: the model's unverified LaTeX code appeared during generation and disappeared when the outlet guard replaced the completed answer. The guard now has a stream handler that substitutes the verified legacy Tool result for the first model text delta and suppresses later model text deltas, while preserving completion and error events.

The private live stream check passed: the model showed the verified Tool reply throughout generation and no transient inline LaTeX code block appeared. A separate ordinary document question still answered without conversion. The private five-page visual preview remained close to the source; the optional reflowing draft remained withheld because its text collided. The current canary therefore validates reply integrity and the visual route, but does not yet satisfy the requested easy-to-edit, appearance-preserving template for complex documents. Keep the filter and converter confined to the private test model while semantic layout quality and broader document evaluations remain open.

The reflowing Word draft's first failure came from flattening a complex Word header into a LaTeX running header with a section command and insufficient reserved space. The updated generator treats running-part paragraphs as header text and reserves page space when a header contains an image. An isolated local run on the private five-page converted DOCX rendered five pages with text collision counts `[0, 0, 0, 0, 0]`, 0.99 text coverage, the image present, and a header on each page. The editable version is now eligible as a **separate** draft. Its minimum ink overlap with the source was only 0.032, so its appearance is materially different and must not be presented as a matching visual template. The original visual draft remains the appearance-preserving output. The editable ZIP also includes an editing guide with a picture insertion example.

The private Open WebUI canary produced both verified downloads and an editable draft after the server updated to the new converter. The chat remained active for roughly two minutes after the verified reply appeared, then completed normally. In Legacy function calling, Open WebUI still runs a model completion after the Tool; the stream guard hides that model text but does not end generation. This is an outstanding latency issue for rollout. Review of the editable preview also found that cached Word `PAGE` and `NUMPAGES` values repeated on every page. The generator now translates those Word fields into LaTeX page references without an extra TeX package. It preserves a simple Word header table as LaTeX cells and places a leading picture beside accompanying text. A local five-page integration render has sheet numbers 1 through 5, 0.983 text coverage, and no detected text collisions; an edit that creates six pages shows 6 of 6 on the last page, also without collisions. Word sections with different headers are still represented by the first header only. The reflowing draft's appearance still needs human review and is not the visual-fidelity output.

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
