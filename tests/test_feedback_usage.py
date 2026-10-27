"""Offline tests using a temporary, synthetic Open WebUI-shaped database."""

import importlib.util
import json
import sqlite3
import sys
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
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("feedback_dashboard", SCRIPT)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "webui.db"
        self.now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE chat_message (
                    id TEXT, chat_id TEXT, user_id TEXT, role TEXT,
                    created_at INTEGER, model_id TEXT, usage TEXT
                );
                CREATE TABLE user (id TEXT, name TEXT);
                CREATE TABLE model (id TEXT, name TEXT);
                CREATE TABLE chat (id TEXT, user_id TEXT);
                INSERT INTO user VALUES ('u1', 'Test User');
                INSERT INTO model VALUES ('m1', 'Test Model');
                INSERT INTO chat VALUES ('c1', 'u1');
            """)
            recent = int((self.now - timedelta(minutes=30)).timestamp())
            old = int((self.now - timedelta(days=8)).timestamp())
            db.executemany("INSERT INTO chat_message VALUES (?,?,?,?,?,?,?)", [
                ("q1", "c1", "u1", "user", recent, "", None),
                ("a1", "c1", "u1", "assistant", recent + 10, "m1",
                 json.dumps({"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})),
                ("a2", "c1", "", "assistant", recent + 20, "m1", None),
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
        self.assertEqual(total["prompt_token_responses"], 1)
        self.assertEqual(total["completion_token_responses"], 1)
        self.assertEqual(data["models"][0]["model"], "Test Model")
        self.assertEqual(data["users"][0]["user"], "Test User")
        self.assertEqual(data["users"][0]["responses"], 2)
        self.assertEqual(data["period_unit"], "hour")
        self.assertEqual(data["user_model_periods"][0]["user"], "Test User")
        self.assertEqual(sum(item["responses"] for item in data["user_model_periods"]), 2)
        self.assertEqual(sum(item["prompts"] for item in data["buckets"]), 1)
        self.assertEqual(sum(item["tokens"] for item in data["buckets"]), 20)
        self.assertIsNone(data["recent"][0]["tokens"])
        self.assertNotIn("content", json.dumps(data))

    def test_minute_day_and_week_windows(self):
        self.assertEqual(dashboard.read_usage(self.path, "minutes", 10, now=self.now)
                         ["totals"]["prompts"], 0)
        self.assertEqual(dashboard.read_usage(self.path, "week", now=self.now)
                         ["totals"]["prompts"], 1)
        self.assertEqual(dashboard.read_usage(self.path, "week", now=self.now)["period_unit"], "day")
        self.assertEqual(dashboard.read_usage(self.path, "minutes", 40, now=self.now)["period_unit"], "minute")
        self.assertEqual(dashboard.read_usage(self.path, "minutes", 90, now=self.now)["period_unit"], "hour")
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

    def test_breakdown_separates_users_and_models(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO user VALUES ('u2', 'Other User')")
            db.execute("INSERT INTO model VALUES ('m2', 'Other Model')")
            at = int((self.now - timedelta(minutes=20)).timestamp())
            db.execute("INSERT INTO chat_message VALUES (?,?,?,?,?,?,?)",
                       ("a3", "c2", "u2", "assistant", at, "m2",
                        json.dumps({"input_tokens": 5, "output_tokens": 7})))
            db.commit()
        data = dashboard.read_usage(self.path, "hours", 2, now=self.now)
        self.assertEqual(data["totals"]["responses"], 3)
        self.assertEqual(data["totals"]["total_tokens"], 32)
        self.assertEqual(data["totals"]["total_token_responses"], 2)
        self.assertEqual({item["user"] for item in data["users"]},
                         {"Test User", "Other User"})
        self.assertEqual({item["model"] for item in data["models"]},
                         {"Test Model", "Other Model"})
        self.assertEqual(len(data["user_model_periods"]), 2)

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
            content = page.read()
            self.assertIn(b"Usage data", content)
            self.assertIn(b"Feedback data", content)
        with urlopen(base + "/?key=test-key", timeout=2) as page:
            content = page.read()
            self.assertIn(b"Usage data", content)
            self.assertIn(b"Feedback data", content)
        request = Request(base + "/api/usage?view=hours&amount=2",
                          headers={"X-Local-Token": "test-key"})
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
            self.assertIn("totals", payload)
            self.assertIn("user_model_periods", payload)


if __name__ == "__main__":
    unittest.main()
