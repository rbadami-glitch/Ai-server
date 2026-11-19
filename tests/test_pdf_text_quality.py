import importlib.util
from pathlib import Path
import unittest

import pymupdf


path = Path(__file__).resolve().parents[1] / "openwebui-tools" / "pdf_text_quality.py"
spec = importlib.util.spec_from_file_location("pdf_text_quality", path)
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class PdfTextQualityTests(unittest.TestCase):
    def test_detects_cross_line_collision_without_flagging_separated_lines(self):
        clean = pymupdf.open()
        page = clean.new_page()
        page.insert_text((72, 72), "First line of readable text")
        page.insert_text((72, 100), "Second separated line")
        self.assertEqual([0], quality.text_collision_counts(clean))

        collided = pymupdf.open()
        page = collided.new_page()
        page.insert_text((72, 72), "First overlapping line")
        page.insert_text((72, 77), "Second overlapping line")
        self.assertGreater(quality.text_collision_counts(collided)[0], 0)


if __name__ == "__main__":
    unittest.main()
