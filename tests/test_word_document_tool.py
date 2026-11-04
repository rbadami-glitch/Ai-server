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
