"""Offline checks for the persistent feedback dashboard launcher."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MONITOR_DIR = Path(__file__).resolve().parents[1] / "OpenWebUi Monitor"
SCRIPT = MONITOR_DIR / "Run-CW-Feedback-Dashboard.py"
SPEC = importlib.util.spec_from_file_location("cw_feedback_runner", SCRIPT)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class StartupTests(unittest.TestCase):
    def test_access_link_token_survives_launcher_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            token_path = Path(temporary) / "access-token.txt"
            token = "a" * 64
            token_path.write_text(token, encoding="utf-8")
            self.assertEqual(RUNNER.read_token(token_path), token)
            self.assertEqual(RUNNER.read_token(token_path), token)
            token_path.write_text("not-a-token", encoding="utf-8")
            with self.assertRaises(ValueError):
                RUNNER.read_token(token_path)

    def test_launcher_loads_existing_feedback_dashboard(self):
        sys.path.insert(0, str(MONITOR_DIR))
        try:
            dashboard = RUNNER.load_dashboard()
        finally:
            sys.path.remove(str(MONITOR_DIR))
        self.assertEqual(dashboard.HOST, "127.0.0.1")
        self.assertEqual(dashboard.DEFAULT_PORT, 8789)
        self.assertTrue(callable(dashboard.make_handler))


if __name__ == "__main__":
    unittest.main()
