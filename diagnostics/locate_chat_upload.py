"""Locate original files attached to one Open WebUI chat without reading file contents.

Reads SQLite in query-only mode. Prints file IDs, names, paths, sizes, and
signatures; never prints message text or document contents. Keep output private.
"""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def file_refs(value):
    if isinstance(value, dict):
        files = value.get("files")
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                inner = item.get("file") if isinstance(item.get("file"), dict) else item
                if isinstance(inner.get("id"), str):
                    yield inner["id"], inner.get("name") or item.get("name")
        history = value.get("history")
        if isinstance(history, dict):
            yield from file_refs(history)
        messages = value.get("messages")
        if isinstance(messages, dict):
            for item in messages.values():
                yield from file_refs(item)
        elif isinstance(messages, list):
            for item in messages:
                yield from file_refs(item)


def locate(db_path, chat_id):
    if not db_path.is_file():
        raise ValueError("Open WebUI database not found")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        if not {"id", "chat"} <= columns(db, "chat"):
            raise ValueError("Unsupported chat table")
        row = db.execute("SELECT chat FROM chat WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            raise ValueError("Chat ID not found")
        refs = list(file_refs(decode(row[0])))
        message_cols = columns(db, "chat_message")
        if {"chat_id", "files"} <= message_cols:
            for (raw,) in db.execute("SELECT files FROM chat_message WHERE chat_id=?", (chat_id,)):
                refs.extend(file_refs({"files": decode(raw)}))
        names = dict(refs)
        found = []
        file_cols = columns(db, "file")
        if not {"id", "filename"} <= file_cols:
            raise ValueError("Unsupported file table")
        selected = [field for field in ("id", "filename", "path", "meta") if field in file_cols]
        for file_id in sorted(names):
            row = db.execute(
                f"SELECT {', '.join(selected)} FROM file WHERE id=?", (file_id,)
            ).fetchone()
            if row is None:
                found.append({"id": file_id, "name": names[file_id], "record_found": False})
                continue
            record = dict(zip(selected, row))
            meta = decode(record.get("meta")) or {}
            path = record.get("path")
            entry = {
                "id": file_id,
                "name": record.get("filename") or names[file_id],
                "record_found": True,
                "content_type": meta.get("content_type") if isinstance(meta, dict) else None,
                "path": path,
            }
            if path and Path(path).is_file():
                with open(path, "rb") as source:
                    signature = source.read(8)
                entry["size_bytes"] = Path(path).stat().st_size
                entry["signature_hex"] = signature.hex()
            else:
                entry["file_on_disk"] = False
            found.append(entry)
        return {"attachment_count": len(found), "attachments": found}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--chat-id", required=True)
    args = parser.parse_args()
    print(json.dumps(locate(args.db, args.chat_id), indent=2))


if __name__ == "__main__":
    main()
