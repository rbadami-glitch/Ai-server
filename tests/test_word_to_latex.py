"""Synthetic checks for Word-structure LaTeX drafts."""

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "openwebui-tools"))
from word_to_latex import Draft


class WordDraftHeaderTests(unittest.TestCase):
    def test_complex_header_stays_in_running_header_and_reserves_body_space(self):
        document = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Body text</w:t></w:r></w:p>'
            '<w:sectPr><w:pgMar w:top="1440"/></w:sectPr></w:body></w:document>'
        )
        header = (
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            '<w:r><w:t>HEADER TITLE</w:t></w:r></w:p>'
            '<w:p><w:r><w:t xml:space="preserve">   header detail   </w:t>'
            '<w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>'
            '<w:p><w:r><w:t>SHEET NO. </w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText>PAGE</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:r><w:t> OF </w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText>NUMPAGES \\* ARABIC</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>5</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '</w:p></w:hdr>'
        )
        relations = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="media/logo.png"/></Relationships>'
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "synthetic.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", document)
                archive.writestr("word/header1.xml", header)
                archive.writestr("word/_rels/header1.xml.rels", relations)
                archive.writestr("word/media/logo.png", b"synthetic-image")
            data, report = Draft(source).build()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tex = archive.read("template.tex").decode("utf-8")
            self.assertIn("media/image-1.png", archive.namelist())
        self.assertIn(r"\setlength{\headheight}{1.6in}", tex)
        self.assertIn(r"\geometry{top=\dimexpr\headheight+0.35in\relax", tex)
        self.assertIn(r"\fancyhead[L]{\parbox{\textwidth}{\small HEADER TITLE", tex)
        self.assertNotIn(r"\section*{HEADER TITLE}", tex)
        self.assertIn(r"\label{owui:lastpage}", tex)
        self.assertIn(r"SHEET NO. \thepage OF \pageref{owui:lastpage}", tex)
        self.assertNotIn("SHEET NO. 1 OF 5", tex)
        self.assertIn("Body text", tex)
        self.assertIn("Running header content is flattened and needs visual review.",
                      report["warnings"])

    def test_header_table_keeps_cells_and_places_picture_beside_text(self):
        namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        document = (f'<w:document xmlns:w="{namespace}"><w:body>'
                    '<w:p><w:r><w:t>Body</w:t></w:r></w:p>'
                    '<w:sectPr/></w:body></w:document>')
        header = (
            f'<w:hdr xmlns:w="{namespace}" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<w:tbl><w:tblGrid><w:gridCol w:w="6000"/><w:gridCol w:w="3000"/></w:tblGrid>'
            '<w:tr><w:tc><w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing>'
            '<w:t>Organization</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Address</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Sheet </w:t></w:r></w:p></w:tc></w:tr>'
            '</w:tbl></w:hdr>'
        )
        relations = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="media/logo.png"/></Relationships>'
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "synthetic-table.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", document)
                archive.writestr("word/header1.xml", header)
                archive.writestr("word/_rels/header1.xml.rels", relations)
                archive.writestr("word/media/logo.png", b"synthetic-image")
            data, report = Draft(source).build()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tex = archive.read("template.tex").decode("utf-8")
        self.assertIn(r"\begin{tabular}{|p{", tex)
        self.assertIn(r"\begin{minipage}[c]{0.53\linewidth}", tex)
        self.assertIn("Organization", tex)
        self.assertIn("Address", tex)
        self.assertIn("Running header table structure needs visual review.", report["warnings"])


if __name__ == "__main__":
    unittest.main()
