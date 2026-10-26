"""Offline checks for the GPU history API; no server services are contacted."""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "GPU usage" / "CW-GPU-Dashboard.py"
SPEC = importlib.util.spec_from_file_location("cw_gpu_dashboard", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = MODULE.HistoryStore(Path(self.temporary.name) / "history")
        self.now = datetime.now().astimezone().replace(
            year=2026, month=10, day=22, hour=13, minute=0,
            second=0, microsecond=0,
        )

    def record(self, when):
        self.store.record({
            "checked_at": when.isoformat(),
            "gpus": [{"index": 0, "used_mib": 1024, "free_mib": 7168,
                      "total_mib": 8192}],
            "services": [],
        })

    def test_custom_minutes_and_hours(self):
        for minutes_ago in (3, 20, 45, 125):
            self.record(self.now - timedelta(minutes=minutes_ago))
        self.assertEqual(len(self.store.query("minutes", minutes=5, now=self.now)["samples"]), 1)
        self.assertEqual(len(self.store.query("minutes", minutes=30, now=self.now)["samples"]), 2)
        self.assertEqual(len(self.store.query("hours", hours=1, now=self.now)["samples"]), 3)
        with self.assertRaises(ValueError):
            self.store.query("minutes", minutes=0, now=self.now)

    def test_midnight_and_seven_day_retention(self):
        midnight = self.now.replace(hour=0, minute=10)
        self.record(midnight - timedelta(minutes=15))
        self.record(midnight - timedelta(minutes=3))
        self.assertEqual(len(self.store.query("minutes", minutes=30, now=midnight)["samples"]), 2)
        for days_ago in range(7, -1, -1):
            self.record(self.now - timedelta(days=days_ago, hours=1))
        self.assertEqual(len(list(self.store.directory.glob("*.json"))), 7)

    def test_server_history_path_is_outside_checkout(self):
        self.assertEqual(
            MODULE.HISTORY_DIR,
            Path(r"E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History"),
        )
        self.assertNotIn("github", str(MODULE.HISTORY_DIR).lower())

    def test_request_logs_show_metadata_without_prompts_or_fake_token_counts(self):
        directory = Path(self.temporary.name) / "requests"
        directory.mkdir()
        now = datetime(2026, 10, 22, 13, tzinfo=timezone.utc)
        day = now.date().isoformat()
        first = {"schema": 1, "at": int((now - timedelta(minutes=3)).timestamp()),
                 "listener": "11434", "route": "/api/chat", "model": "gemma4:31b",
                 "source_ip": "10.104.74.13", "status": 200,
                 "prompt_tokens": 21, "completion_tokens": 3, "duration_ms": 1234,
                 "prompt": "secret prompt", "authorization": "secret key"}
        second = {"schema": 1, "at": int((now - timedelta(minutes=2)).timestamp()),
                  "listener": "11435", "route": "/v1/chat/completions",
                  "model": "llama4:latest", "source_ip": "127.0.0.1", "status": 500}
        dropped = {"schema": 1, "kind": "dropped", "listener": "11434",
                   "at": int(now.timestamp()), "dropped": 2}
        (directory / f"ollama-11434-{day}.jsonl").write_text(
            json.dumps(first) + "\n" + json.dumps(dropped) + "\n", encoding="utf-8")
        (directory / f"ollama-11435-{day}.jsonl").write_text(
            json.dumps(second) + "\n", encoding="utf-8")
        result = MODULE.request_logs(1, directory, now)
        self.assertEqual(result["files_found"], 2)
        self.assertEqual(result["dropped"], 2)
        self.assertEqual([row["model"] for row in result["rows"]],
                         ["llama4:latest", "gemma4:31b"])
        self.assertIsNone(result["rows"][0]["total_tokens"])
        self.assertEqual(result["rows"][1]["total_tokens"], 24)
        self.assertEqual(result["rows"][1]["duration_ms"], 1234)
        self.assertNotIn("secret", json.dumps(result))

    def test_request_logs_missing_files_are_not_zero_request_proof(self):
        directory = Path(self.temporary.name) / "missing"
        result = MODULE.request_logs(24, directory,
                                     datetime(2026, 10, 22, 13, tzinfo=timezone.utc))
        self.assertFalse(result["recorder_configured"])
        self.assertEqual(result["rows"], [])
        with self.assertRaises(ValueError):
            MODULE.request_logs(2, directory)


if __name__ == "__main__":
    unittest.main()
