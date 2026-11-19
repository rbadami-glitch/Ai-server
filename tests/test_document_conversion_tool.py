"""Local safety checks for the opt-in Open WebUI conversion tool."""

import asyncio
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch


def module(name, **members):
    result = types.ModuleType(name)
    result.__dict__.update(members)
    if name in {"open_webui", "open_webui.models", "open_webui.routers",
                "open_webui.storage", "starlette"}:
        result.__path__ = []
    return result


class FakeBaseModel:
    def __init__(self, **values):
        for name in self.__class__.__annotations__:
            setattr(self, name, values.get(name, getattr(self.__class__, name)))


with patch.dict(sys.modules, {"pydantic": module(
    "pydantic", BaseModel=FakeBaseModel, Field=lambda default, **_: default
)}):
    path = Path(__file__).resolve().parents[1] / "openwebui-tools" / "document_to_latex_tool.py"
    spec = importlib.util.spec_from_file_location("document_to_latex_tool", path)
    tool_code = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool_code)


def valid_zip(report=None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("template.tex", "example")
        archive.writestr("preview.pdf", b"%PDF-1.7\n")
        archive.writestr("README.txt", "review")
        archive.writestr("conversion-report.json", json.dumps(
            report or {"status": "fixed_layout_draft_requires_human_review"}))
    return output.getvalue()


class DocumentConversionToolTests(unittest.TestCase):
    def test_chat_file_ids_accept_only_attached_uuid_files(self):
        selected = str(uuid.uuid4())
        self.assertEqual([selected], tool_code._chat_file_ids([
            {"id": selected, "type": "file"}, {"id": selected},
            {"id": "https://example.test/file", "type": "file"},
            {"id": str(uuid.uuid4()), "type": "url"},
        ]))

    def test_workspace_tool_reads_chat_attachments_from_metadata(self):
        selected = str(uuid.uuid4())
        tool = tool_code.Tools()
        tool.valves = types.SimpleNamespace(
            worker_python=sys.executable, worker_script=sys.executable,
            libreoffice_exe=sys.executable, tectonic_exe=sys.executable,
            worker_localappdata=tempfile.gettempdir(),
            timeout_seconds=30,
        )
        result = asyncio.run(tool.convert_attached_document_to_latex(
            file_id=str(uuid.uuid4()), __request__=object(),
            __user__={"id": "user"}, __files__=[],
            __metadata__={"files": [{"id": selected, "type": "file"}]},
        ))
        self.assertIn("not attached", result)
        result = asyncio.run(tool.convert_attached_document_to_latex(
            __request__=object(), __user__={"id": "user"},
            __files__=[], __metadata__={"files": []},
        ))
        self.assertIn("Attach exactly one", result)

    def test_archive_must_have_verified_report_and_preview(self):
        tool_code._verify_zip(valid_zip())
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            tool_code._verify_zip(b"not a zip")

    def test_reflowing_fallback_requires_coverage_and_explicit_visual_limit(self):
        report = {"status": "reflowing_word_draft_requires_human_review",
                  "text_coverage": 0.95, "visual_fidelity_verified": False,
                  "page_geometry_match": False}
        self.assertEqual(report, tool_code._verify_zip(valid_zip(report)))
        with self.assertRaises(ValueError):
            tool_code._verify_zip(valid_zip({**report, "text_coverage": 0.5}))
        with self.assertRaises(ValueError):
            tool_code._verify_zip(valid_zip({**report, "visual_fidelity_verified": True}))
        with self.assertRaises(ValueError):
            tool_code._verify_zip(valid_zip({**report, "page_geometry_match": None}))

    def test_missing_context_and_configuration_do_not_claim_success(self):
        tool = tool_code.Tools()
        result = asyncio.run(tool.convert_attached_document_to_latex())
        self.assertIn("context is unavailable", result)
        result = asyncio.run(tool.convert_attached_document_to_latex(
            __request__=object(), __user__={"id": "user"}, __files__=[]
        ))
        self.assertIn("not configured", result)
        self.assertNotIn("Download", result)

    def test_only_owned_attached_file_can_reach_worker_and_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.pdf"
            source_path.write_bytes(b"%PDF-1.7\n")
            saved_path = root / "saved.zip"
            selected = str(uuid.uuid4())
            saved_id = str(uuid.uuid4())
            user = types.SimpleNamespace(id="user")
            source = types.SimpleNamespace(filename="source.pdf", path="source-key")
            saved = types.SimpleNamespace(id=saved_id, user_id="user", path="saved-key")
            calls = {"worker": 0, "upload": 0}
            owner_allowed = True

            class FakeFiles:
                @staticmethod
                async def get_file_by_id_and_user_id(file_id, user_id):
                    return source if owner_allowed and file_id == selected and user_id == user.id else None

                @staticmethod
                async def get_file_by_id(file_id):
                    return saved if file_id == saved_id else None

            class FakeUsers:
                @staticmethod
                async def get_user_by_id(user_id):
                    return user if user_id == user.id else None

            class FakeStorage:
                @staticmethod
                def get_file(key):
                    return str({"source-key": source_path, "saved-key": saved_path}[key])

            class FakeUpload:
                def __init__(self, file, filename, headers):
                    self.file = file

            async def fake_upload(request, file, metadata, process, user):
                calls["upload"] += 1
                saved_path.write_bytes(file.file.getvalue())
                return types.SimpleNamespace(id=saved_id)

            def fake_run(command, **kwargs):
                calls["worker"] += 1
                Path(command[command.index("--output") + 1]).write_bytes(valid_zip())
                return types.SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

            fake_modules = {
                "fastapi": module("fastapi", UploadFile=FakeUpload),
                "starlette": module("starlette"),
                "starlette.datastructures": module("starlette.datastructures", Headers=dict),
                "open_webui": module("open_webui"),
                "open_webui.models": module("open_webui.models"),
                "open_webui.models.files": module("open_webui.models.files", Files=FakeFiles),
                "open_webui.models.users": module("open_webui.models.users", Users=FakeUsers),
                "open_webui.routers": module("open_webui.routers"),
                "open_webui.routers.files": module("open_webui.routers.files", upload_file_handler=fake_upload),
                "open_webui.storage": module("open_webui.storage"),
                "open_webui.storage.provider": module("open_webui.storage.provider", Storage=FakeStorage),
            }
            tool = tool_code.Tools()
            tool.valves = types.SimpleNamespace(
                worker_python=sys.executable, worker_script=sys.executable,
                libreoffice_exe=sys.executable, tectonic_exe=sys.executable,
                worker_localappdata=root,
                timeout_seconds=30,
            )
            with patch.dict(sys.modules, fake_modules), patch.object(tool_code.subprocess, "run", fake_run):
                kwargs = {"__request__": object(), "__user__": {"id": "user"},
                          "__files__": [],
                          "__metadata__": {"files": [{"id": selected, "type": "file"}]}}
                wrong = asyncio.run(tool.convert_attached_document_to_latex(
                    file_id=str(uuid.uuid4()), **kwargs
                ))
                self.assertIn("not attached", wrong)
                self.assertEqual(0, calls["worker"])

                owner_allowed = False
                denied = asyncio.run(tool.convert_attached_document_to_latex(**kwargs))
                self.assertIn("not owned", denied)
                self.assertEqual(0, calls["worker"])

                owner_allowed = True
                completed = asyncio.run(tool.convert_attached_document_to_latex(**kwargs))
                self.assertIn("Download", completed)
                self.assertIn("do not reflow", completed)
                self.assertEqual(1, calls["worker"])
                self.assertEqual(1, calls["upload"])


if __name__ == "__main__":
    unittest.main()
