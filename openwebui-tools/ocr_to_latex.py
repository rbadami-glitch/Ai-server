"""Offline OCR text draft for scanned PDFs with an explicit tessdata directory."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from word_to_latex import escape


def convert(source: Path, output: Path, tessdata: Path, *, language: str = "eng") -> dict:
    if output.exists():
        raise FileExistsError("Refusing to overwrite an OCR output directory")
    if not (tessdata / f"{language}.traineddata").is_file():
        raise ValueError("Offline OCR language data is missing")
    if language != "eng":
        raise ValueError("Only the verified English OCR model is supported")
    output.mkdir(parents=True)
    blocks = []
    characters = 0
    recognized_characters = 0
    ocr_pages = []
    pages_without_text = []
    with pymupdf.open(source) as document:
        if not document or len(document) > 20:
            raise ValueError("Scanned PDF must contain 1 to 20 pages")
        for index, page in enumerate(document):
            source_blocks = page.get_text("blocks")
            use_ocr = not any(block[6] == 0 and block[4].strip() for block in source_blocks)
            if use_ocr:
                textpage = page.get_textpage_ocr(
                    language=language, dpi=200, full=True, tessdata=str(tessdata))
                source_blocks = page.get_text("blocks", textpage=textpage)
                ocr_pages.append(index + 1)
            page_blocks = []
            for block in source_blocks:
                if block[6] == 0 and block[4].strip():
                    content = block[4].strip()
                    characters += len(content)
                    if use_ocr:
                        recognized_characters += len(content)
                    page_blocks.append(escape(content).replace("\n", " "))
            if not page_blocks:
                pages_without_text.append(index + 1)
            if index:
                blocks.append(r"\clearpage")
            blocks.append(f"% OCR from source page {index + 1}; verify every word.")
            blocks.extend(page_blocks or [r"\mbox{}"])
        page_count = len(document)
    if recognized_characters < 10:
        raise ValueError("OCR found too little text to create a useful draft")
    preamble = r"""% OCR text draft. Recognition and formatting require human review.
\documentclass[10pt,letterpaper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{fontspec}
\setmainfont{Arial}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{8pt}
\begin{document}
"""
    (output / "template.tex").write_text(
        preamble + "\n\n".join(blocks) + "\n\\end{document}\n", encoding="utf-8")
    return {"status": "ocr_text_draft_requires_human_review",
            "source_pages": page_count, "recognized_characters": recognized_characters,
            "total_characters": characters, "ocr_pages": ocr_pages,
            "pages_without_text": pages_without_text, "language": language,
            "visual_fidelity_verified": False, "ocr_verified": False}
