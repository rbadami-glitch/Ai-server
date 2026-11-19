# Word document tool

## Fixed-layout PDF to LaTeX prototype

`pdf_to_latex_layout.py` recreates selectable PDF text, embedded PNG/JPEG images,
and simple line/rectangle drawings as fixed-position LaTeX elements. It uses
PyMuPDF to read PDFs and requires XeLaTeX or Tectonic plus Arial to render. Keep
inputs and outputs outside this public Git checkout. It refuses PDFs with no
selectable text; scanned text needs OCR first.

Each word is editable in `template.tex`, but longer edits do not reflow and may
overlap neighboring content. This is a visual copy, not a semantic Word template.
Compile the draft, compare its PDF with the reference using
`diagnostics/compare_pdf_layout.py`, and inspect every page. The source script
has passed one synthetic and one private layout check; it is not installed in
Open WebUI and should not be enabled there without broader tests and a clear
user-facing description of the fixed-layout limitation.

## Downloadable Word document tool

`word_document.py` is a narrow Open WebUI Workspace tool for creating a `.docx` from a title and document text. It uses only Python's standard library for document creation. It does not run shell commands or install packages.

The document uses Word paragraph styles for its title, headings, and body text. The tool recognizes Markdown-style `#`/`##` headings, `-` bullets, `1.` numbered items, and `**bold**` spans. It does not support company templates, images, tables, or arbitrary Markdown layout.

The upload path was tested with Open WebUI 0.12.5 in a private chat, and the browser downloaded a `.docx`. The improved formatting has only local package checks so far. Microsoft Word opening and visual review are still required. Its presence in this Git checkout does not install or enable it in Open WebUI.

## Local check

From the repository root:

```powershell
python -B -m unittest -v tests/test_word_document_tool.py
```

## Private model trial

1. Copy the contents of `word_document.py` into a new Workspace tool in Open WebUI. Keep its access private.
2. Enable that tool only on the private test model. Do not enable the broad Code Execution tool to create Word files.
3. In a new test chat, ask: `Create a Microsoft Word document about the benefits of teamwork that I can download.`
4. Verify that a real file card or link is shown, downloading returns a `.docx`, and Microsoft Word opens it with the requested text. Also verify that a failed tool call does not produce a success claim.

The tool returns a download link only after Open WebUI has stored the file, verified its ownership, and checked that the stored bytes match the generated document. The tool cannot independently prevent a model from claiming success if the model never calls it; that behavior needs a separate output check.
