"""Offline tests using a temporary, synthetic Open WebUI-shaped database."""

import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SCRIPT = Path(__file__).resolve().parents[1] / "OpenWebUi Monitor" / "OpenWebUI-Feedback-Dashboard.py"
SPEC = importlib.util.spec_from_file_location("feedback_dashboard", SCRIPT)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "webui.db"
        self.now = datetime.now().astimezone().replace(microsecond=0)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE chat_message (
                    id TEXT, chat_id TEXT, user_id TEXT, role TEXT,
                    created_at INTEGER, model_id TEXT, usage TEXT
                );
                CREATE TABLE user (id TEXT, name TEXT);
                CREATE TABLE model (id TEXT, name TEXT);
                INSERT INTO user VALUES ('u1', 'Test User');
                INSERT INTO model VALUES ('m1', 'Test Model');
            """)
            recent = int((self.now - timedelta(minutes=30)).timestamp())
            old = int((self.now - timedelta(days=8)).timestamp())
            db.executemany("INSERT INTO chat_message VALUES (?,?,?,?,?,?,?)", [
                ("q1", "c1", "u1", "user", recent, "", None),
                ("a1", "c1", "u1", "assistant", recent + 10, "m1",
                 json.dumps({"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})),
                ("a2", "c1", "u1", "assistant", recent + 20, "m1", None),
                ("q-old", "old-chat", "u1", "user", old, "", None),
            ])
            db.commit()

    def test_range_counts_and_missing_usage(self):
        data = dashboard.read_usage(self.path, "hours", 2, now=self.now)
        total = data["totals"]
        self.assertEqual((total["prompts"], total["responses"]), (1, 2))
        self.assertEqual((total["active_users"], total["active_chats"]), (1, 1))
        self.assertEqual((total["prompt_tokens"], total["completion_tokens"],
                          total["total_tokens"]), (12, 8, 20))
        self.assertEqual(total["total_token_responses"], 1)
        self.assertEqual(data["models"][0]["model"], "Test Model")
        self.assertEqual(sum(item["prompts"] for item in data["buckets"]), 1)
        self.assertEqual(sum(item["tokens"] for item in data["buckets"]), 20)
        self.assertIsNone(data["recent"][0]["tokens"])
        self.assertNotIn("content", json.dumps(data))

    def test_minute_day_and_week_windows(self):
        self.assertEqual(dashboard.read_usage(self.path, "minutes", 10, now=self.now)
                         ["totals"]["prompts"], 0)
        self.assertEqual(dashboard.read_usage(self.path, "week", now=self.now)
                         ["totals"]["prompts"], 1)
        self.assertEqual(dashboard.read_usage(self.path, "day", selected_day=self.now.date().isoformat(),
                                              now=self.now)["totals"]["prompts"], 1)

    def test_invalid_ranges_and_token_values(self):
        with self.assertRaises(ValueError):
            dashboard.usage_window("minutes", 10081, now=self.now)
        with self.assertRaises(ValueError):
            dashboard.usage_window("day", selected_day="2020-01-01", now=self.now)
        self.assertIsNone(dashboard.token_counts('{"total_tokens": "unknown"}'))
        self.assertEqual(dashboard.token_counts('{"input_tokens": 3, "output_tokens": 4}')
                         ["total"], 7)

    def test_usage_page_and_api_require_key(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     dashboard.make_handler(self.path, "test-key"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaises(HTTPError) as denied:
            urlopen(base + "/usage", timeout=2)
        self.assertEqual(denied.exception.code, 403)
        with urlopen(base + "/usage?key=test-key", timeout=2) as page:
            self.assertIn(b"Chat usage", page.read())
        request = Request(base + "/api/usage?view=hours&amount=2",
                          headers={"X-Local-Token": "test-key"})
        with urlopen(request, timeout=2) as response:
            self.assertIn("totals", json.load(response))


if __name__ == "__main__":
    unittest.main()
