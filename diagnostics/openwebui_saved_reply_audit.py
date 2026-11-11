"""Summarize saved Open WebUI reply outcomes without exporting chat content.

Reads at most 5,000 recent chat records from SQLite in read-only mode. Only
aggregate counts and numeric token usage leave the process; no user/chat IDs,
prompts, answers, names, email addresses, or exact message times are written.
Saved message metadata may be incomplete and cannot prove what reached a browser.
"""

import argparse
import collections
import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def parse_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) else None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def columns(db, table):
    if table not in {"chat", "user"}:
        return set()
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def account_limit(settings):
    data = parse_json(settings) or {}
    ui = data.get("ui") or {}
    params = ui.get("params") if isinstance(ui, dict) else {}
    value = params.get("max_tokens") if isinstance(params, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def messages_in(chat):
    history = chat.get("history") or {}
    messages = history.get("messages") if isinstance(history, dict) else None
    if isinstance(messages, dict):
        return [item for item in messages.values() if isinstance(item, dict)]
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    messages = chat.get("messages") or []
    return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []


def finish_reason(message):
    value = message.get("finish_reason")
    info = message.get("info") or {}
    if value is None and isinstance(info, dict):
        value = info.get("finish_reason")
        choices = info.get("choices") or []
        if value is None and isinstance(choices, list) and choices and isinstance(choices[0], dict):
            value = choices[0].get("finish_reason")
    if isinstance(value, str) and value in {"stop", "length", "content_filter", "tool_calls", "function_call"}:
        return value
    return "missing" if value is None else "other"


def usage_tokens(message):
    usage = message.get("usage")
    if not isinstance(usage, dict):
        info = message.get("info") or {}
        usage = info.get("usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def utc_day(value):
    try:
        number = float(value)
        if number > 1e12:
            number /= 1000
        return dt.datetime.fromtimestamp(number, dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def summarize(db_path, model, days=14, limit=5000):
    uri = db_path.resolve().as_uri() + "?mode=ro"
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()
    result = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": model,
        "days": days,
        "maximum_chats": limit,
        "chats_read": 0,
        "chats_matching_model": 0,
        "assistant_messages": 0,
        "finish_reasons": {},
        "by_account_setting": {},
        "by_chat_setting": {},
        "by_day": {},
        "metadata_coverage": {},
        "limits": [
            "Saved replies may omit finish reasons or token usage.",
            "A saved account or chat setting may differ from the value sent for a past request.",
            "No browser/network completion evidence is present in the chat database.",
        ],
    }
    reasons = collections.Counter()
    by_account = collections.defaultdict(collections.Counter)
    by_chat = collections.defaultdict(collections.Counter)
    by_day = collections.defaultdict(collections.Counter)
    coverage = collections.Counter()
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        chat_cols = columns(db, "chat")
        user_cols = columns(db, "user")
        if "chat" not in chat_cols:
            result["status"] = "chat_table_unavailable"
            return result
        owners = {}
        if {"id", "settings"} <= user_cols:
            for uid, settings in db.execute("SELECT id, settings FROM user"):
                owners[uid] = account_limit(settings)
        selected = ["chat"]
        if "user_id" in chat_cols:
            selected.append("user_id")
        if "updated_at" in chat_cols:
            selected.append("updated_at")
        order = "updated_at" if "updated_at" in chat_cols else "rowid"
        query = f"SELECT {', '.join(selected)} FROM chat ORDER BY {order} DESC LIMIT ?"
        for row in db.execute(query, (limit,)):
            record = dict(zip(selected, row))
            chat = parse_json(record.get("chat"))
            if not chat:
                continue
            result["chats_read"] += 1
            day = utc_day(record.get("updated_at"))
            if day and day < cutoff:
                continue
            messages = messages_in(chat)
            models = chat.get("models") or []
            selected_model = chat.get("model")
            chat_matches = (selected_model == model or
                            isinstance(models, list) and model in models or
                            any(item.get("model") == model for item in messages))
            if not chat_matches:
                continue
            result["chats_matching_model"] += 1
            params = chat.get("params") or {}
            chat_limit = params.get("max_tokens") if isinstance(params, dict) else None
            account = owners.get(record.get("user_id"))
            account_group = "128" if account == 128 else "other_explicit" if account is not None else "unset_or_unknown"
            chat_group = "128" if chat_limit == 128 else "other_explicit" if chat_limit is not None else "unset"
            for message in messages:
                if message.get("role") != "assistant":
                    continue
                message_model = message.get("model")
                if message_model and message_model != model and models != [model]:
                    continue
                reason = finish_reason(message)
                result["assistant_messages"] += 1
                reasons[reason] += 1
                by_account[account_group][reason] += 1
                by_chat[chat_group][reason] += 1
                if day:
                    by_day[day][reason] += 1
                if message.get("done") is False:
                    coverage["explicit_done_false"] += 1
                if message.get("error"):
                    coverage["error_present"] += 1
                if usage_tokens(message) is not None:
                    coverage["completion_usage_present"] += 1
                if reason != "missing":
                    coverage["finish_reason_present"] += 1
    result["status"] = "read_only"
    result["finish_reasons"] = dict(sorted(reasons.items()))
    result["by_account_setting"] = {key: dict(sorted(value.items())) for key, value in sorted(by_account.items())}
    result["by_chat_setting"] = {key: dict(sorted(value.items())) for key, value in sorted(by_chat.items())}
    result["by_day"] = {key: dict(sorted(value.items())) for key, value in sorted(by_day.items())}
    result["metadata_coverage"] = dict(sorted(coverage.items()))
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
        parser.error("Keep diagnostic output outside the Git checkout")
    report = summarize(args.db, args.model, args.days, args.limit)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("Wrote aggregate saved-reply metadata only.")


if __name__ == "__main__":
    main()
