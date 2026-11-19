"""Offline fixed-layout draft from selectable PDF text, images, and lines.

Each word has a fixed position. Edits do not reflow; render and compare every
page before use. Keep input and output outside the public source checkout.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import pymupdf


ESCAPES = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
           "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
           "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}

FONT_FAMILIES = (
    ("timesnewroman", "Times New Roman"),
    ("liberationsans", "Liberation Sans"),
    ("liberationserif", "Liberation Serif"),
    ("couriernew", "Courier New"),
    ("calibri", "Calibri"),
    ("cambria", "Cambria"),
    ("verdana", "Verdana"),
    ("georgia", "Georgia"),
    ("tahoma", "Tahoma"),
    ("arial", "Arial"),
)


def font_family(pdf_name: str) -> str:
    """Map known PDF font names to safe, installed-family candidates."""
    name = pdf_name.split("+", 1)[-1]
    normalized = "".join(character for character in name.lower() if character.isalpha())
    for prefix, family in FONT_FAMILIES:
        if normalized.startswith(prefix):
            return family
    raise ValueError("Unsupported PDF font family")


def escape(text: str) -> str:
    return "".join(ESCAPES.get(char, char) for char in text)


def point(xy) -> str:
    return f"({xy.x:.3f},{xy.y:.3f})"


def color_name(rgb) -> str:
    if rgb is None:
        return "black"
    values = [round(x * 255) for x in rgb]
    return "c" + "_".join(str(x) for x in values)


def run(source: Path, output: Path) -> dict:
    checkout = Path(__file__).resolve().parent.parent
    if source.resolve().is_relative_to(checkout) or output.resolve().is_relative_to(checkout):
        raise ValueError("Keep private documents and drafts outside the public Git checkout")
    if output.exists():
        raise FileExistsError("Refusing to overwrite the output directory")
    report = {"page_count": 0, "text_units": 0, "images": 0,
              "drawing_commands": 0, "unsupported_drawing_commands": 0,
              "status": "draft_requires_rendered_comparison"}
    with pymupdf.open(source) as doc:
        if not doc or len({(round(p.rect.width), round(p.rect.height)) for p in doc}) != 1:
            raise ValueError("Pages must exist and share one page size")
        empty_pages = [index + 1 for index, page in enumerate(doc)
                       if not page.get_text().strip()]
        if len(empty_pages) == len(doc):
            raise ValueError("PDF has no selectable text; OCR is required for editable output")
        report["pages_without_selectable_text"] = empty_pages
        if empty_pages:
            report["status"] = "draft_contains_pages_requiring_ocr"
        output.mkdir(parents=True)
        asset_dir = output / "media"
        asset_dir.mkdir()
        report["page_count"] = len(doc)
        width, height = doc[0].rect.width, doc[0].rect.height
        colors = collections.defaultdict(lambda: (0, 0, 0))
        required_fonts = set()
        pages = []
        assets = {}
        for page in doc:
            commands = []
            for path in page.get_drawings():
                stroke = color_name(path.get("color"))
                fill = color_name(path.get("fill"))
                if path.get("color") is not None:
                    colors[stroke] = tuple(round(c * 255) for c in path["color"])
                if path.get("fill") is not None:
                    colors[fill] = tuple(round(c * 255) for c in path["fill"])
                opts = [f"line width={max(path.get('width') or 0, 0.1):.3f}bp"]
                opts.append(f"draw={stroke}" if path.get("color") is not None else "draw=none")
                if path.get("fill") is not None:
                    opts.append(f"fill={fill}")
                for item in path["items"]:
                    kind = item[0]
                    if kind == "l":
                        commands.append(r"\draw[" + ",".join(opts) + "] " + point(item[1]) + " -- " + point(item[2]) + ";")
                    elif kind == "re":
                        rect = item[1]
                        commands.append(r"\draw[" + ",".join(opts) + "] " + point(rect.tl) + " rectangle " + point(rect.br) + ";")
                    else:
                        report["unsupported_drawing_commands"] += 1
                    report["drawing_commands"] += 1
            for image in page.get_images(full=True):
                xref = image[0]
                if xref not in assets:
                    blob = doc.extract_image(xref)
                    extension = blob["ext"]
                    if extension not in {"png", "jpeg", "jpg"}:
                        raise ValueError("Unsupported embedded image type")
                    name = f"image-{len(assets) + 1}.{extension}"
                    (asset_dir / name).write_bytes(blob["image"])
                    assets[xref] = name
                for rect in page.get_image_rects(xref):
                    commands.append(r"\node[anchor=north west,inner sep=0pt] at " + point(rect.tl)
                                    + r" {\includegraphics[width=" + f"{rect.width:.3f}bp,height={rect.height:.3f}bp"
                                    + "]{media/" + assets[xref] + "}};")
                    report["images"] += 1
            for block in page.get_text("rawdict")["blocks"]:
                for line in block.get("lines", []):
                    if tuple(line.get("dir", (1.0, 0.0))) != (1.0, 0.0):
                        raise ValueError("Rotated text needs separate handling")
                    for span in line["spans"]:
                        family = font_family(span["font"])
                        required_fonts.add(family)
                        rgb = span["color"]
                        name = f"c{(rgb >> 16) & 255}_{(rgb >> 8) & 255}_{rgb & 255}"
                        colors[name] = ((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)
                        font = [r"\fontspec{" + family + "}",
                                r"\fontsize{" + f"{span['size']:.3f}bp" + "}{"
                                + f"{span['size']:.3f}bp" + r"}\selectfont", r"\color{" + name + "}"]
                        if "Bold" in span["font"]:
                            font.append(r"\bfseries")
                        if "Italic" in span["font"] or "Oblique" in span["font"]:
                            font.append(r"\itshape")
                        chars = span["chars"]
                        first = None
                        for index in range(len(chars) + 1):
                            whitespace = index == len(chars) or chars[index]["c"].isspace()
                            if not whitespace and first is None:
                                first = index
                            if whitespace and first is not None:
                                content = "".join(char["c"] for char in chars[first:index])
                                origin = chars[first]["origin"]
                                commands.append(r"\node[anchor=base west,inner sep=0pt,outer sep=0pt,font={"
                                                + " ".join(font) + "}] at "
                                                + f"({origin[0]:.3f},{origin[1]:.3f})"
                                                + " {" + escape(content) + "};")
                                report["text_units"] += 1
                                first = None
            pages.append(commands)
        report["required_font_families"] = sorted(required_fonts)
        definitions = [r"\definecolor{" + name + "}{RGB}{" + ",".join(map(str, rgb)) + "}"
                       for name, rgb in sorted(colors.items()) if name != "black"]
        preamble = [r"\documentclass{article}", r"\usepackage{fontspec}",
                    r"\usepackage{tikz}", r"\usepackage{graphicx}",
                    r"\usepackage{xcolor}", r"\usepackage[paperwidth="
                    + f"{width:.3f}bp,paperheight={height:.3f}bp,margin=0pt"
                    + r"]{geometry}", r"\setmainfont{Arial}", r"\pagestyle{empty}",
                    r"\setlength{\parindent}{0pt}"] + definitions
        document = preamble + [r"\begin{document}"]
        for index, commands in enumerate(pages):
            document += [r"\noindent", r"\begin{tikzpicture}[remember picture,overlay]",
                         r"\begin{scope}[shift={(current page.north west)},x=1bp,y=-1bp]",
                         f"% Editable page {index + 1}"] + commands
            document += [r"\end{scope}", r"\end{tikzpicture}", r"\null"]
            if index < len(pages) - 1:
                document.append(r"\newpage")
        document += [r"\end{document}", ""]
        (output / "template.tex").write_text("\n".join(document), encoding="utf-8")
        (output / "conversion-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    print(json.dumps(run(args.source, args.output), indent=2))
