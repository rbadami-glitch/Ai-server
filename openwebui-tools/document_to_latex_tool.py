"""Open WebUI Workspace tool: attached document to reviewed LaTeX draft.

The administrator configures an offline worker. The model cannot supply a
filesystem path, and the tool reads only a file attached to this chat and owned
by the requesting user. No success link is returned until upload verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field


def _chat_file_ids(files: list[dict] | None) -> list[str]:
    result = []
    for item in files or []:
        if not isinstance(item, dict) or item.get("type", "file") != "file":
            continue
        candidate = item.get("id")
        try:
            normalized = str(uuid.UUID(candidate))
        except (TypeError, ValueError, AttributeError):
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def _verify_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        required = {"template.tex", "preview.pdf", "conversion-report.json", "README.txt"}
        if not required.issubset(archive.namelist()) or archive.testzip() is not None:
            raise ValueError("Incomplete LaTeX draft ZIP")
        if not archive.read("preview.pdf", pwd=None).startswith(b"%PDF-"):
            raise ValueError("The preview is not a PDF")
        report = json.loads(archive.read("conversion-report.json"))
        if report.get("status") not in {
            "fixed_layout_draft_requires_human_review",
            "reflowing_word_draft_requires_human_review",
            "ocr_text_draft_requires_human_review",
        }:
            raise ValueError("The worker did not verify the draft")
        if report.get("status") == "reflowing_word_draft_requires_human_review":
            if (report.get("text_coverage", 0) < 0.90
                    or report.get("visual_fidelity_verified") is not False
                    or not isinstance(report.get("page_geometry_match"), bool)):
                raise ValueError("The editable draft lacks a valid quality report")
        if report.get("status") == "ocr_text_draft_requires_human_review":
            if (report.get("recognized_characters", 0) < 10
                    or report.get("visual_fidelity_verified") is not False
                    or report.get("ocr_verified") is not False):
                raise ValueError("The OCR draft lacks a valid uncertainty report")
        editable = report.get("editable_draft")
        if (report.get("status") == "fixed_layout_draft_requires_human_review"
                and isinstance(editable, dict)
                and editable.get("status") == "reflowing_draft_requires_human_review"):
            if (not {"editable.tex", "editable-preview.pdf", "semantic-report.json"}.issubset(archive.namelist())
                    or not archive.read("editable-preview.pdf").startswith(b"%PDF-")
                    or json.loads(archive.read("semantic-report.json")) != editable
                    or editable.get("text_coverage", 0) < 0.90):
                raise ValueError("The optional editable draft is incomplete")
        return report


def _download_interceptor_code(files: list[dict]) -> str:
    """Use Open WebUI's execute event to download verified files in the chat tab.

    Its standard Markdown links and file-modal links always open a new tab. The
    handler is restricted to the exact files this Tool just stored for the user.
    """
    names = {item["id"]: item["name"] for item in files}
    return r"""
