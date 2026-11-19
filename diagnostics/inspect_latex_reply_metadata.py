"""Inspect recent Open WebUI LaTeX canary output shape without printing content.

Reads the chat database in read-only mode. Does not print or write document text,
chat text, file paths, file IDs, user IDs, or download URLs.
"""

import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path


TOOL_NAME = "convert_attached_document_to_latex"


def load_guard():
    path = Path(__file__).resolve().parents[1] / "openwebui-tools" / "verified_latex_reply_filter.py"
    spec = importlib.util.spec_from_file_location("verified_latex_reply_filter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_output(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, list) else None


def summarize_output(output, guard):
    if output is None:
        return {"output_list": False}
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]
    results = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call_output"]
    names = [str(item.get("name", "")) for item in calls]
    return {
        "output_list": True,
        "item_types": [str(item.get("type", "")) for item in output if isinstance(item, dict)],
        "call_names": names,
        "conversion_name_exact": any(name == TOOL_NAME for name in names),
        "conversion_name_suffix": any(name.endswith(TOOL_NAME) for name in names),
        "call_count": len(calls),
        "result_count": len(results),
        "call_result_ids_link": any(
            call.get("call_id") and call.get("call_id") == result.get("call_id")
            for call in calls for result in results
        ),
        "result_shapes": [type(item.get("output")).__name__ for item in results],
        "guard_matches": guard._verified_tool_result(output) is not None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("limit must be 1-10")
    guard = load_guard()
    uri = args.db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in db.execute("PRAGMA table_info(chat_message)")}
        if not {"role", "model_id", "created_at", "output"} <= columns:
            print(json.dumps({"status": "chat_message_columns_missing"}))
            return
        rows = db.execute(
            "SELECT output FROM chat_message WHERE role='assistant' AND model_id=? "
            "ORDER BY created_at DESC LIMIT ?", (args.model, args.limit)
        ).fetchall()
    print(json.dumps({"status": "read_only", "matching_replies": len(rows),
                      "replies": [summarize_output(parse_output(row[0]), guard) for row in rows]},
                     separators=(",", ":")))


if __name__ == "__main__":
    main()
