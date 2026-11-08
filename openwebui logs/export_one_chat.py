"""Export one Open WebUI conversation from SQLite without changing the database.

The output contains employee chat text. Keep it outside source-control checkouts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


MESSAGE_FIELDS = (
    "id", "role", "parent_id", "content", "output", "model_id", "files",
    "sources", "embeds", "meta", "done", "status_history", "error",
    "usage", "context_summary", "created_at", "updated_at",
)
JSON_FIELDS = {"content", "output", "files", "sources", "embeds", "meta", "status_history", "error", "usage"}


def chat_reference(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] in {"c", "s"}:
            return parts[-1]
    return value.strip()


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def export_chat(db_path: Path, reference: str) -> dict:
    if not db_path.is_file():
        raise ValueError("Database file does not exist")
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        chat_columns = columns(db, "chat")
        if not {"id", "title", "chat"} <= chat_columns:
            raise ValueError("Unsupported chat table")
        ref = chat_reference(reference)
        where = "id = ? OR share_id = ?" if "share_id" in chat_columns else "id = ?"
        params = (ref, ref) if "share_id" in chat_columns else (ref,)
        available = [name for name in ("id", "title", "chat", "created_at", "updated_at", "current_message_id") if name in chat_columns]
        row = db.execute(f"SELECT {', '.join(available)} FROM chat WHERE {where}", params).fetchone()
        if row is None:
            raise ValueError("Chat not found; use its /c/ or /s/ link or chat ID")
        saved = decoded(row["chat"])
        if not isinstance(saved, dict):
            raise ValueError("Chat JSON has an unexpected format")

        message_columns = columns(db, "chat_message")
        selected = [name for name in MESSAGE_FIELDS if name in message_columns]
        messages = []
        if {"chat_id", "id", "role"} <= message_columns:
            query = f"SELECT {', '.join(selected)} FROM chat_message WHERE chat_id = ? ORDER BY created_at, id"
            for message in db.execute(query, (row["id"],)):
                messages.append({key: decoded(message[key]) if key in JSON_FIELDS else message[key] for key in selected})

        # Older chats can have only the JSON snapshot, so retain its message map.
        history = saved.get("history") if isinstance(saved.get("history"), dict) else {}
        snapshot = history.get("messages") or saved.get("messages") or {}
        if not isinstance(snapshot, (dict, list)):
            snapshot = {}
        return {
            "format": "openwebui-one-chat-v1",
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"] if "created_at" in available else None,
            "updated_at": row["updated_at"] if "updated_at" in available else None,
            "current_message_id": (row["current_message_id"] if "current_message_id" in available else None) or history.get("currentId"),
            "chat_message_rows": messages,
            "snapshot_messages": snapshot,
            "message_count": len(messages) if messages else len(snapshot),
            "note": "Attachment metadata is included when saved, but attachment file contents are not exported.",
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--chat", required=True, help="Exact chat ID, /c/ link, or /s/ share link")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = export_chat(args.db, args.chat)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise SystemExit("Output file already exists; choose a new filename")
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Exported {report['message_count']} saved messages to {args.output}")


if __name__ == "__main__":
    main()
