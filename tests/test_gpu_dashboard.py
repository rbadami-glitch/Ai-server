"""Offline checks for the GPU history API; no server services are contacted."""

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta
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


if __name__ == "__main__":
    unittest.main()
