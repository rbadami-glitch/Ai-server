"""Offline direct Ollama usage checks with synthetic metadata-only records."""

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SCRIPT = Path(__file__).resolve().parents[1] / "OpenWebUi Monitor" / "OpenWebUI-Feedback-Dashboard.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("feedback_dashboard_direct", SCRIPT)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


class DirectUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = datetime(2026, 10, 23, 19, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        self.recent = int((self.now - timedelta(minutes=20)).timestamp())
        self.older = int((self.now - timedelta(hours=3)).timestamp())
        day = self.now.astimezone(timezone.utc).date().isoformat()
        self.general = self.directory / f"ollama-11434-{day}.jsonl"
        self.private = self.directory / f"ollama-11435-{day}.jsonl"
        self._write(self.general, [
            {"schema": 1, "at": self.recent, "listener": "11434",
             "model": "codestral:22b", "source_ip": "10.0.0.8", "route": "/api/chat",
             "status": 200, "prompt_tokens": 12, "completion_tokens": 8,
             "prompt": "must never appear in the dashboard"},
            {"schema": 1, "at": self.older, "listener": "11434",
             "model": "codestral:22b", "source_ip": "10.0.0.8", "route": "/api/chat",
             "status": 200, "prompt_tokens": 100, "completion_tokens": 50},
        ])
        self._write(self.private, [
            {"schema": 1, "at": self.recent + 10, "listener": "11435",
             "model": "gemma4:31b", "source_ip": "127.0.0.1", "route": "/v1/chat/completions",
             "status": 500, "prompt_tokens": None, "completion_tokens": None},
        ])

    @staticmethod
    def _write(path, records):
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def _read(self, view="hours", amount=2):
        start, end, oldest, today = dashboard.usage_window(view, amount, now=self.now)
        return dashboard.read_direct_usage(
            self.directory, start, end, view, amount, oldest, today)

    def test_counts_window_coverage_and_privacy(self):
        data = self._read()
        totals = data["totals"]
        self.assertEqual((totals["requests"], totals["successes"], totals["failures"]), (2, 1, 1))
        self.assertEqual((totals["prompt_tokens"], totals["completion_tokens"], totals["total_tokens"]),
                         (12, 8, 20))
        self.assertEqual(totals["total_coverage"], 1)
        self.assertEqual(data["period_unit"], "hour")
        self.assertEqual({row["model"] for row in data["models"]}, {"codestral:22b", "gemma4:31b"})
        self.assertEqual({row["caller"] for row in data["callers"]}, {"10.0.0.8", "127.0.0.1"})
        self.assertNotIn("must never appear", json.dumps(data))
        self.assertTrue(data["complete"])

    def test_selected_window_and_invalid_record_are_explicit(self):
        self.assertEqual(self._read("minutes", 10)["totals"]["requests"], 0)
        self.assertEqual(self._read("week", None)["totals"]["requests"], 3)
        self.assertEqual(self._read("week", None)["period_unit"], "day")
        with self.general.open("a", encoding="utf-8") as out:
            out.write('{"schema":1,"at":"bad"}\n')
        data = self._read()
        self.assertFalse(data["complete"])
        self.assertEqual(data["totals"]["requests"], 2)
        self.assertEqual(data["error_count"], 1)

    def test_queue_drop_is_reported_as_incomplete(self):
        with self.general.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"schema": 1, "kind": "dropped", "at": self.recent,
                                  "listener": "11434", "dropped": 3}) + "\n")
        data = self._read()
        self.assertEqual(data["dropped_records"], 3)
        self.assertFalse(data["complete"])
        self.assertEqual(data["totals"]["requests"], 2)

    def test_real_access_log_fallback_and_recorder_priority(self):
        log = self.directory / "ollama-service.log"
        log_time = datetime.fromtimestamp(self.recent).strftime("%Y/%m/%d - %H:%M:%S")
        log.write_text(
            f'[GIN] {log_time} | 200 | 5.3s | 192.0.2.13 | POST     "/api/chat"\n'
            f'[GIN] {log_time} | 200 | 3.2ms | 192.0.2.10 | GET      "/api/tags"\n',
            encoding="utf-8")
        start, end, oldest, today = dashboard.usage_window("hours", 2, now=self.now)
        empty = self.directory / "logs-only"
        empty.mkdir()
        from direct_usage import read_direct_usage
        observed = read_direct_usage(empty, start, end, "hours", 2, oldest, today, (log,))
        self.assertEqual(observed["totals"]["requests"], 1)
        self.assertEqual(observed["recent"][0]["source_ip"], "192.0.2.13")
        self.assertEqual(observed["recent"][0]["model"], "Not recorded")
        self.assertIsNone(observed["recent"][0]["total"])
        self.assertEqual(observed["access_log_files"], 1)
        self.assertFalse(observed["complete"])
        recorded = read_direct_usage(self.directory, start, end, "hours", 2,
                                     oldest, today, (log,))
        self.assertEqual(recorded["totals"]["requests"], 2)
        self.assertEqual(recorded["access_log_files"], 0)

    def test_page_and_api_require_key(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     dashboard.make_handler(self.directory / "unused.db",
                                                            "test-key", self.directory))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaises(HTTPError) as denied:
            urlopen(base + "/direct-usage", timeout=2)
        self.assertEqual(denied.exception.code, 403)
        with urlopen(base + "/direct-usage?key=test-key", timeout=2) as page:
            self.assertIn(b"Ollama request usage", page.read())
        request = Request(base + "/api/direct-usage?view=hours&amount=2",
                          headers={"X-Local-Token": "test-key"})
        with urlopen(request, timeout=2) as response:
            self.assertIn("totals", json.load(response))


if __name__ == "__main__":
    unittest.main()