const names = %s;
const key = '__verifiedLatexDownloads';
const state = window[key] || (window[key] = { names: {}, installed: false, timer: null });
Object.assign(state.names, names);
if (!state.installed) {
  state.installed = true;
  const showStatus = (message, hold = false, percent = null) => {
    let notice = document.getElementById('verified-latex-download-status');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'verified-latex-download-status';
      notice.setAttribute('role', 'status');
      notice.setAttribute('aria-live', 'polite');
      document.body.appendChild(notice);
    }
    const toaster = document.querySelector('[data-sonner-toaster]');
    const theme = toaster ? getComputedStyle(toaster) : null;
    const dark = document.documentElement.classList.contains('dark');
    const color = (variable, fallback) =>
      theme?.getPropertyValue(variable).trim() || fallback;
    Object.assign(notice.style, {
      position: 'fixed', right: '1rem', top: '1rem', zIndex: '9999',
      maxWidth: 'min(26rem, calc(100vw - 2rem))',
      padding: '0.875rem 1rem', borderRadius: '0.75rem',
      fontFamily: 'inherit', fontSize: '0.875rem', lineHeight: '1.25rem',
      backgroundColor: color('--normal-bg', dark ? '#1f2937' : '#ffffff'),
      color: color('--normal-text', dark ? '#f9fafb' : '#111827'),
      border: `1px solid ${color('--normal-border', dark ? '#374151' : '#e5e7eb')}`,
      boxShadow: '0 4px 12px #0002'
    });
    notice.textContent = message;
    if (hold) {
      const progress = document.createElement('progress');
      progress.max = 100;
      if (percent !== null) progress.value = percent;
      Object.assign(progress.style, {
        display: 'block', width: '100%%', height: '0.5rem', marginTop: '0.5rem',
        accentColor: '#3b82f6'
      });
      notice.appendChild(progress);
    }
    clearTimeout(state.timer);
    if (!hold) state.timer = setTimeout(() => notice.remove(), 8000);
  };
  document.addEventListener('click', async (event) => {
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest('a[href]');
    const card = link ? null : event.target.closest('button');
    const cardEntry = card ? Object.entries(state.names).find(([, name]) =>
      card.textContent?.includes(name)) : null;
    if (!link && !cardEntry) return;
    const url = new URL(link ? link.href :
      `/api/v1/files/${cardEntry[0]}/content?attachment=true`, window.location.href);
    const match = url.pathname.match(/^\/api\/v1\/files\/([0-9a-f-]{36})\/content$/i);
    if (!match || url.origin !== window.location.origin || !state.names[match[1]]) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const filename = state.names[match[1]];
    showStatus(`Connecting to download: ${filename}`, true);
    try {
      const token = window.localStorage.getItem('token');
      const response = await fetch(url.href, {
        credentials: 'same-origin',
        headers: token ? { Authorization: `Bearer ${token}` } : {}
      });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const total = Number(response.headers.get('Content-Length')) || 0;
      const chunks = [];
      let received = 0;
      if (response.body) {
        const reader = response.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.byteLength;
          const percent = total ? Math.min(100, Math.round(received * 100 / total)) : null;
          const amount = (received / 1048576).toFixed(1);
          showStatus(total ? `Downloading ${filename}: ${percent}%%` :
            `Downloading ${filename}: ${amount} MB`, true, percent);
        }
      } else {
        chunks.push(await response.blob());
      }
      showStatus(`Preparing ${filename} for your browser…`, true, 100);
      const blob = new Blob(chunks, {
        type: response.headers.get('Content-Type') || 'application/octet-stream'
      });
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      showStatus(`Sent to your browser: ${filename}. Check Downloads.`);
    } catch (error) {
      console.error('LaTeX download failed', error);
      showStatus('Download failed. Please try again.');
    }
  }, true);
}
""" % json.dumps(names)


class Tools:
    class Valves(BaseModel):
        worker_python: str = Field(default="", description="Absolute Python executable in an isolated conversion environment")
        worker_script: str = Field(default="", description="Absolute document_conversion_worker.py path")
        libreoffice_exe: str = Field(default="", description="Absolute soffice.com path")
        tectonic_exe: str = Field(default="", description="Absolute tectonic.exe path with offline cache ready")
        worker_localappdata: str = Field(default="", description="Dedicated LOCALAPPDATA folder containing the Tectonic cache")
        include_editable_draft: bool = Field(default=False, description="Experimental Word reflowing draft; keep disabled for shared rollout")
        ocr_tessdata: str = Field(default="", description="Optional offline OCR language-data folder for experimental scanned PDFs")
        timeout_seconds: int = Field(default=240, ge=30, le=600)

    def __init__(self):
        self.valves = self.Valves()

    async def convert_attached_document_to_latex(
        self,
        file_id: str = "",
        __request__=None,
        __user__=None,
        __files__=None,
        __metadata__=None,
        __event_emitter__=None,
    ) -> str:
        """Create a downloadable LaTeX draft from one attached Word or PDF file.

        Use this when the user asks to turn an attached document into a LaTeX
        template. The default output preserves page appearance but longer edits
        do not reflow. Experimental Word and scanned-PDF fallbacks can reflow
        text, but their visual appearance and OCR accuracy are not verified.
        Every output requires human review.

        :param file_id: Optional ID from the current chat when multiple files are attached
        :return: A download only after conversion, rendering, comparison, and storage verification
        """
        if __request__ is None or not isinstance(__user__, dict) or not __user__.get("id"):
            return "I could not convert the document: chat user context is unavailable."
        config = self.valves
        configured = (config.worker_python, config.worker_script,
                      config.libreoffice_exe, config.tectonic_exe)
        if (not all(configured)
                or not all(Path(value).is_absolute() and Path(value).is_file() for value in configured)
                or not config.worker_localappdata
                or not Path(config.worker_localappdata).is_absolute()
                or not Path(config.worker_localappdata).is_dir()):
            return "Document conversion is not configured. No file was created."

        metadata_files = __metadata__.get("files") if isinstance(__metadata__, dict) else None
        attached = _chat_file_ids(metadata_files or __files__)
        if file_id:
            try:
                chosen = str(uuid.UUID(file_id))
            except (TypeError, ValueError, AttributeError):
                return "The selected file ID is invalid. No file was created."
            if chosen not in attached:
                return "The selected file is not attached to this chat. No file was created."
        elif len(attached) == 1:
            chosen = attached[0]
        else:
            return "Attach exactly one Word or PDF file, or select a file ID from this chat. No file was created."

        stage = "user_lookup"
        try:
            from fastapi import UploadFile
            from starlette.datastructures import Headers
            from open_webui.models.files import Files
            from open_webui.models.users import Users
            from open_webui.routers.files import upload_file_handler
            from open_webui.storage.provider import Storage

            user = await Users.get_user_by_id(__user__["id"])
            if user is None:
                return "Your account could not be verified. No file was created."
            stage = "source_lookup"
            source_file = await Files.get_file_by_id_and_user_id(chosen, user.id)
            if source_file is None or not source_file.path:
                return "The attached file is unavailable or not owned by your account. No file was created."
            if Path(source_file.filename).suffix.lower() not in {".doc", ".docx", ".pdf"}:
                return "Only attached DOC, DOCX, or PDF files can be converted. No file was created."

            stage = "source_storage"
            stored_path = Path(await asyncio.to_thread(Storage.get_file, source_file.path))
            if not stored_path.is_file() or stored_path.stat().st_size > 15 * 1024 * 1024:
                return "The attached file is missing or exceeds the 15 MB limit. No file was created."

            stage = "offline_conversion"
            with tempfile.TemporaryDirectory(prefix="openwebui-latex-tool-") as temporary:
                output = Path(temporary) / "draft.zip"
                command = [config.worker_python, config.worker_script,
                           "--source", str(stored_path), "--output", str(output),
                           "--soffice", config.libreoffice_exe,
                           "--tectonic", config.tectonic_exe]
                if getattr(config, "include_editable_draft", False):
                    command.append("--include-editable-draft")
                    if getattr(config, "ocr_tessdata", ""):
                        command.extend(["--tessdata", config.ocr_tessdata])
                safe_env = {name: os.environ[name] for name in
                            ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "APPDATA",
                             "LOCALAPPDATA", "USERPROFILE") if name in os.environ}
                safe_env["PYTHONNOUSERSITE"] = "1"
                safe_env["LOCALAPPDATA"] = config.worker_localappdata

                def execute():
                    return subprocess.run(
                        command, capture_output=True, text=True, shell=False,
                        timeout=config.timeout_seconds, env=safe_env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )

                try:
                    result = await asyncio.to_thread(execute)
                except subprocess.TimeoutExpired:
                    return "Document conversion timed out. No file was created."
                if result.returncode or not output.is_file():
                    try:
                        detail = json.loads(result.stdout.splitlines()[-1])
                        code = detail.get("code", "conversion_failed")
                    except (IndexError, ValueError, AttributeError):
                        code = "conversion_failed"
                    return f"This document could not be converted reliably ({code}). No download was created."
                data = output.read_bytes()
                if len(data) > 25 * 1024 * 1024:
                    return "The converted package exceeds the 25 MB limit. No download was created."
                report = _verify_zip(data)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    preview_data = archive.read("preview.pdf")

                async def store_verified(payload: bytes, filename: str, mime: str):
                    nonlocal stage
                    stage = "file_upload"
                    upload = UploadFile(
                        file=io.BytesIO(payload), filename=filename,
                        headers=Headers({"content-type": mime}),
                    )
                    file_item = await upload_file_handler(
                        __request__, file=upload, metadata={}, process=False, user=user,
                    )
                    stage = "stored_file_verification"
                    saved = await Files.get_file_by_id(file_item.id)
                    if saved is None or saved.user_id != user.id or not saved.path:
                        raise ValueError("Stored file ownership could not be verified")
                    saved_path = Path(await asyncio.to_thread(Storage.get_file, saved.path))
                    saved_data = await asyncio.to_thread(saved_path.read_bytes)
                    if hashlib.sha256(saved_data).digest() != hashlib.sha256(payload).digest():
                        raise ValueError("Stored file bytes changed")
                    if mime == "application/zip":
                        _verify_zip(saved_data)
                    elif not saved_data.startswith(b"%PDF-"):
                        raise ValueError("Stored preview is not a PDF")
                    return saved.id, f"/api/v1/files/{saved.id}/content?attachment=true"

                project_name = f"latex-project-{uuid.uuid4().hex[:12]}.zip"
                project_id, project_url = await store_verified(
                    data, project_name, "application/zip")
                files = [{"type": "file", "id": project_id, "name": project_name,
                          "url": project_url}]
                preview_url = None
                try:
                    preview_name = f"latex-preview-{uuid.uuid4().hex[:12]}.pdf"
                    preview_id, preview_url = await store_verified(
                        preview_data, preview_name, "application/pdf")
                    files.append({"type": "file", "id": preview_id,
                                  "name": preview_name, "url": preview_url})
                except Exception:
                    preview_url = None
                if __event_emitter__ is not None:
                    try:
                        await __event_emitter__({"type": "execute", "data": {
                            "code": _download_interceptor_code(files)}})
                    except Exception:
                        pass
                    try:
                        await __event_emitter__({"type": "files", "data": {"files": files}})
                    except Exception:
                        pass
                downloads = (
                    f"[Download preview PDF]({preview_url}) and "
                    if preview_url else "Preview PDF is inside the ZIP. "
                ) + f"[Download LaTeX project ZIP]({project_url}). "
                if report["status"] == "reflowing_word_draft_requires_human_review":
                    geometry_notice = (
                        " Page size or orientation changed in the preview."
                        if not report["page_geometry_match"] else ""
                    )
                    return (
                        "Editable LaTeX draft created: " + downloads +
                        "Compare every preview page with the source. "
                        "Text coverage passed, but the original appearance was not verified."
                        + geometry_notice
                    )
                if report["status"] == "ocr_text_draft_requires_human_review":
                    return (
                        "OCR text draft created: " + downloads +
                        "The recognized words and formatting are unverified. "
                        "Compare every page with the uploaded PDF before editing or using it."
                    )
                editable = report.get("editable_draft")
                editable_notice = (
                    "The ZIP also contains editable.tex and editable-preview.pdf, a separate "
                    "reflowing draft. Its visual formatting was not verified; compare it with "
                    "the source before using it. "
                    if isinstance(editable, dict)
                    and editable.get("status") == "reflowing_draft_requires_human_review"
                    else (
                        "The reflowing draft was withheld because its rendered text overlaps. "
                        if isinstance(editable, dict)
                        and editable.get("reason") == "editable_text_overlap"
                        else "No reviewed reflowing version was produced. "
                    )
                )
                return (
                    "Fixed-layout LaTeX draft created: " + downloads +
                    "Inspect every preview page. Text is editable but longer "
                    "changes do not reflow; this is not a fully semantic template. "
                    + editable_notice
                )
        except Exception as error:
            return ("Document conversion failed. No verified download is available. "
                    f"Diagnostic: {stage} / {type(error).__name__}.")
