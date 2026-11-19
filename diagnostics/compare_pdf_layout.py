"""Compare two private PDFs without printing their text or copying their pages.

Requires PyMuPDF locally. A passing automated check still needs visual review.
Do not put either PDF or this tool's runtime output in a public Git checkout.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

import pymupdf


def ink_overlap(reference, candidate) -> float:
    left = reference.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
    right = candidate.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
    if (left.width, left.height) != (right.width, right.height):
        return 0.0
    left_ink = right_ink = common = 0
    for a, b in zip(left.samples, right.samples):
        a_dark = a < 210
        b_dark = b < 210
        left_ink += a_dark
        right_ink += b_dark
        common += a_dark and b_dark
    total = left_ink + right_ink
    return 2 * common / total if total else 1.0


def compare(reference_path: Path, candidate_path: Path) -> dict:
    with pymupdf.open(reference_path) as reference, pymupdf.open(candidate_path) as candidate:
        pages = []
        for index in range(min(len(reference), len(candidate))):
            original = reference[index]
            draft = candidate[index]
            original_text = " ".join(original.get_text().split())
            draft_text = " ".join(draft.get_text().split())
            pages.append({
                "page": index + 1,
                "text_similarity": round(difflib.SequenceMatcher(
                    None, original_text, draft_text, autojunk=False
                ).ratio(), 3),
                "ink_overlap": round(ink_overlap(original, draft), 3),
            })
        gate_passed = (len(reference) == len(candidate)
                       and all(page["text_similarity"] >= 0.95
                               and page["ink_overlap"] >= 0.75 for page in pages))
        return {
            "status": "visual_review_required" if gate_passed else "automated_gate_failed",
            "reference_pages": len(reference),
            "candidate_pages": len(candidate),
            "pages": pages,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    result = compare(args.reference, args.candidate)
    print(json.dumps(result, indent=2))
    if result["status"] == "automated_gate_failed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
