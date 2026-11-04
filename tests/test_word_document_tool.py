"""Local checks for the Word document tool draft; no Open WebUI calls."""

import asyncio
import importlib.util
import io
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


TOOL_PATH = Path(__file__).resolve().parents[1] / "openwebui-tools" / "word_document.py"
SPEC = importlib.util.spec_from_file_location("word_document_tool", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WordDocumentToolTests(unittest.TestCase):
    def test_document_is_readable_package_with_requested_text(self):
        data = MODULE.make_docx("Benefits & Teamwork", "First <point>.\n\nSecond point.\ufffe")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("word/document.xml", archive.namelist())
            root = ET.fromstring(archive.read("word/document.xml"))
            text = "".join(
                node.text or ""
                for node in root.iter(f"{{{MODULE._WORD_NS}}}t")
            )
        self.assertIn("Benefits & Teamwork", text)
        self.assertIn("First <point>.", text)
        self.assertIn("Second point.", text)

    def test_headings_and_lists_have_word_styles_and_numbering(self):
        content = (
            "## Introduction\nA useful **bold** point.\n\n"
            "- First benefit\n- Second benefit\n"
            "1. First action\n2. Second action"
        )
        data = MODULE.make_docx("Teamwork", content)
        ns = {"w": MODULE._WORD_NS}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            styles = ET.fromstring(archive.read("word/styles.xml"))
            numbering = ET.fromstring(archive.read("word/numbering.xml"))
            document = ET.fromstring(archive.read("word/document.xml"))
        style_ids = {
            node.attrib[f"{{{MODULE._WORD_NS}}}styleId"]
            for node in styles.findall("w:style", ns)
        }
        self.assertTrue({"Title", "Heading2", "ListBullet", "ListNumber"} <= style_ids)
        self.assertEqual(2, len(numbering.findall("w:num", ns)))
        paragraphs = document.findall(".//w:body/w:p", ns)
        applied = [
            node.attrib[f"{{{MODULE._WORD_NS}}}val"]
            for paragraph in paragraphs
            for node in paragraph.findall("w:pPr/w:pStyle", ns)
        ]
        self.assertEqual(
            ["Title", "Heading2", "Normal", "ListBullet", "ListBullet", "ListNumber", "ListNumber"],
            applied,
        )
        self.assertEqual(4, len(document.findall(".//w:numPr", ns)))
        self.assertTrue(document.findall(".//w:rPr/w:b", ns))

    def test_no_false_success_without_user_context(self):
        result = asyncio.run(
            MODULE.Tools().create_word_document("Test", "Content")
        )
        self.assertIn("could not create", result)
        self.assertNotIn("Download", result)

    def test_empty_or_oversized_inputs_are_rejected(self):
        for title, content in (("", "Content"), ("Title", ""), ("T" * 161, "Content")):
            with self.subTest(title=title[:10], content=content[:10]):
                with self.assertRaises(ValueError):
                    MODULE.make_docx(title, content)


if __name__ == "__main__":
    unittest.main()
