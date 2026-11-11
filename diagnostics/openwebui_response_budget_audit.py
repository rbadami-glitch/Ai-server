"""Read-only Open WebUI/Ollama generation-limit audit.

The report contains selected numeric settings and model identifiers only. It
never exports prompts, chat messages, API keys, connection URLs, or log text.
It does not make an inference request or change either service.
"""

import argparse
import collections
import datetime as dt
import json
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path


FIELDS = {
    "max_tokens", "max_completion_tokens", "num_predict", "num_ctx",
    "reasoning_effort", "think", "stream", "stream_response",
}


def json_value(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def selected_fields(value, prefix="", depth=0):
    """Return only allowlisted settings; suppress stop text and other secrets."""
    if depth > 9 or not isinstance(value, dict):
        return {}
    found = {}
    for key, item in value.items():
        if key == "model_controls":
            continue
        path = f"{prefix}.{key}" if prefix else str(key)
        if key in FIELDS and isinstance(item, (str, int, float, bool)):
            found[path] = item
        elif key == "stop" and isinstance(item, (str, list)):
            found[path + "_configured"] = bool(item)
        elif isinstance(item, dict):
            found.update(selected_fields(item, path, depth + 1))
    return found


def control_defaults(params):
    if not isinstance(params, dict):
        return []
    controls = params.get("model_controls") or {}
    if not isinstance(controls, dict):
        return []
    result = []
    for control_id, control in controls.items():
        if not isinstance(control, dict):
            continue
        default = control.get("default")
        options = control.get("options") or {}
        option = options.get(default) if isinstance(options, dict) else None
        result.append({"control_id": control_id, "default_option": default,
                       "default_generation_fields": selected_fields(
                           option.get("params") if isinstance(option, dict) else None)})
    return result


def table_columns(db, table):
    if table not in {"config", "model", "user", "chat"}:
        return set()
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def database_settings(path, model_id):
    if not path.is_file():
        return {"status": "database_not_found"}
    uri = path.resolve().as_uri() + "?mode=ro"
    result = {"status": "read_only", "global_fields": [],
              "models": [], "account_field_counts": [],
              "chat_override_field_counts": [], "chats_inspected": 0,
              "accounts_inspected": 0}
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        config_cols = table_columns(db, "config")
        if "data" in config_cols:
            for (raw,) in db.execute("SELECT data FROM config"):
                parsed = json_value(raw)
                if isinstance(parsed, dict):
                    result["global_fields"].append(selected_fields(parsed))
        elif {"key", "value"} <= config_cols:
            for key, raw in db.execute("SELECT key, value FROM config"):
                parsed = json_value(raw)
                if isinstance(parsed, dict):
                    result["global_fields"].append(selected_fields(parsed, str(key)))

        model_cols = table_columns(db, "model")
        if {"id", "params"} <= model_cols:
            columns = ["id", "params"]
            if "base_model_id" in model_cols:
                columns.append("base_model_id")
            if "is_active" in model_cols:
                columns.append("is_active")
            query = "SELECT " + ", ".join(columns) + " FROM model WHERE id = ?"
            for row in db.execute(query, (model_id,)):
                record = dict(zip(columns, row))
                params = json_value(record.pop("params"))
                record["generation_fields"] = selected_fields(params)
                record["model_control_defaults"] = control_defaults(params)
                result["models"].append(record)

        user_cols = table_columns(db, "user")
        if "settings" in user_cols:
            counts = collections.Counter()
            for (raw,) in db.execute("SELECT settings FROM user"):
                result["accounts_inspected"] += 1
                fields = selected_fields(json_value(raw))
                for name, value in fields.items():
                    counts[(name, json.dumps(value, sort_keys=True))] += 1
            result["account_field_counts"] = [
                {"field": name, "value": json.loads(value), "accounts": count}
                for (name, value), count in sorted(counts.items())
            ]
        chat_cols = table_columns(db, "chat")
        if "chat" in chat_cols:
            order = " ORDER BY updated_at DESC" if "updated_at" in chat_cols else ""
            counts = collections.Counter()
            query = "SELECT chat FROM chat" + order + " LIMIT 500"
            for (raw,) in db.execute(query):
                parsed = json_value(raw)
                if not isinstance(parsed, dict):
                    continue
                result["chats_inspected"] += 1
                fields = selected_fields(parsed.get("params"))
                fields.update(selected_fields(parsed.get("controls"), "controls"))
                nested = parsed.get("chat")
                if isinstance(nested, dict):
                    fields.update(selected_fields(nested.get("params"), "chat.params"))
                for name, value in fields.items():
                    counts[(name, json.dumps(value, sort_keys=True))] += 1
            result["chat_override_field_counts"] = [
                {"field": name, "value": json.loads(value), "chats": count}
                for (name, value), count in sorted(counts.items())
            ]
    return result


def fetch_json(url, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body else {}
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if body else "GET")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=5) as response:
        return json.load(response)


