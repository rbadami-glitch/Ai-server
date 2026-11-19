"""Draft a self-contained LaTeX ZIP from a DOCX without claiming visual fidelity.

Run only with private input and output paths outside the public Git checkout.
The caller must render the TeX and compare it with the source PDF before use.
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
           "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
           "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escape(text: str) -> str:
    return "".join(SPECIAL.get(char, char) for char in text)


def inches(length) -> float | None:
    return round(length.inches, 3) if length is not None else None


class Draft:
    def __init__(self, source: Path):
        self.doc = Document(source)
        self.media: dict[str, bytes] = {}
        self.warnings: set[str] = set()
        self.image_number = 0

    def image_tex(self, run, part) -> str:
        rendered = []
        for blip in run._r.xpath(".//a:blip"):
            relationship = blip.get(qn("r:embed"))
            if not relationship or relationship not in part.related_parts:
                self.warnings.add("An image could not be resolved from its relationship.")
                continue
            image = part.related_parts[relationship]
            extension = Path(image.partname).suffix.lower()
            if extension not in {".png", ".jpg", ".jpeg", ".pdf"}:
                self.warnings.add("An image format needs manual conversion before LaTeX compilation.")
                continue
            self.image_number += 1
            name = f"media/image-{self.image_number}{extension}"
            self.media[name] = image.blob
            rendered.append(r"\includegraphics[width=0.8\linewidth]{" + name + "}")
        return " ".join(rendered)

    def paragraph(self, paragraph: Paragraph) -> str:
        pieces = []
        for run in paragraph.runs:
            value = escape(run.text).replace("\n", r"\\" + "\n")
            if value:
                if run.bold:
                    value = r"\textbf{" + value + "}"
                if run.italic:
                    value = r"\textit{" + value + "}"
                if run.underline:
                    value = r"\underline{" + value + "}"
                pieces.append(value)
            image = self.image_tex(run, paragraph.part)
            if image:
                pieces.append(image)
        content = "".join(pieces).strip()
        if not content:
            return ""
        style = paragraph.style.name.lower() if paragraph.style else ""
        if style.startswith("heading 1"):
            return r"\section*{" + content + "}"
        if style.startswith("heading 2"):
            return r"\subsection*{" + content + "}"
        if style.startswith("heading 3"):
            return r"\subsubsection*{" + content + "}"
        if "title" in style:
            return r"\begin{center}\Large\bfseries " + content + r"\end{center}"
        if style.startswith("list"):
            self.warnings.add("List numbering and indentation need visual review.")
        alignment = paragraph.alignment
        if alignment == WD_ALIGN_PARAGRAPH.CENTER:
            return r"\begin{center}" + content + r"\end{center}"
        if alignment == WD_ALIGN_PARAGRAPH.RIGHT:
            return r"\begin{flushright}" + content + r"\end{flushright}"
        return content + "\n"

    def table(self, table: Table) -> str:
        rows = table.rows
        if not rows:
            return ""
        count = max(len(row.cells) for row in rows)
        if count == 0:
            return ""
        self.warnings.add("Table widths, merged cells, borders, and page breaks need visual review.")
        width = round(0.92 / count, 4)
        columns = "|" + "|".join(f"p{{{width}\\linewidth}}" for _ in range(count)) + "|"
        lines = [r"\begin{longtable}{" + columns + "}", r"\hline"]
        for row in rows:
            cells = []
            for cell in row.cells:
                value = " ".join(self.paragraph(p).strip() for p in cell.paragraphs)
                cells.append(value or "~")
            cells.extend(["~"] * (count - len(cells)))
            lines.append(" & ".join(cells) + r" \\ \hline")
        lines.append(r"\end{longtable}")
        return "\n".join(lines)

    def build(self) -> tuple[bytes, dict]:
        self.warnings.add("Named Word styles, fonts, colors, and precise spacing are not fully mapped.")
        sections = self.doc.sections
        if len(sections) > 1:
            self.warnings.add("Multiple Word sections are represented with the first section's page geometry only.")
        section = sections[0]
        geometry = []
        for name, length in (("paperwidth", section.page_width), ("paperheight", section.page_height),
                             ("left", section.left_margin), ("right", section.right_margin),
                             ("top", section.top_margin), ("bottom", section.bottom_margin)):
            if (value := inches(length)) is not None:
                geometry.append(f"{name}={value}in")
        header = " ".join(self.paragraph(p).strip() for p in section.header.paragraphs)
        footer = " ".join(self.paragraph(p).strip() for p in section.footer.paragraphs)
        if section.different_first_page_header_footer or self.doc.settings.odd_and_even_pages_header_footer:
            self.warnings.add("First-page or odd/even header variants need visual review.")
        body = []
        for element in self.doc.element.body.iterchildren():
            if element.tag == qn("w:p"):
                value = self.paragraph(Paragraph(element, self.doc))
            elif element.tag == qn("w:tbl"):
                value = self.table(Table(element, self.doc))
            else:
                continue
            if value:
                body.append(value)
        if not body:
            raise ValueError("No printable body content found")
        if self.media:
            self.warnings.add("Embedded images were copied, but OCR was not performed on them.")
        preamble = [r"\documentclass{article}", r"\usepackage[T1]{fontenc}",
                    r"\usepackage[utf8]{inputenc}", r"\usepackage{geometry}",
                    r"\usepackage{graphicx}", r"\usepackage{longtable}",
                    r"\usepackage{fancyhdr}", r"\geometry{" + ",".join(geometry) + "}",
                    r"\pagestyle{fancy}", r"\fancyhf{}"]
        if header:
            preamble.append(r"\fancyhead[L]{\parbox{\textwidth}{" + header + "}}")
        if footer:
            preamble.append(r"\fancyfoot[C]{\parbox{\textwidth}{" + footer + "}}")
        tex = "\n".join(preamble + [r"\begin{document}", "\n\n".join(body), r"\end{document}", ""])
        report = {"status": "draft_requires_visual_review", "body_blocks": len(body),
                  "media_files": len(self.media), "section_count": len(sections),
                  "warnings": sorted(self.warnings)}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("template.tex", tex)
            archive.writestr("conversion-report.json", json.dumps(report, indent=2))
            for name, blob in self.media.items():
                archive.writestr(name, blob)
        return output.getvalue(), report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    if args.output.resolve().is_relative_to(repo):
        parser.error("Keep converted documents outside the public Git checkout")
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Output must be a new ZIP path in an existing private directory")
    if args.output.suffix.lower() != ".zip":
        parser.error("Output must end in .zip")
    data, report = Draft(args.docx).build()
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
