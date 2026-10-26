"""Offline checks for the GPU history API; no server services are contacted."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
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

    def test_openwebui_activity_reads_saved_metadata_without_chat_text(self):
        path = Path(self.temporary.name) / "webui.db"
        start = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        at = int((start + timedelta(minutes=5)).timestamp())
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE chat_message (created_at INTEGER, role TEXT, model_id TEXT, usage TEXT, content TEXT)")
            db.executemany("INSERT INTO chat_message VALUES (?,?,?,?,?)", [
                (at, "user", "", None, "secret prompt"),
                (at + 10, "assistant", "curtiss-wright-policies",
                 json.dumps({"prompt_tokens": 12, "completion_tokens": 8}), "secret answer"),
                ((at + 20) * 1000, "assistant", "devstral", None, "another secret answer"),
            ])
            db.commit()
        result = MODULE.openwebui_activity(start, start + timedelta(hours=1), path)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(all("type" not in row for row in result["rows"]))
        self.assertEqual(result["rows"][0]["at"], at + 20)
        self.assertIsNone(result["rows"][0]["total_tokens"])
        self.assertEqual(result["rows"][1]["model"], "curtiss-wright-policies")
        self.assertEqual(result["rows"][1]["total_tokens"], 20)
        self.assertNotIn("secret", json.dumps(result))

    def test_openwebui_activity_missing_database_and_invalid_range(self):
        start = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        result = MODULE.openwebui_activity(start, start + timedelta(hours=1),
                                           Path(self.temporary.name) / "missing.db")
        self.assertFalse(result["available"])
        with self.assertRaises(ValueError):
            MODULE.openwebui_activity(start, start + timedelta(days=8))


if __name__ == "__main__":
    unittest.main()
