"""Offline worker for an opt-in Open WebUI document-to-LaTeX tool.

This runs outside the Open WebUI Python environment. It never contacts a model
or a network service. The output is a fixed-layout draft requiring human review.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf

from pdf_to_latex_layout import run as draft_from_pdf
from ocr_to_latex import convert as ocr_text_from_pdf
from word_to_latex import Draft as WordDraft

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


def office_to_docx(source: Path, soffice: Path, work: Path) -> Path:
    """Convert a legacy Word file to DOCX without touching the original."""
    input_dir = work / "semantic-input"
    input_dir.mkdir()
    local_source = input_dir / "source.doc"
    shutil.copyfile(source, local_source)
    profile = (work / "semantic-office-profile").as_uri()
    run_command(
        [str(soffice), f"-env:UserInstallation={profile}", "--headless",
         "--convert-to", "docx:Office Open XML Text", "--outdir", str(input_dir),
         str(local_source)],
        cwd=input_dir, timeout=90,
    )
    converted = input_dir / "source.docx"
    if not zipfile.is_zipfile(converted):
        raise ConversionError("office_docx_missing", "semantic_conversion")
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


def build(source: Path, output: Path, soffice: Path, tectonic: Path,
          *, include_editable_draft: bool = False) -> dict:
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

        semantic_report = {"status": "not_requested"}
        semantic_dir = None
        semantic_rendered = None
        if include_editable_draft and extension in {".doc", ".docx"}:
            try:
                semantic_source = source if extension == ".docx" else office_to_docx(source, soffice, work)
                semantic_dir = work / "semantic"
                semantic_dir.mkdir()
                semantic_media = semantic_dir / "editable-media"
                semantic_media.mkdir()
                semantic_data, semantic_report = WordDraft(semantic_source).build()
                with zipfile.ZipFile(io.BytesIO(semantic_data)) as semantic_archive:
                    editable_source = semantic_archive.read("template.tex").decode("utf-8")
                    editable_source = editable_source.replace("{media/", "{editable-media/")
                    (semantic_dir / "editable.tex").write_text(editable_source, encoding="utf-8")
                    for name in semantic_archive.namelist():
                        if (name.startswith("media/") and
                                "/" not in name[len("media/"):] and
                                "\\" not in name and
                                ".." not in Path(name).parts):
                            target = semantic_media / Path(name).name
                            target.write_bytes(semantic_archive.read(name))
                semantic_rendered_dir = semantic_dir / "rendered"
                semantic_rendered_dir.mkdir()
                run_command(
                    [str(tectonic), "editable.tex", "--only-cached", "--untrusted",
                     "--outdir", str(semantic_rendered_dir)],
                    cwd=semantic_dir, timeout=90, env=env,
                )
                semantic_rendered = semantic_rendered_dir / "editable.pdf"
                if not semantic_rendered.is_file():
                    raise ConversionError("semantic_pdf_missing", "semantic_render")
                with pymupdf.open(semantic_rendered) as preview:
                    if not preview or not any(page.get_text().strip() for page in preview):
                        raise ConversionError("semantic_pdf_empty", "semantic_render")
                    semantic_report["preview_pages"] = len(preview)
                    with pymupdf.open(reference) as reference_preview:
                        reference_tokens = collections.Counter(re.findall(
                            r"[a-z0-9]+",
                            " ".join(page.get_text() for page in reference_preview).lower()))
                        reference_sizes = [
                            (round(page.rect.width), round(page.rect.height))
                            for page in reference_preview
                        ]
                    rendered_tokens = collections.Counter(re.findall(
                        r"[a-z0-9]+", " ".join(page.get_text() for page in preview).lower()))
                    semantic_report["text_coverage"] = round(
                        sum((reference_tokens & rendered_tokens).values())
                        / max(1, sum(reference_tokens.values())), 3)
                    semantic_report["page_geometry_match"] = reference_sizes == [
                        (round(page.rect.width), round(page.rect.height))
                        for page in preview
                    ]
                    if semantic_report["text_coverage"] < 0.90:
                        raise ConversionError("semantic_text_coverage_low", "semantic_compare")
                    if len(preview) == result["reference_pages"]:
                        semantic_layout = compare(reference, semantic_rendered)
                        semantic_report["minimum_ink_overlap"] = round(
                            min(page["ink_overlap"] for page in semantic_layout["pages"]), 3)
                    else:
                        semantic_report["minimum_ink_overlap"] = None
                    semantic_report["status"] = "reflowing_draft_requires_human_review"
            except (ConversionError, ValueError, OSError, zipfile.BadZipFile) as error:
                semantic_report = {
                    "status": "unavailable",
                    "reason": error.code if isinstance(error, ConversionError) else type(error).__name__,
                }
                semantic_dir = semantic_rendered = None
        elif include_editable_draft:
            semantic_report = {"status": "unavailable", "reason": "PDF has no reliable Word structure"}

        report = {
            "status": "fixed_layout_draft_requires_human_review",
            "page_count": draft_report["page_count"],
            "text_units": draft_report["text_units"],
            "images": draft_report["images"],
            "required_font_families": draft_report["required_font_families"],
            "minimum_ink_overlap": min(page["ink_overlap"] for page in result["pages"]),
            "text_similarity_per_page": [page["text_similarity"] for page in result["pages"]],
            "editable_draft": semantic_report,
        }
        readme = (
            "Open preview.pdf to inspect the visually faithful fixed-layout draft.\n"
            "template.tex positions each word; longer edits do not reflow and can overlap.\n"
            "If editable.tex and editable-preview.pdf are present, they are a separate "
            "reflowing Word-structure draft. Inspect semantic-report.json and compare "
            "the editable preview with the source before using it.\n"
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
                if semantic_dir is not None and semantic_rendered is not None:
                    archive.write(semantic_dir / "editable.tex", "editable.tex")
                    archive.write(semantic_rendered, "editable-preview.pdf")
                    archive.writestr("semantic-report.json", json.dumps(semantic_report, indent=2))
                    for path in sorted(semantic_media.iterdir()):
                        if path.is_file():
                            archive.write(path, f"editable-media/{path.name}")
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ConversionError("corrupt_output", "package")
        report["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        return report


def build_editable_fallback(source: Path, output: Path, soffice: Path, tectonic: Path) -> dict:
    """Experimental Word fallback when a verified visual draft is impossible."""
    if output.exists() or output.suffix.lower() != ".zip" or not output.parent.is_dir():
        raise ConversionError("output_must_be_new_zip", "input")
    checkout = Path(__file__).resolve().parent.parent
    if source.resolve().is_relative_to(checkout) or output.resolve().is_relative_to(checkout):
        raise ConversionError("private_paths_required", "input")
    if validate_input(source) not in {".doc", ".docx"}:
        raise ConversionError("editable_fallback_requires_word", "input")
    if not soffice.is_file() or not tectonic.is_file():
        raise ConversionError("converter_unavailable", "preflight")
    with tempfile.TemporaryDirectory(prefix="openwebui-editable-") as temporary:
        work = Path(temporary)
        reference = office_to_pdf(source, soffice, work)
        semantic_source = source if source.suffix.lower() == ".docx" else office_to_docx(source, soffice, work)
        try:
            data, word_report = WordDraft(semantic_source).build()
        except (ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
            raise ConversionError("editable_source_unsupported", "semantic_conversion") from error
        draft = work / "draft"
        draft.mkdir()
        media = draft / "media"
        media.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            (draft / "template.tex").write_bytes(archive.read("template.tex"))
            for name in archive.namelist():
                if (name.startswith("media/") and
                        "/" not in name[len("media/"):] and
                        "\\" not in name and
                        ".." not in Path(name).parts):
                    (media / Path(name).name).write_bytes(archive.read(name))
        cache_root = os.environ.get("LOCALAPPDATA")
        if not cache_root:
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        cache = Path(cache_root) / "TectonicProject" / "Tectonic" / "cache"
        if not (cache / "bundles" / "data").is_dir():
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        env = os.environ.copy()
        env["TECTONIC_CACHE_DIR"] = str(cache)
        env["FONTCONFIG_FILE"] = str(font_config(work))
        rendered_dir = draft / "rendered"
        rendered_dir.mkdir()
        run_command(
            [str(tectonic), "template.tex", "--only-cached", "--untrusted",
             "--outdir", str(rendered_dir)],
            cwd=draft, timeout=90, env=env,
        )
        rendered = rendered_dir / "template.pdf"
        if not rendered.is_file():
            raise ConversionError("rendered_pdf_missing", "render")
        with pymupdf.open(reference) as original, pymupdf.open(rendered) as candidate:
            source_sizes = [(round(page.rect.width), round(page.rect.height))
                            for page in original]
            preview_sizes = [(round(page.rect.width), round(page.rect.height))
                             for page in candidate]
            reference_tokens = collections.Counter(re.findall(
                r"[a-z0-9]+", " ".join(page.get_text() for page in original).lower()))
            candidate_tokens = collections.Counter(re.findall(
                r"[a-z0-9]+", " ".join(page.get_text() for page in candidate).lower()))
            coverage = sum((reference_tokens & candidate_tokens).values()) / max(
                1, sum(reference_tokens.values()))
            if not candidate_tokens or coverage < 0.90:
                raise ConversionError("editable_text_coverage_low", "compare")
            report = {
                "status": "reflowing_word_draft_requires_human_review",
                "source_pages": len(original),
                "preview_pages": len(candidate),
                "text_coverage": round(coverage, 3),
                "visual_fidelity_verified": False,
                "page_geometry_match": source_sizes == preview_sizes,
                "warnings": word_report["warnings"],
            }
        with output.open("xb") as stream, zipfile.ZipFile(
            stream, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.write(draft / "template.tex", "template.tex")
            archive.write(rendered, "preview.pdf")
            for path in sorted(media.iterdir()):
                archive.write(path, f"media/{path.name}")
            archive.writestr("conversion-report.json", json.dumps(report, indent=2))
            archive.writestr(
                "README.txt",
                "Reflowing Word-to-LaTeX draft. Text coverage passed, but appearance "
                "was not verified. Compare every page with the source before use.\n",
            )
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ConversionError("corrupt_output", "package")
        report["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        return report


def build_ocr_fallback(source: Path, output: Path, tectonic: Path, tessdata: Path) -> dict:
    """Experimental local OCR text fallback for scanned English PDFs."""
    if output.exists() or output.suffix.lower() != ".zip" or not output.parent.is_dir():
        raise ConversionError("output_must_be_new_zip", "input")
    checkout = Path(__file__).resolve().parent.parent
    if source.resolve().is_relative_to(checkout) or output.resolve().is_relative_to(checkout):
        raise ConversionError("private_paths_required", "input")
    if validate_input(source) != ".pdf":
        raise ConversionError("ocr_fallback_requires_pdf", "input")
    if not tectonic.is_file() or not (tessdata / "eng.traineddata").is_file():
        raise ConversionError("offline_ocr_unavailable", "preflight")
    with tempfile.TemporaryDirectory(prefix="openwebui-ocr-") as temporary:
        work = Path(temporary)
        draft = work / "draft"
        try:
            report = ocr_text_from_pdf(source, draft, tessdata)
        except (ValueError, RuntimeError) as error:
            raise ConversionError("ocr_text_unavailable", "ocr") from error
        cache_root = os.environ.get("LOCALAPPDATA")
        if not cache_root:
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        cache = Path(cache_root) / "TectonicProject" / "Tectonic" / "cache"
        if not (cache / "bundles" / "data").is_dir():
            raise ConversionError("tectonic_cache_unavailable", "preflight")
        env = os.environ.copy()
        env["TECTONIC_CACHE_DIR"] = str(cache)
        env["FONTCONFIG_FILE"] = str(font_config(work))
        rendered_dir = draft / "rendered"
        rendered_dir.mkdir()
        run_command(
            [str(tectonic), "template.tex", "--only-cached", "--untrusted",
             "--outdir", str(rendered_dir)],
            cwd=draft, timeout=90, env=env,
        )
        rendered = rendered_dir / "template.pdf"
        if not rendered.is_file():
            raise ConversionError("rendered_pdf_missing", "render")
        with pymupdf.open(rendered) as preview:
            if not preview or not any(page.get_text().strip() for page in preview):
                raise ConversionError("ocr_preview_empty", "render")
            report["preview_pages"] = len(preview)
        with output.open("xb") as stream, zipfile.ZipFile(
            stream, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.write(draft / "template.tex", "template.tex")
            archive.write(rendered, "preview.pdf")
            archive.writestr("conversion-report.json", json.dumps(report, indent=2))
            archive.writestr(
                "README.txt",
                "OCR text draft. Recognition may contain mistakes and formatting was "
                "not preserved. Compare every word and page with the uploaded PDF.\n",
            )
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
    parser.add_argument("--include-editable-draft", action="store_true",
                        help="Experimental Word structure output; not ready for shared-model rollout")
    parser.add_argument("--tessdata", type=Path,
                        help="Directory containing a verified offline eng.traineddata file")
    args = parser.parse_args()
    try:
        report = build(args.source, args.output, args.soffice, args.tectonic,
                       include_editable_draft=args.include_editable_draft)
    except ConversionError as error:
        if (args.include_editable_draft and error.code == "ocr_required"
                and args.source.suffix.lower() == ".pdf" and args.tessdata):
            try:
                report = build_ocr_fallback(args.source, args.output, args.tectonic, args.tessdata)
            except ConversionError as fallback_error:
                print(json.dumps({"ok": False, "code": fallback_error.code,
                                  "stage": fallback_error.stage}))
                raise SystemExit(2)
        elif args.include_editable_draft and error.code in {
            "too_many_pages", "unsupported_layout", "ocr_required", "fidelity_check_failed"
        } and args.source.suffix.lower() in {".doc", ".docx"}:
            try:
                report = build_editable_fallback(
                    args.source, args.output, args.soffice, args.tectonic)
            except ConversionError as fallback_error:
                print(json.dumps({"ok": False, "code": fallback_error.code,
                                  "stage": fallback_error.stage}))
                raise SystemExit(2)
        else:
            print(json.dumps({"ok": False, "code": error.code, "stage": error.stage}))
            raise SystemExit(2)
    print(json.dumps({"ok": True, **report}))


if __name__ == "__main__":
    main()
