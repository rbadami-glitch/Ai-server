"""Read-only, content-free details for past Open WebUI assistant replies.

The output has timestamps, one-way correlation hashes, numeric usage, and
boolean completion/error flags. It never writes messages, error text, users,
chat IDs, request headers, or IP addresses.
"""

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from openwebui_saved_reply_audit import finish_reason, messages_in, parse_json, usage_tokens


def iso_time(value):
    try:
        number = float(value)
        if number > 1e12:
            number /= 1000
        return dt.datetime.fromtimestamp(number, dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def summarize(db_path, model, days, limit):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    result = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "model": model, "days": days, "chats_read": 0,
              "matching_chats": 0, "replies": [], "limits": [
                  "Saved reply timestamps are not the time the provider finished.",
                  "Missing finish reasons cannot be reconstructed from the chat database.",
                  "Timestamp matches to service logs may be ambiguous under concurrent traffic.",
              ]}
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        fields = {row[1] for row in db.execute("PRAGMA table_info(chat)")}
        if not {"chat", "updated_at"} <= fields:
            result["status"] = "required_columns_missing"
            return result
        for raw, updated in db.execute("SELECT chat, updated_at FROM chat ORDER BY updated_at DESC LIMIT ?", (limit,)):
            chat = parse_json(raw)
            if not chat:
                continue
            result["chats_read"] += 1
            updated_iso = iso_time(updated)
            if updated_iso and dt.datetime.fromisoformat(updated_iso) < cutoff:
                continue
            messages = messages_in(chat)
            models = chat.get("models") or []
            if not (chat.get("model") == model or model in models or any(m.get("model") == model for m in messages)):
                continue
            result["matching_chats"] += 1
            chat_id = chat.get("id")
            for message in messages:
                if message.get("role") != "assistant":
                    continue
                if message.get("model") and message.get("model") != model and models != [model]:
                    continue
                stamp = iso_time(message.get("timestamp") or message.get("created_at"))
                if not stamp:
                    stamp = updated_iso
                if not stamp or dt.datetime.fromisoformat(stamp) < cutoff:
                    continue
                message_id = message.get("id")
                trace = None
                if isinstance(chat_id, str) and isinstance(message_id, str):
                    trace = hashlib.sha256((chat_id + ":" + message_id).encode()).hexdigest()[:20]
                done = message.get("done")
                error = bool(message.get("error"))
                reason = finish_reason(message)
                result["replies"].append({
                    "time_utc": stamp,
                    "time_source": "message" if message.get("timestamp") or message.get("created_at") else "chat_updated_fallback",
                    "trace": trace,
                    "finish_reason": reason,
                    "done": done if isinstance(done, bool) else None,
                    "error_present": error,
                    "completion_tokens": usage_tokens(message),
                })
    result["replies"].sort(key=lambda row: row["time_utc"])
    result["status"] = "read_only"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    if args.days < 1 or args.days > 60 or args.limit < 1 or args.limit > 10000:
        parser.error("days must be 1-60 and limit must be 1-10000")
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Output must be a new file in an existing directory")
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parent.parent):
        parser.error("Keep output outside the public Git checkout")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(summarize(args.db, args.model, args.days, args.limit), stream, separators=(",", ":"))
    print("Wrote content-free historical reply metadata.")


if __name__ == "__main__":
    main()
