"""The targeted export must never include another user's chat."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "openwebui logs" / "export_one_chat.py"
spec = importlib.util.spec_from_file_location("export_one_chat", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChatExportTests(unittest.TestCase):
    def test_share_link_exports_only_selected_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "webui.db"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("CREATE TABLE chat (id TEXT, share_id TEXT, title TEXT, chat TEXT, created_at INTEGER, updated_at INTEGER, current_message_id TEXT)")
                db.execute("CREATE TABLE chat_message (id TEXT, chat_id TEXT, role TEXT, parent_id TEXT, content TEXT, model_id TEXT, created_at INTEGER, updated_at INTEGER)")
                db.execute("INSERT INTO chat VALUES (?,?,?,?,?,?,?)", ("wanted", "share-123", "Invoice status", json.dumps({"history": {"messages": {"a": {"role": "user", "content": "Invoice question"}}}}), 1, 2, "a"))
                db.execute("INSERT INTO chat VALUES (?,?,?,?,?,?,?)", ("other", "share-456", "Private other chat", json.dumps({"history": {"messages": {"b": {"content": "OTHER_SECRET"}}}}), 1, 2, "b"))
                db.execute("INSERT INTO chat_message VALUES (?,?,?,?,?,?,?,?)", ("a", "wanted", "user", None, json.dumps("Invoice question"), None, 1, 1))
                db.execute("INSERT INTO chat_message VALUES (?,?,?,?,?,?,?,?)", ("b", "other", "user", None, json.dumps("OTHER_SECRET"), None, 1, 1))
                db.commit()
            result = module.export_chat(db_path, "http://10.104.74.10/s/share-123")
            self.assertEqual(result["id"], "wanted")
            self.assertEqual(result["message_count"], 1)
            self.assertEqual(result["chat_message_rows"][0]["content"], "Invoice question")
            self.assertNotIn("OTHER_SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
