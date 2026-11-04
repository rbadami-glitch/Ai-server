"""Word-only Open WebUI tool for a private trial on version 0.12.5."""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_MAX_TITLE = 160
_MAX_CONTENT = 20_000


def _clean_text(value: str, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    # Keep only characters permitted by XML 1.0.
    value = "".join(
        ch
        for ch in value
        if ch in "\n\t"
        or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or 0x10000 <= ord(ch) <= 0x10FFFF
    )
    value = value.strip()
    if not value:
        raise ValueError(f"{label} cannot be empty")
    if len(value) > limit:
        raise ValueError(f"{label} is too long (maximum {limit} characters)")
    return value


def _paragraph(text: str, title: bool = False) -> str:
    if not text:
        return "<w:p/>"
    lines = text.split("\n")
    runs = "<w:br/>".join(
        f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in lines
    )
    formatting = "<w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr>" if title else ""
    spacing = "<w:pPr><w:spacing w:after=\"240\"/></w:pPr>" if title else ""
    return f"<w:p>{spacing}<w:r>{formatting}{runs}</w:r></w:p>"


def make_docx(title: str, content: str) -> bytes:
    """Create a basic, valid .docx entirely with Python's standard library."""
    title = _clean_text(title, _MAX_TITLE, "Title")
    content = _clean_text(content, _MAX_CONTENT, "Content")
    paragraphs = [_paragraph(title, title=True)]
    paragraphs.extend(_paragraph(block) for block in content.split("\n\n"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_WORD_NS}"><w:body>'
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        '</w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_CONTENT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{DOCX_MIME}.main+xml"/>'
        '</Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    data = output.getvalue()
    validate_docx(data)
    return data


def validate_docx(data: bytes) -> None:
    """Reject corrupt packages before a file is registered or reported as done."""
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        if not required.issubset(archive.namelist()) or archive.testzip() is not None:
            raise ValueError("Word document package is incomplete")
        for name in required:
            ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read("word/document.xml"))
        if root.tag != f"{{{_WORD_NS}}}document":
            raise ValueError("Word document has an invalid root element")
        if not root.findall(f".//{{{_WORD_NS}}}t"):
            raise ValueError("Word document has no text")


def _safe_filename(title: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return f"{stem or 'document'}-{uuid.uuid4().hex[:10]}.docx"


class Tools:
    async def create_word_document(
        self,
        title: str,
        content: str,
        __request__=None,
        __user__=None,
        __event_emitter__=None,
    ) -> str:
        """Create and attach a real Word file for the requesting user.

        :param title: A short title for the document
        :param content: The complete document text, with blank lines between paragraphs
        :return: A download link only after the stored file has been verified
        """
        if __request__ is None or not isinstance(__user__, dict) or not __user__.get("id"):
            return "I could not create the Word document: chat user context is unavailable."

        try:
            data = make_docx(title, content)
        except ValueError as error:
            return f"I could not create the Word document: {error}."

        stage = "imports"
        try:
            from fastapi import UploadFile
            from starlette.datastructures import Headers
            from open_webui.models.files import Files
            from open_webui.models.users import Users
            from open_webui.routers.files import upload_file_handler
            from open_webui.storage.provider import Storage

            stage = "user lookup"
            user = await Users.get_user_by_id(__user__["id"])
            if user is None:
                return "I could not create the Word document: user access could not be verified."

            stage = "upload preparation"
            filename = _safe_filename(title)
            upload = UploadFile(
                file=io.BytesIO(data),
                filename=filename,
                headers=Headers({"content-type": DOCX_MIME}),
            )
            stage = "file upload"
            file_item = await upload_file_handler(
                __request__, file=upload, metadata={}, process=False, user=user
            )
            stage = "stored file lookup"
            file_id = file_item.id
            stored = await Files.get_file_by_id(file_id)
            if stored is None or stored.user_id != user.id:
                return "I could not verify the stored Word document. No download is available."

            stage = "stored file verification"
            stored_path = await asyncio.to_thread(Storage.get_file, stored.path)
            stored_data = await asyncio.to_thread(Path(stored_path).read_bytes)
            validate_docx(stored_data)
            if hashlib.sha256(stored_data).digest() != hashlib.sha256(data).digest():
                return "I could not verify the stored Word document. No download is available."

            url = f"/api/v1/files/{file_id}/content?attachment=true"
            if __event_emitter__ is not None:
                try:
                    await __event_emitter__(
                        {
                            "type": "files",
                            "data": {
                                "files": [
                                    {"type": "file", "id": file_id, "name": filename, "url": url}
                                ]
                            },
                        }
                    )
                except Exception:
                    # The verified, authenticated link still works if the file card fails.
                    pass
            return f"Word document created: [Download {filename}]({url})"
        except Exception as error:
            # Never claim success when uploading or checking the file fails.
            return (
                "I could not create and verify the Word document. "
                f"No download is available. Diagnostic: {stage} / {type(error).__name__}."
            )
