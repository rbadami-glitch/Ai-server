"""Run synthetic questions through Open WebUI; never grade answers in the VDI.

Requires an explicit --run flag before making any model request. Uses an API
token entered invisibly at the terminal, prints answers, and writes private
JSON/HTML locally.
"""

import argparse
import datetime as dt
import getpass
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an API key to a redirected address."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_base_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use a full http:// or https:// Open WebUI URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Do not put credentials or query text in the URL")
    if parsed.path not in ("", "/"):
        raise ValueError("Use the Open WebUI base URL, without an API path")
    if parsed.scheme == "http" and parsed.hostname.lower() not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Use HTTPS before sending an API key; HTTP is allowed only for localhost")
    return value.rstrip("/")


def load_suite(path):
    with path.open("r", encoding="utf-8") as stream:
        suite = json.load(stream)
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The suite needs a non-empty cases list")
    seen = set()
    for case in cases:
        identifier = case.get("id")
        messages = case.get("messages")
        if not isinstance(identifier, str) or identifier in seen:
            raise ValueError("Every case needs a unique text ID")
        seen.add(identifier)
        if (not isinstance(messages, list) or not messages
                or messages[-1].get("role") != "user"):
            raise ValueError(f"{identifier} must end with a user message")
        for message in messages:
            if (message.get("role") not in ("user", "assistant")
                    or not isinstance(message.get("content"), str)):
                raise ValueError(f"{identifier} has an invalid message")
        if not isinstance(case.get("review"), list) or not case["review"]:
            raise ValueError(f"{identifier} needs a review checklist")
    return suite


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(part.get("text", "")) for part in value
                         if isinstance(part, dict))
    return ""


def read_windows_clipboard():
    """Read Unicode text from this Windows session without a shell command."""
    if sys.platform != "win32":
        raise ValueError("Clipboard key input requires Windows")
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = ()
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.1)
    else:
        raise ValueError("Windows clipboard is busy; copy the key and try again")
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            raise ValueError("Clipboard has no text; copy the API key in Open WebUI")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ValueError("Could not read clipboard text")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def read_api_key(from_clipboard=False):
    if from_clipboard:
        key = read_windows_clipboard().strip()
        if not key or len(key) > 4096 or any(char.isspace() for char in key):
            raise ValueError("Clipboard does not contain one API key; copy it and try again")
        print("Open WebUI API key: ******** (loaded from Windows clipboard; not shown)",
              flush=True)
        return key
    if sys.platform != "win32":
        return getpass.getpass("Open WebUI API key (hidden): ").strip()
    import msvcrt

    print("Open WebUI API key (* appears for each character; do not screenshot): ",
          end="", flush=True)
    characters = []
    while True:
        character = msvcrt.getwch()
        if character in ("\r", "\n"):
            print(flush=True)
            return "".join(characters).strip()
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\b":
            if characters:
                characters.pop()
                print("\b \b", end="", flush=True)
            continue
        if character in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if character.isprintable():
            characters.append(character)
            print("*", end="", flush=True)


