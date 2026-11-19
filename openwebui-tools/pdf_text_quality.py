"""Conservative legibility checks for rendered, reflowing LaTeX drafts."""

from __future__ import annotations

import pymupdf


def text_collision_counts(document) -> list[int]:
    """Count substantial overlaps between text on different rendered lines.

    Same-line spans may overlap slightly through kerning or font changes, so
    they are excluded. This is a rejection signal, not a fidelity score.
    """
    counts = []
    for page in document:
        spans = []
        for block_index, block in enumerate(page.get_text("dict").get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line_index, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    if not str(span.get("text", "")).strip():
                        continue
                    box = pymupdf.Rect(span.get("bbox", (0, 0, 0, 0)))
                    if box.is_empty or box.is_infinite:
                        continue
                    spans.append((block_index, line_index, box))
        collisions = 0
        for index, (block_a, line_a, box_a) in enumerate(spans):
            for block_b, line_b, box_b in spans[index + 1:]:
                if block_a == block_b and line_a == line_b:
                    continue
                overlap = box_a & box_b
                if overlap.is_empty:
                    continue
                x_fraction = overlap.width / min(box_a.width, box_b.width)
                y_fraction = overlap.height / min(box_a.height, box_b.height)
                if x_fraction >= 0.35 and y_fraction >= 0.45:
                    collisions += 1
        counts.append(collisions)
    return counts


if __name__ == "__main__":
    import argparse
    import json
    import zipfile

    parser = argparse.ArgumentParser(description="Print only collision counts from an editable draft ZIP")
    parser.add_argument("--zip", required=True)
    args = parser.parse_args()
    with zipfile.ZipFile(args.zip) as archive:
        preview = archive.read("editable-preview.pdf")
    with pymupdf.open(stream=preview, filetype="pdf") as document:
        print(json.dumps({"pages": len(document),
                          "text_collisions_per_page": text_collision_counts(document)}))
