"""Report content-free structural counts from a locally converted DOCX.

The source document and converted files must remain outside the public Git
checkout. This tool reads the DOCX ZIP package and prints no document text.
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def inspect(path):
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise ValueError("Expected an existing DOCX ZIP package")
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX has no word/document.xml")
        document = ET.fromstring(package.read("word/document.xml"))
        styles = ET.fromstring(package.read("word/styles.xml")) if "word/styles.xml" in names else None
        media = [name for name in names if name.startswith("word/media/") and not name.endswith("/")]
        headers = [name for name in names if re.fullmatch(r"word/header\d+\.xml", name)]
        footers = [name for name in names if re.fullmatch(r"word/footer\d+\.xml", name)]
        paragraphs = list(document.iter(W + "p"))
        style_refs = {
            item.attrib.get(W + "val")
            for item in document.iter(W + "pStyle")
            if item.attrib.get(W + "val")
        }
        style_refs.update(
            item.attrib.get(W + "val")
            for item in document.iter(W + "rStyle")
            if item.attrib.get(W + "val")
        )
        body_text_chars = sum(len(item.text or "") for item in document.iter(W + "t"))
        return {
            "source_kind": "converted_docx",
            "body_paragraphs": len(paragraphs),
            "body_text_characters": body_text_chars,
            "tables": sum(1 for _ in document.iter(W + "tbl")),
            "table_cells": sum(1 for _ in document.iter(W + "tc")),
            "section_properties": sum(1 for _ in document.iter(W + "sectPr")),
            "style_definitions": sum(1 for _ in styles.iter(W + "style")) if styles is not None else 0,
            "referenced_styles": len(style_refs),
            "header_parts": len(headers),
            "footer_parts": len(footers),
            "embedded_media_files": len(media),
            "ocr_assessment": "inspect_media_visually" if media else "no_embedded_media_detected",
            "limits": [
                "Counts do not prove the original document's visual fidelity survived conversion.",
                "Embedded media may be logos, charts, or scanned text; OCR need requires visual inspection.",
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.docx), indent=2))


if __name__ == "__main__":
    main()
