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


def _runs(text: str) -> str:
    """Render plain text and simple **bold** spans without treating it as HTML."""
    parts = re.split(r"(\*\*[^*\n]+\*\*)", text)
    runs = []
    for part in parts:
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**")
        value = part[2:-2] if bold else part
        properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
        runs.append(f'<w:r>{properties}<w:t xml:space="preserve">{escape(value)}</w:t></w:r>')
    return "".join(runs)


def _paragraph(text: str, style: str = "Normal", list_id: int | None = None) -> str:
    properties = f'<w:pStyle w:val="{style}"/>'
    if list_id is not None:
        properties += (
            '<w:numPr><w:ilvl w:val="0"/>'
            f'<w:numId w:val="{list_id}"/></w:numPr>'
        )
    return f"<w:p><w:pPr>{properties}</w:pPr>{_runs(text)}</w:p>"


def _content_paragraphs(content: str, title: str) -> list[str]:
    paragraphs = []
    body_lines = []

    def flush() -> None:
        if body_lines:
            paragraphs.append(_paragraph(" ".join(body_lines)))
            body_lines.clear()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        bullet = re.fullmatch(r"[-*•]\s+(.+)", line)
        numbered = re.fullmatch(r"\d+[.)]\s+(.+)", line)
        if heading:
            flush()
            heading_text = heading.group(2).strip()
            if heading_text.casefold() != title.casefold():
                style = "Heading1" if len(heading.group(1)) == 1 else "Heading2"
                paragraphs.append(_paragraph(heading_text, style))
        elif bullet:
            flush()
            paragraphs.append(_paragraph(bullet.group(1), "ListBullet", 1))
        elif numbered:
            flush()
            paragraphs.append(_paragraph(numbered.group(1), "ListNumber", 2))
        elif len(line) <= 80 and line.endswith(":"):
            flush()
            paragraphs.append(_paragraph(line[:-1], "Heading1"))
        else:
            body_lines.append(line)
    flush()
    return paragraphs


def make_docx(title: str, content: str) -> bytes:
    """Create a styled .docx entirely with Python's standard library."""
    title = _clean_text(title, _MAX_TITLE, "Title")
    content = _clean_text(content, _MAX_CONTENT, "Content")
    paragraphs = [_paragraph(title, "Title")]
    paragraphs.extend(_content_paragraphs(content, title))

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
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
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
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        '</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{_WORD_NS}">'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:sz w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:keepNext/><w:spacing w:after="320"/></w:pPr>'
        '<w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="44"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:keepNext/><w:spacing w:before="260" w:after="100"/></w:pPr>'
        '<w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/></w:pPr>'
        '<w:rPr><w:b/><w:color w:val="24476B"/><w:sz w:val="26"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
        '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/>'
        '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr></w:style>'
        '</w:styles>'
    )
    numbering = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:numbering xmlns:w="{_WORD_NS}">'
        '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
        '<w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs>'
        '<w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs>'
        '<w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
        '</w:numbering>'
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/numbering.xml", numbering)
    data = output.getvalue()
    validate_docx(data)
    return data


def validate_docx(data: bytes) -> None:
    """Reject corrupt packages before a file is registered or reported as done."""
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        required = {
            "[Content_Types].xml", "_rels/.rels", "word/document.xml",
            "word/_rels/document.xml.rels", "word/styles.xml", "word/numbering.xml",
        }
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
        :param content: Complete document text. Use # or ## for headings, - for bullets, 1. for numbered items, **text** for bold, and blank lines between paragraphs.
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
