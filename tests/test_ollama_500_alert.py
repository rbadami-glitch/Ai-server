"""Offline checks for first-error alerting with synthetic recorder records."""

import json
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "OpenWebUi Monitor"
sys.path.insert(0, str(SOURCE))
import ollama_500_alert as alert  # noqa: E402


class Ollama500AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_path = self.root / "state.json"
        self.config_path = self.root / "config.json"
        self.api_key_path = self.root / "resend-api-key.txt"
        self.api_key_path.write_text("re_test_key", encoding="utf-8")
        self.config = {
            "resend_api_key_file": str(self.api_key_path),
            "sender": "alerts@example.test",
            "recipients": ["operator@example.test"], "rearm_mode": "quiet",
            "quiet_minutes": 30,
        }
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.at = 1793180000

    def record(self, port, status, at=None, **extra):
        at = self.at if at is None else at
        path = self.root / f"ollama-{port}-{datetime.fromtimestamp(at, timezone.utc).date()}.jsonl"
        data = {"schema": 1, "at": at, "listener": port, "route": "/api/chat",
                "model": "gemma4:31b", "source_ip": "10.104.74.10", "status": status,
                **extra}
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(data) + "\n")
        return path

    def test_first_500_per_listener_and_quiet_rearm(self):
        state = alert.new_state(self.root, self.at)
        self.record("11434", 200)
        self.record("11434", 500, self.at + 1, prompt="private text")
        self.record("11434", 500, self.at + 2)
        self.record("11435", 500, self.at + 3)
        with patch.object(alert, "memory_snapshot", return_value=["No GPU snapshot"]):
            alert.scan(state, self.root, self.config, self.at + 4)
        self.assertEqual(set(state["incidents"]), {"11434", "11435"})
        self.assertIn("11434", state["incidents"]["11434"]["pending"]["subject"])
        self.assertNotIn("private text", state["incidents"]["11434"]["pending"]["body"])
        first_body = state["incidents"]["11434"]["pending"]["body"]
        self.assertIn("10.104.74.10", first_body)
        self.assertEqual(state["incidents"]["11434"]["last_500"], self.at + 2)
        state["incidents"]["11434"]["pending"] = None  # Simulate successful SMTP.
        self.record("11434", 500, self.at + 4)
        alert.scan(state, self.root, self.config, self.at + 5)
        self.assertIsNone(state["incidents"]["11434"]["pending"])
        self.record("11434", 500, self.at + 1805)
        with patch.object(alert, "memory_snapshot", return_value=["No GPU snapshot"]):
            alert.scan(state, self.root, self.config, self.at + 1806)
        self.assertIsNotNone(state["incidents"]["11434"]["pending"])

    def test_existing_history_is_skipped_then_cursor_survives_restart(self):
        self.record("11434", 500)
        state = alert.new_state(self.root, self.at + 1)
        alert.scan(state, self.root, self.config, self.at + 1)
        self.assertEqual(state["incidents"], {})
        self.record("11434", 500, self.at + 2)
        with patch.object(alert, "memory_snapshot", return_value=[]):
            alert.scan(state, self.root, self.config, self.at + 3)
        alert.save_state(self.state_path, state)
        restored = json.loads(self.state_path.read_text(encoding="utf-8"))
        alert.scan(restored, self.root, self.config, self.at + 4)
        self.assertEqual(restored["incidents"], state["incidents"])

    def test_incomplete_record_waits_for_newline(self):
        path = self.record("11434", 200)
        state = alert.new_state(self.root, self.at)
        pending = {"schema": 1, "at": self.at + 1, "listener": "11434",
                   "route": "/api/chat", "model": "gemma4:31b",
                   "source_ip": "10.104.74.10", "status": 500}
        with path.open("ab") as output:
            output.write(json.dumps(pending).encode())
        alert.scan(state, self.root, self.config, self.at + 2)
        self.assertEqual(state["incidents"], {})
        with path.open("ab") as output:
            output.write(b"\n")
        with patch.object(alert, "memory_snapshot", return_value=[]):
            alert.scan(state, self.root, self.config, self.at + 2)
        self.assertIsNotNone(state["incidents"]["11434"]["pending"])

    def test_other_server_errors_do_not_trigger(self):
        state = alert.new_state(self.root, self.at)
        self.record("11434", 503)
        alert.scan(state, self.root, self.config, self.at + 1)
        self.assertIsNone(state["incidents"]["11434"]["pending"])

    def test_success_rearm_and_config_validation(self):
        self.config["rearm_mode"] = "success"
        state = alert.new_state(self.root, self.at)
        with patch.object(alert, "memory_snapshot", return_value=[]):
            for status, at in ((500, self.at), (500, self.at + 1), (200, self.at + 2)):
                event = alert._event({"schema": 1, "at": at, "listener": "11434",
                                      "route": "/api/chat", "model": "gemma4:31b",
                                      "source_ip": "10.104.74.10", "status": status})
                alert.process_event(state, event, self.config)
            self.assertFalse(state["incidents"]["11434"]["armed"])
            state["incidents"]["11434"]["pending"] = None
            alert.process_event(state, alert._event({"schema": 1, "at": self.at + 3,
                "listener": "11434", "route": "/api/chat", "model": "gemma4:31b",
                "source_ip": "10.104.74.10", "status": 200}), self.config)
            self.assertTrue(state["incidents"]["11434"]["armed"])
        self.assertEqual(alert.load_config(self.config_path)["sender"], "alerts@example.test")
        self.config["resend_api_key_file"] = str(self.root / "missing-key.txt")
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        with self.assertRaises(ValueError):
            alert.load_config(self.config_path)

    def test_delivery_is_persisted_and_not_repeated_after_restart(self):
        alert.save_state(self.state_path, alert.new_state(self.root, self.at))
        self.record("11434", 500, self.at + 1)
        with (patch.object(alert, "memory_snapshot", return_value=[]),
              patch.object(alert, "send_email") as deliver,
              patch.object(alert.time, "time", return_value=self.at + 2)):
            alert.run(self.root, self.state_path, self.config_path, once=True)
            alert.run(self.root, self.state_path, self.config_path, once=True)
        deliver.assert_called_once()
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["incidents"]["11434"]["pending"])

    def test_resend_request_uses_stable_idempotency_key(self):
        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        with patch.object(alert, "urlopen", return_value=Response(b'{"id":"email-id"}')) as open_api:
            alert.send_email(self.config, "First 500", "Metadata report", "incident-123")
        request = open_api.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("Idempotency-key"), "incident-123")
        self.assertEqual(json.loads(request.data)["text"], "Metadata report")
        self.assertNotIn("re_test_key", request.data.decode())

    def test_failed_send_retries_same_email_after_restart(self):
        alert.save_state(self.state_path, alert.new_state(self.root, self.at))
        self.record("11435", 500, self.at + 1)
        with (patch.object(alert, "memory_snapshot", return_value=[]),
              patch.object(alert, "send_email", side_effect=[OSError("offline"), None]) as deliver,
              patch.object(alert.logging, "error"),
              patch.object(alert.time, "time", return_value=self.at + 2)):
            alert.run(self.root, self.state_path, self.config_path, once=True)
            pending = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(pending["incidents"]["11435"]["pending"])
            alert.run(self.root, self.state_path, self.config_path, once=True)
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(deliver.call_args_list[0].args, deliver.call_args_list[1].args)
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["incidents"]["11435"]["pending"])


if __name__ == "__main__":
    unittest.main()