def ollama_settings(base_url, candidates):
    report = {"reachable": False, "matched_models": []}
    try:
        tags = fetch_json(base_url.rstrip("/") + "/api/tags")
        report["reachable"] = True
        installed = {item.get("name") for item in tags.get("models", [])}
        for name in sorted(candidates & installed):
            item = {"model": name}
            try:
                show = fetch_json(base_url.rstrip("/") + "/api/show", {"name": name})
                parameters = show.get("parameters") or ""
                item["parameters"] = {}
                for line in parameters.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and parts[0] in {"num_predict", "num_ctx"}:
                        item["parameters"][parts[0]] = parts[1].strip()
                info = show.get("model_info") or {}
                item["context_lengths"] = {
                    key: value for key, value in info.items()
                    if key.endswith(".context_length") and isinstance(value, int)
                }
            except (OSError, ValueError, KeyError):
                item["show_status"] = "unavailable"
            report["matched_models"].append(item)
    except (OSError, ValueError, KeyError):
        report["status"] = "unavailable"
    return report


def recorder_events(directory, candidates):
    """Read bounded metadata tails; never copy source IPs or request text."""
    if not directory or not directory.is_dir():
        return {"status": "not_available", "recent": []}
    events = []
    files = sorted(directory.glob("ollama-*.jsonl"))[-4:]
    for path in files:
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 8 * 1024 * 1024))
                tail = stream.read().decode("utf-8", "replace")
            if size > 8 * 1024 * 1024:
                tail = tail.split("\n", 1)[-1]
            for line in tail.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("model") not in candidates:
                    continue
                events.append({
                    field: row.get(field) for field in (
                        "at", "listener", "route", "model", "status",
                        "duration_ms", "prompt_tokens", "completion_tokens")
                })
        except OSError:
            continue
    events.sort(key=lambda row: row.get("at") or 0)
    return {"status": "bounded_last_four_files", "recent": events[-30:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Open WebUI SQLite database path")
    parser.add_argument("--model", required=True,
                        help="Exact Open WebUI model ID to inspect")
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSON report outside the Git checkout")
    parser.add_argument("--webui-url", default="http://127.0.0.1")
    parser.add_argument("--ollama-url", action="append", default=[],
                        help="Optional local Ollama base URL; repeat for each instance")
    parser.add_argument("--recorder-dir", type=Path,
                        help="Optional Ollama metadata recorder directory")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output file already exists; choose a new path")
    if not args.output.parent.is_dir():
        parser.error("Output parent directory must already exist")
    repo_root = Path(__file__).resolve().parent.parent
    if args.output.resolve().is_relative_to(repo_root):
        parser.error("Keep the report outside the Git checkout")

    try:
        database = database_settings(args.db, args.model)
    except (OSError, sqlite3.Error, ValueError) as error:
        database = {"status": "read_failed", "error_type": type(error).__name__}

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": args.model,
        "database": database,
        "webui": {},
        "ollama": [],
        "recorder": {},
        "limits": [
            "Saved chat overrides are sampled; caller-supplied request parameters are not visible.",
            "A saved limit does not prove it was applied to a particular request.",
            "No inference request was made; finish reasons and stalls remain unmeasured.",
        ],
    }
    try:
        version = fetch_json(args.webui_url.rstrip("/") + "/api/version")
        report["webui"]["version"] = version.get("version")
    except (OSError, ValueError, KeyError):
        report["webui"]["status"] = "version_unavailable"

    candidates = {args.model}
    for item in report["database"].get("models", []):
        if item.get("base_model_id"):
            candidates.add(item["base_model_id"])
    for url in args.ollama_url:
        report["ollama"].append(ollama_settings(url, candidates))
    report["recorder"] = recorder_events(args.recorder_dir, candidates)

    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
    print(f"Wrote settings-only report: {args.output}")
    print("No inference request or service change was made.")


if __name__ == "__main__":
    main()
