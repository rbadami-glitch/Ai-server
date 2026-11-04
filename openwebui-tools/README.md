# Word document tool

`word_document.py` is a narrow Open WebUI Workspace tool for creating a basic `.docx` from a title and document text. It uses only Python's standard library for document creation. It does not run shell commands or install packages.

The tool was reviewed against Open WebUI 0.12.5 source and passed local package checks. A live Open WebUI test, browser download test, and Microsoft Word opening test are still required. Its presence in this Git checkout does not install or enable it in Open WebUI.

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
