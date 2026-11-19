"""Offline worker for an opt-in Open WebUI document-to-LaTeX tool.

This runs outside the Open WebUI Python environment. It never contacts a model
or a network service. The output is a fixed-layout draft requiring human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pymupdf

from pdf_to_latex_layout import run as draft_from_pdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from diagnostics.compare_pdf_layout import compare

MAX_INPUT_BYTES = 15 * 1024 * 1024
MAX_PAGES = 20


class ConversionError(Exception):
    def __init__(self, code: str, stage: str):
        super().__init__(code)
        self.code = code
        self.stage = stage


def validate_input(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ConversionError("missing_or_oversized_input", "input")
    extension = path.suffix.lower()
    with path.open("rb") as stream:
        prefix = stream.read(8)
    if extension == ".pdf" and prefix.startswith(b"%PDF-"):
        return extension
    if extension == ".doc" and prefix == bytes.fromhex("d0cf11e0a1b11ae1"):
        return extension
    if extension == ".docx" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                raise ConversionError("oversized_docx_package", "input")
            if {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
                return extension
    raise ConversionError("unsupported_or_mismatched_file", "input")


def run_command(arguments: list[str], *, cwd: Path, timeout: int, env=None) -> None:
    try:
        result = subprocess.run(
            arguments, cwd=cwd, env=env, capture_output=True, text=True,
            timeout=timeout, check=False, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise ConversionError("command_timed_out", "conversion") from error
    if result.returncode:
        raise ConversionError("command_failed", "conversion")


def office_to_pdf(source: Path, soffice: Path, work: Path) -> Path:
    if not soffice.is_file():
        raise ConversionError("libreoffice_unavailable", "preflight")
    input_dir = work / "office-input"
    input_dir.mkdir()
    local_source = input_dir / ("source" + source.suffix.lower())
    shutil.copyfile(source, local_source)
    profile = (work / "libreoffice-profile").as_uri()
    run_command(
        [str(soffice), f"-env:UserInstallation={profile}", "--headless",
         "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(input_dir),
         str(local_source)],
        cwd=input_dir, timeout=90,
    )
    converted = input_dir / "source.pdf"
    if not converted.is_file() or not converted.read_bytes().startswith(b"%PDF-"):
        raise ConversionError("office_pdf_missing", "office_conversion")
    return converted


def font_config(work: Path) -> Path:
    fonts = Path("C:/Windows/Fonts")
    if not (fonts / "arial.ttf").is_file():
        raise ConversionError("arial_unavailable", "preflight")
    cache = work / "font-cache"
    cache.mkdir()
    config = work / "fonts.conf"
    config.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        f'<fontconfig><dir>{fonts.as_posix()}</dir><cachedir>{cache.as_posix()}</cachedir></fontconfig>\n',
        encoding="utf-8",
    )
    return config


def build(source: Path, output: Path, soffice: Path, tectonic: Path) -> dict:
    checkout = Path(__file__).resolve().parent.parent
    if source.resolve().is_relative_to(checkout) or output.resolve().is_relative_to(checkout):
        raise ConversionError("private_paths_required", "input")
    if output.exists() or not output.parent.is_dir() or output.suffix.lower() != ".zip":
        raise ConversionError("output_must_be_new_zip", "input")
    extension = validate_input(source)
    if not tectonic.is_file():
        raise ConversionError("tectonic_unavailable", "preflight")

    with tempfile.TemporaryDirectory(prefix="openwebui-latex-") as temporary:
        work = Path(temporary)
        reference = source if extension == ".pdf" else office_to_pdf(source, soffice, work)
        with pymupdf.open(reference) as document:
            if len(document) > MAX_PAGES:
                raise ConversionError("too_many_pages", "input")
        draft_dir = work / "draft"
        try:
            draft_report = draft_from_pdf(reference, draft_dir)
        except ValueError as error:
            reason = "ocr_required" if "selectable text" in str(error) else "unsupported_layout"
            raise ConversionError(reason, "draft") from error
        if draft_report["pages_without_selectable_text"]:
            raise ConversionError("ocr_required", "draft")
        if draft_report["unsupported_drawing_commands"]:
            raise ConversionError("unsupported_layout", "draft")

        rendered_dir = draft_dir / "rendered"
        rendered_dir.mkdir()
        env = os.environ.copy()
        local_app_data = env.get("LOCALAPPDATA")
        if not local_app_data:
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        cache = Path(local_app_data) / "TectonicProject" / "Tectonic" / "cache"
        if not (cache / "bundles" / "data").is_dir():
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        env["TECTONIC_CACHE_DIR"] = str(cache)
        env["FONTCONFIG_FILE"] = str(font_config(work))
        run_command(
            [str(tectonic), "template.tex", "--only-cached", "--untrusted",
             "--outdir", str(rendered_dir)],
            cwd=draft_dir, timeout=90, env=env,
        )
        rendered = rendered_dir / "template.pdf"
        if not rendered.is_file():
            raise ConversionError("rendered_pdf_missing", "render")
        result = compare(reference, rendered)
        if (result["reference_pages"] != result["candidate_pages"]
                or any(page["text_similarity"] < 0.99 or page["ink_overlap"] < 0.95
                       for page in result["pages"])):
            raise ConversionError("fidelity_check_failed", "compare")

        report = {
            "status": "fixed_layout_draft_requires_human_review",
            "page_count": draft_report["page_count"],
            "text_units": draft_report["text_units"],
            "images": draft_report["images"],
            "required_font_families": draft_report["required_font_families"],
            "minimum_ink_overlap": min(page["ink_overlap"] for page in result["pages"]),
            "text_similarity_per_page": [page["text_similarity"] for page in result["pages"]],
        }
        readme = (
            "Fixed-layout LaTeX draft. Open preview.pdf before editing.\n"
            "Text is editable in template.tex, one word per positioned node. "
            "Longer edits do not reflow and can overlap.\n"
            "Compile with XeLaTeX or Tectonic with Arial and the font families listed in "
            "conversion-report.json, plus fontspec, TikZ, graphicx, xcolor, and geometry.\n"
            "A visual comparison passed, but this package still requires human review.\n"
        )
        with output.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(draft_dir / "template.tex", "template.tex")
                archive.write(rendered, "preview.pdf")
                for path in sorted((draft_dir / "media").iterdir()):
                    if path.is_file():
                        archive.write(path, f"media/{path.name}")
                archive.writestr("conversion-report.json", json.dumps(report, indent=2))
                archive.writestr("README.txt", readme)
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ConversionError("corrupt_output", "package")
        report["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--soffice", required=True, type=Path)
    parser.add_argument("--tectonic", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = build(args.source, args.output, args.soffice, args.tectonic)
    except ConversionError as error:
        print(json.dumps({"ok": False, "code": error.code, "stage": error.stage}))
        raise SystemExit(2)
    print(json.dumps({"ok": True, **report}))


if __name__ == "__main__":
    main()
