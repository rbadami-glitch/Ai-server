"""Run one synthetic Open WebUI streaming request and save metadata only.

The API key is prompted from the terminal. Neither the prompt nor response text
is written to the report. This measures the direct API path, which may differ
from a browser chat carrying account or chat controls.
"""

import argparse
import datetime as dt
import getpass
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path


PROMPT = "Write the integers 1 through 120 in order, separated by commas. End with DONE."
FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}


def safe_reason(value):
    if value is None:
        return None
    return value if isinstance(value, str) and value in FINISH_REASONS else "other"


def safe_usage(value):
    if not isinstance(value, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens",
            "input_tokens", "output_tokens")
    return {key: value[key] for key in keys
            if key in value and isinstance(value[key], int) and value[key] >= 0}


def probe(url, model, key, read_timeout=90, deadline_seconds=300):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
    }
    report = {
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "path": "/api/chat/completions",
        "model": model,
        "request_stream": True,
        "request_max_tokens_present": False,
        "request_chars": len(PROMPT),
        "http_status": None,
        "content_type": None,
        "events": 0,
        "content_chars": 0,
        "first_event_ms": None,
        "first_content_ms": None,
        "largest_event_gap_ms": None,
        "finish_reason": None,
        "done_marker": False,
        "usage": None,
        "error_kind": None,
        "total_ms": None,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/api/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.monotonic()
    previous_event = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=read_timeout) as response:
            report["http_status"] = response.status
            report["content_type"] = response.headers.get_content_type()
            if report["content_type"] != "text/event-stream":
                # Some custom models return a JSON completion despite stream=True.
                body = json.load(response)
                for choice in body.get("choices", []):
                    report["finish_reason"] = safe_reason(choice.get("finish_reason"))
                    content = (choice.get("message") or {}).get("content")
                    if isinstance(content, str):
                        report["content_chars"] += len(content)
                report["usage"] = safe_usage(body.get("usage"))
            else:
                while True:
                    if time.monotonic() - started > deadline_seconds:
                        report["error_kind"] = "client_deadline_reached"
                        break
                    line = response.readline()
                    if not line:
                        break
                    if not line.startswith(b"data:"):
                        continue
                    now = time.monotonic()
                    report["events"] += 1
                    elapsed = round((now - started) * 1000)
                    if report["first_event_ms"] is None:
                        report["first_event_ms"] = elapsed
                    if previous_event is not None:
                        gap = round((now - previous_event) * 1000)
                        report["largest_event_gap_ms"] = max(
                            report["largest_event_gap_ms"] or 0, gap)
                    previous_event = now
                    raw = line[5:].strip()
                    if raw == b"[DONE]":
                        report["done_marker"] = True
                        break
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("done") is True:
                        report["done_marker"] = True
                    usage = safe_usage(event.get("usage"))
                    if usage:
                        report["usage"] = usage
                    for choice in event.get("choices") or []:
                        if not isinstance(choice, dict):
                            continue
                        reason = safe_reason(choice.get("finish_reason"))
                        if reason:
                            report["finish_reason"] = reason
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            report["content_chars"] += len(content)
                            if report["first_content_ms"] is None:
                                report["first_content_ms"] = elapsed
    except urllib.error.HTTPError as error:
        report["http_status"] = error.code
        report["error_kind"] = "http_error"
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        report["error_kind"] = type(error).__name__
    except (ValueError, TypeError):
        report["error_kind"] = "invalid_response"
    finally:
        report["total_ms"] = round((time.monotonic() - started) * 1000)
        report["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Output must be a new file in an existing directory")
    repo_root = Path(__file__).resolve().parent.parent
    if args.output.resolve().is_relative_to(repo_root):
        parser.error("Keep diagnostic output outside the Git checkout")
    key = getpass.getpass("Open WebUI API key (hidden; never saved): ").strip()
    if not key:
        parser.error("No API key entered")
    report = probe(args.url, args.model, key)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("Metadata-only live probe complete; report saved for encryption.")


if __name__ == "__main__":
    main()
