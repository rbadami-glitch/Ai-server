"""Draft a LaTeX ZIP from DOCX with Python's standard library only.

This is an offline prototype. Keep inputs and outputs outside the public Git
checkout. A rendered-page comparison with the source PDF is required before use.
"""

from __future__ import annotations

import argparse
import io
import json
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
           "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
           "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escape(text: str) -> str:
    return "".join(SPECIAL.get(char, char) for char in text)


def attr(node, name):
    return node.get(W + name) if node is not None else None


class Draft:
    def __init__(self, source: Path):
        if not source.is_file() or not zipfile.is_zipfile(source):
            raise ValueError("Expected an existing DOCX ZIP package")
        self.package = zipfile.ZipFile(source)
        self.names = set(self.package.namelist())
        if "word/document.xml" not in self.names:
            self.package.close()
            raise ValueError("DOCX has no word/document.xml")
        self.document = self.xml("word/document.xml")
        self.styles = self.style_names()
        self.media: dict[str, bytes] = {}
        self.warnings: set[str] = {
            "Named styles, fonts, colors, borders, and precise spacing are not fully mapped."
        }

    def xml(self, name):
        return ET.fromstring(self.package.read(name))

    def style_names(self):
        names = {}
        if "word/styles.xml" in self.names:
            for style in self.xml("word/styles.xml").iter(W + "style"):
                style_id = attr(style, "styleId")
                name = attr(style.find(W + "name"), "val")
                if style_id and name:
                    names[style_id] = name.lower()
        return names

    def relationships(self, part):
        directory, filename = posixpath.split(part)
        rels_name = posixpath.join(directory, "_rels", filename + ".rels")
        if rels_name not in self.names:
            return {}
        result = {}
        for item in self.xml(rels_name).iter(P + "Relationship"):
            if item.get("TargetMode") == "External":
                continue
            target = item.get("Target", "")
            if not target:
                continue
            resolved = (target.lstrip("/") if target.startswith("/") else
                        posixpath.normpath(posixpath.join(directory, target)))
            if resolved in self.names:
                result[item.get("Id")] = resolved
        return result

    def image(self, relationship_id, rels, width=None):
        target = rels.get(relationship_id)
        if not target:
            self.warnings.add("An image relationship could not be resolved.")
            return ""
        extension = posixpath.splitext(target)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".pdf"}:
            self.warnings.add("An image format needs manual conversion for LaTeX.")
            return ""
        name = f"media/image-{len(self.media) + 1}{extension}"
        self.media[name] = self.package.read(target)
        size = width or r"0.8\linewidth"
        return r"\includegraphics[width=" + size + "]{" + name + "}"

    def paragraph(self, node, rels, *, running_part=False):
        parts = []
        for run in node.iter(W + "r"):
            value = "".join(escape(item.text or "") for item in run.iter(W + "t"))
            if running_part:
                value = re.sub(r"\s+", " ", value)
            if run.find(W + "tab") is not None:
                value += " "
            for line_break in run.findall(W + "br"):
                value += (r"\\" if running_part or attr(line_break, "type") != "page"
                          else r"\clearpage")
            props = run.find(W + "rPr")
            if value and props is not None:
                if props.find(W + "b") is not None:
                    value = r"\textbf{" + value + "}"
                if props.find(W + "i") is not None:
                    value = r"\textit{" + value + "}"
                if props.find(W + "u") is not None:
                    value = r"\underline{" + value + "}"
            if value:
                parts.append(value)
            for blip in run.iter(A + "blip"):
                if image := self.image(blip.get(R + "embed"), rels):
                    parts.append(image)
            for shape in run.iter(V + "shape"):
                dimensions = re.search(r"(?:^|;)\s*width:\s*([0-9.]+)pt", shape.get("style", ""))
                width = f"{dimensions.group(1)}pt" if dimensions else None
                for legacy_image in shape.iter(V + "imagedata"):
                    if image := self.image(legacy_image.get(R + "id"), rels, width):
                        parts.append(image)
        content = "".join(parts).strip()
        if not content or content == r"\\":
            return ""
        if content == r"\clearpage":
            return content
        props = node.find(W + "pPr")
        style_id = attr(props.find(W + "pStyle"), "val") if props is not None else None
        style = self.styles.get(style_id, (style_id or "").lower())
        if running_part:
            return content
        if re.match(r"heading\s*1", style):
            return r"\section*{" + content + "}"
        if re.match(r"heading\s*2", style):
            return r"\subsection*{" + content + "}"
        if re.match(r"heading\s*3", style):
            return r"\subsubsection*{" + content + "}"
        if "title" in style:
            return r"\begin{center}\Large\bfseries " + content + r"\end{center}"
        alignment = attr(props.find(W + "jc"), "val") if props is not None else None
        if alignment == "center":
            return r"\begin{center}" + content + r"\end{center}"
        if alignment == "right":
            return r"\begin{flushright}" + content + r"\end{flushright}"
        if props is not None and props.find(W + "numPr") is not None:
            self.warnings.add("List numbering and indentation need visual review.")
        return content

    def table(self, node, rels):
        rows = list(node.findall(W + "tr"))
        count = max((len(row.findall(W + "tc")) for row in rows), default=0)
        if not count:
            return ""
        self.warnings.add("Table widths, merged cells, borders, and page breaks need visual review.")
        width = 0.92 / count
        columns = "|" + "|".join(f"p{{{width:.4f}\\linewidth}}" for _ in range(count)) + "|"
        lines = [r"\begin{longtable}{" + columns + "}", r"\hline"]
        for row in rows:
            cells = []
            for cell in row.findall(W + "tc"):
                text = " ".join(filter(None, (self.paragraph(p, rels) for p in cell.findall(W + "p"))))
                cells.append(text or "~")
            cells.extend(["~"] * (count - len(cells)))
            lines.append(" & ".join(cells) + r" \\ \hline")
        return "\n".join(lines + [r"\end{longtable}"])

    def geometry(self, section):
        values = []
        for child, pairs in (("pgSz", (("w", "paperwidth"), ("h", "paperheight"))),
                             ("pgMar", (("left", "left"), ("right", "right"),
                                        ("top", "top"), ("bottom", "bottom")))):
            item = section.find(W + child) if section is not None else None
            for source, target in pairs:
                raw = attr(item, source)
                if raw and raw.lstrip("-").isdigit():
                    values.append(f"{target}={int(raw) / 1440:.3f}in")
        return ",".join(values)

    def part_text(self, part):
        rels = self.relationships(part)
        return " ".join(filter(None, (self.paragraph(p, rels, running_part=True)
                                      for p in self.xml(part).iter(W + "p"))))

    def build(self):
        try:
            body = self.document.find(W + "body")
            if body is None:
                raise ValueError("DOCX has no body")
            sections = list(self.document.iter(W + "sectPr"))
            if len(sections) > 1:
                self.warnings.add("Multiple Word sections use the first page geometry only.")
            rels = self.relationships("word/document.xml")
            blocks = []
            for child in body:
                if child.tag == W + "p":
                    value = self.paragraph(child, rels)
                elif child.tag == W + "tbl":
                    value = self.table(child, rels)
                else:
                    continue
                if value:
                    blocks.append(value)
                if child.tag == W + "p" and child.find(W + "pPr/" + W + "sectPr") is not None:
                    blocks.append(r"\clearpage")
            if not blocks:
                raise ValueError("No printable body content found")
            headers = sorted(n for n in self.names if re.fullmatch(r"word/header\d+\.xml", n))
            footers = sorted(n for n in self.names if re.fullmatch(r"word/footer\d+\.xml", n))
            if len(headers) > 1 or len(footers) > 1:
                self.warnings.add("Multiple headers or footers need per-page visual review.")
            header = self.part_text(headers[0]) if headers else ""
            footer = self.part_text(footers[0]) if footers else ""
            if self.media:
                self.warnings.add("Embedded images were copied; OCR was not performed.")
            source_media = [name for name in self.names if name.startswith("word/media/")]
            if len(self.media) < len(source_media):
                self.warnings.add("Some embedded media were not included in the draft.")
            preamble = [r"\documentclass{article}", r"\usepackage{fontspec}",
                        r"\setmainfont{Arial}", r"\usepackage{geometry}",
                        r"\usepackage{graphicx}", r"\usepackage{longtable}",
                        r"\usepackage{fancyhdr}"]
            if sections and (layout := self.geometry(sections[0])):
                preamble.append(r"\geometry{" + layout + "}")
            preamble.extend([r"\pagestyle{fancy}", r"\fancyhf{}"])
            if header:
                header_height = "1.6in" if r"\includegraphics" in header else "0.9in"
                preamble.extend([
                    r"\setlength{\headheight}{" + header_height + "}",
                    r"\geometry{top=\dimexpr\headheight+0.35in\relax,headsep=0.1in}",
                    r"\fancyhead[L]{\parbox{\textwidth}{\small " + header + "}}",
                ])
                self.warnings.add("Running header content is flattened and needs visual review.")
            if footer:
                preamble.append(r"\fancyfoot[C]{\parbox{\textwidth}{" + footer + "}}")
            tex = "\n".join([
                "% Editable Word-structure draft. Review the rendered preview before use.",
                "% Body text below can reflow; edit the running header above separately.",
                *preamble,
                r"\begin{document}", "\n\n".join(blocks), r"\end{document}", "",
            ])
            report = {"status": "draft_requires_visual_review", "body_blocks": len(blocks),
                      "media_files": len(self.media), "source_media_files": len(source_media),
                      "section_count": len(sections),
                      "warnings": sorted(self.warnings)}
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("template.tex", tex)
                archive.writestr("conversion-report.json", json.dumps(report, indent=2))
                for name, blob in self.media.items():
                    archive.writestr(name, blob)
            return output.getvalue(), report
        finally:
            self.package.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    if args.output.resolve().is_relative_to(repo):
        parser.error("Keep converted documents outside the public Git checkout")
    if args.output.exists() or not args.output.parent.is_dir() or args.output.suffix.lower() != ".zip":
        parser.error("Output must be a new ZIP path in an existing private directory")
    data, report = Draft(args.docx).build()
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