def http_error_message(error, key):
    message = f"Open WebUI returned HTTP {error.code}"
    try:
        payload = json.loads(error.read(4096).decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return message
    if not isinstance(payload, dict):
        return message
    detail = next((payload[field] for field in ("detail", "error", "message")
                   if payload.get(field)), None)
    if detail is None:
        return message
    detail = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    detail = " ".join(detail.replace(key, "[REDACTED KEY]").split())[:300]
    return f"{message}: {detail}" if detail else message


def request_answer(base_url, model, messages, key, timeout=180):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/api/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    local = urllib.parse.urlsplit(base_url).hostname in ("localhost", "127.0.0.1", "::1")
    handlers = (urllib.request.ProxyHandler({}), NoRedirect) if local else (NoRedirect,)
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                raise ValueError("Open WebUI returned a non-JSON response")
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(http_error_message(error, key)) from error
    except urllib.error.URLError as error:
        raise ValueError("Could not reach Open WebUI") from error
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("Response has no answer choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("Response has no answer message")
    answer = content_text(message.get("content"))
    if not answer.strip():
        raise ValueError("Response has no answer text")
    return answer, choices[0].get("finish_reason")


def run_cases(suite, selected, base_url, model, key, ask=request_answer):
    results = []
    for case in selected:
        print(f"ASKING {case['id']} ({case.get('area', 'reasoning')})", flush=True)
        try:
            answer, finish = ask(base_url, model, case["messages"], key)
            results.append({"id": case["id"], "area": case.get("area", ""),
                            "messages": case["messages"], "answer": answer,
                            "finish_reason": finish, "status": "answered"})
            print(f"\n===== {case['id']} ANSWER =====\n{answer}\n", flush=True)
        except (ValueError, OSError) as error:
            results.append({"id": case["id"], "area": case.get("area", ""),
                            "messages": case["messages"], "answer": "",
                            "status": "technical_error", "error": str(error)})
            print(f"STOPPED: technical error on {case['id']}: {error}", file=sys.stderr)
            break
    return {
        "format_version": 1,
        "suite_version": suite.get("suite_version", "not recorded"),
        "run_time_ist": dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(),
        "model": model,
        "requested_cases": len(selected),
        "answered_cases": sum(row["status"] == "answered" for row in results),
        "graded_cases": 0,
        "grading": "None in VDI; screenshots can be graded in a separate chat",
        "results": results,
    }


def render_html(run):
    escape = html.escape
    cards = []
    for row in run["results"]:
        question = "\n\n".join(message["role"].upper() + ": " + message["content"]
                                for message in row["messages"])
        answer = row["answer"] if row["status"] == "answered" else row.get("error", "")
        finish_note = (f'<p>Finish reason: {escape(str(row.get("finish_reason")))}</p>'
                       if row.get("finish_reason") == "length" else "")
        cards.append(
            f'<section><h2>{escape(row["id"])} · {escape(row["area"])}</h2>'
            f'<h3>Prompt and earlier context</h3><pre>{escape(question)}</pre>'
            f'<h3>{"Answer" if row["status"] == "answered" else "Technical error"}</h3>'
            f'<pre>{escape(answer)}</pre>{finish_note}</section>'
        )
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Private live reasoning eval</title>"
            "<style>body{font:16px Arial,sans-serif;max-width:1000px;margin:24px auto;color:#1c2633;}"
            "section{border:1px solid #bbb;border-radius:8px;margin:16px 0;padding:16px;}"
            "h2{margin:0 0 12px;}h3{font-size:14px;margin:12px 0 5px;}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f6;padding:10px;margin:0;}"
            "</style></head><body>"
            f'<h1>Private live reasoning eval</h1><p>Model: {escape(run["model"])} · '
            f'Suite: {escape(run["suite_version"])} · Answered: {run["answered_cases"]}/'
            f'{run["requested_cases"]}. Not graded in the VDI.</p>'
            + "".join(cards) + "</body></html>")


def save_run(run, output_dir):
    """Write only answers and metadata; never write the API key."""
    tag = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    json_path = output_dir / f"OpenWebUI-Live-Reasoning-{tag}.json"
    html_path = output_dir / f"OpenWebUI-Live-Reasoning-{tag}.html"
    with json_path.open("x", encoding="utf-8") as stream:
        json.dump(run, stream, ensure_ascii=False, indent=2)
    with html_path.open("x", encoding="utf-8") as stream:
        stream.write(render_html(run))
    print(f"ANSWERED={run['answered_cases']}/{run['requested_cases']} | GRADED=0")
    print(f"HTML={html_path}")
    print(f"JSON={json_path}")
    return json_path, html_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True,
                        help="One synthetic test suite JSON")
    parser.add_argument("--run", action="store_true", help="Actually send questions to the model")
    parser.add_argument("--key-from-clipboard", action="store_true",
                        help="Read the API key directly from the Windows clipboard")
    parser.add_argument("--url", help="Open WebUI base URL, for example http://10.0.0.2:3000")
    parser.add_argument("--model", help="Exact model ID as shown by Open WebUI")
    parser.add_argument("--output-dir", type=Path, help="Private VDI folder for answer files")
    parser.add_argument("--limit", type=int, help="Only ask the first N cases for a small test")
    args = parser.parse_args(argv)
    suite_paths = (args.suite,)
    suites = [load_suite(path.resolve(strict=True)) for path in suite_paths]
    cases = [case for suite in suites for case in suite["cases"]]
    if len({case["id"] for case in cases}) != len(cases):
        parser.error("The selected suites have duplicate case IDs")
    suite = {"suite_version": " + ".join(str(item.get("suite_version", "not recorded"))
                                          for item in suites), "cases": cases}
    selected = suite["cases"]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        selected = selected[:args.limit]
    if not args.run:
        print(f"DRY RUN: {len(selected)} synthetic cases; no model calls made")
        for case in selected:
            print(f"{case['id']}: {case.get('area', 'reasoning')}")
        return
    url = args.url
    model = args.model
    output_arg = args.output_dir
    if not url or not model or not output_arg:
        parser.error("--run needs --url, --model, and --output-dir")
    base_url = validate_base_url(url)
    output_dir = output_arg.resolve(strict=True)
    if not output_dir.is_dir():
        parser.error("Output directory must already exist")
    if any(not path.resolve().is_relative_to(output_dir.parent) for path in suite_paths):
        parser.error("Keep the suite inside the Open WebUI monitoring folder")
    key = read_api_key(from_clipboard=args.key_from_clipboard)
    if not key:
        parser.error("An API key is required")
    run = run_cases(suite, selected, base_url, model, key)
    save_run(run, output_dir)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
