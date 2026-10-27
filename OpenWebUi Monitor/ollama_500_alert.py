"""Send one Resend email at the start of each observed Ollama HTTP 500 incident.

Reads only recorder metadata. Config, state, logs, and Resend secrets live outside Git.
The recorder logs completed requests, so alerts follow completion rather than start.
"""

import argparse
import ctypes
import json
import logging
import os
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from direct_usage import PORTS, _caller_label, _event


DEFAULT_RECORDER_DIR = Path(r"E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage")
DEFAULT_CONFIG = Path(r"E:\AI\ollama\OpenWebUI-Monitoring\Ollama500Alert\config.json")
MAX_LINE_BYTES = 8192
POLL_SECONDS = 2


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("resend_api_key_file", "sender", "recipients")
    if not isinstance(config, dict) or any(not config.get(name) for name in required):
        raise ValueError("Mail config is missing required settings.")
    if not isinstance(config["resend_api_key_file"], str) or not Path(
        config["resend_api_key_file"]
    ).is_file():
        raise ValueError("resend_api_key_file does not exist.")
    if not Path(config["resend_api_key_file"]).read_text(encoding="utf-8").strip():
        raise ValueError("resend_api_key_file is empty.")
    if not isinstance(config["recipients"], list) or not config["recipients"] or not all(
        isinstance(address, str) and "@" in address for address in config["recipients"]
    ):
        raise ValueError("recipients must be a nonempty list of email addresses.")
    if not isinstance(config["sender"], str) or "@" not in config["sender"]:
        raise ValueError("sender must be an email address.")
    mode = config.get("rearm_mode", "quiet")
    if mode not in ("quiet", "success", "manual"):
        raise ValueError("rearm_mode must be quiet, success, or manual.")
    minutes = config.get("quiet_minutes", 30)
    if type(minutes) is not int or not 1 <= minutes <= 10080:
        raise ValueError("quiet_minutes must be between 1 and 10080.")
    config["rearm_mode"] = mode
    config["quiet_minutes"] = minutes
    return config


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def file_paths(directory, now):
    today = datetime.fromtimestamp(now, timezone.utc).date()
    previous = today - timedelta(days=1)
    for day in (previous, today):
        for port in PORTS:
            yield directory / f"ollama-{port}-{day.isoformat()}.jsonl"


def new_state(directory, now):
    offsets = {}
    for path in file_paths(directory, now):
        try:
            offsets[path.name] = path.stat().st_size
        except FileNotFoundError:
            pass
    return {"offsets": offsets, "incidents": {}, "recent": []}


def memory_snapshot():
    lines = []
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_physical", ctypes.c_ulonglong),
                        ("available_physical", ctypes.c_ulonglong),
                        ("total_pagefile", ctypes.c_ulonglong),
                        ("available_pagefile", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            gib = 1024 ** 3
            lines.append(f"Server RAM: {status.available_physical / gib:.1f} GiB available / "
                         f"{status.total_physical / gib:.1f} GiB total")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=4, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines.append("GPU name, used MiB, total MiB, utilization %:")
            lines.extend(result.stdout.strip().splitlines()[:8])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return lines or ["Memory snapshot unavailable."]


def report(event, previous):
    at = datetime.fromtimestamp(event["at"], timezone.utc)
    local = at.astimezone()
    lines = [
        "Ollama HTTP 500 detected after a completed inference request.",
        "This is the first observed 500 in the current incident on this listener.",
        "",
        f"Time (server local): {local.isoformat(timespec='seconds')}",
        f"Time (UTC): {at.isoformat(timespec='seconds')}",
        f"Listener: {event['listener']}",
        f"Route: {event['route']}",
        f"Model: {event['model']}",
        f"Caller label: {_caller_label(event)}",
        f"Source IP: {event['source_ip']}",
        "HTTP status: 500",
        f"Prompt tokens: {event['prompt'] if event['prompt'] is not None else 'Not recorded'}",
        f"Completion tokens: {event['completion'] if event['completion'] is not None else 'Not recorded'}",
        f"Total tokens: {event['total'] if event['total'] is not None else 'Not recorded'}",
        "",
        "Recent observed requests before this error (metadata only):",
    ]
    if previous:
        for item in previous[-10:]:
            when = datetime.fromtimestamp(item["at"], timezone.utc).isoformat(timespec="seconds")
            lines.append(f"{when} | {item['listener']} | {item['status']} | {item['model']} | {item['route']} | {item['source_ip']}")
    else:
        lines.append("No earlier requests observed since this monitor started.")
    lines.extend(["", "Memory snapshot taken when the alert was prepared:",
                  *memory_snapshot(), "",
                  "A 500 status alone does not establish that memory was the cause.",
                  "No prompt text, answer text, or credentials are included."])
    return "\n".join(lines)


def send_email(config, subject, body, idempotency_key):
    api_key = Path(config["resend_api_key_file"]).read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("Resend API key file is empty.")
    payload = json.dumps({"from": config["sender"], "to": config["recipients"],
                          "subject": subject, "text": body}).encode("utf-8")
    request = Request("https://api.resend.com/emails", data=payload, method="POST",
                      headers={"Authorization": "Bearer " + api_key,
                               "Content-Type": "application/json",
                               "Idempotency-Key": idempotency_key})
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
        if not 200 <= response.status < 300 or not isinstance(result, dict) or not result.get("id"):
            raise ValueError("Resend did not confirm an email ID.")


def process_event(state, event, config):
    incident = state["incidents"].setdefault(event["listener"],
                                               {"armed": True, "last_500": None, "pending": None})
    mode = config["rearm_mode"]
    if event["status"] == 500:
        if (mode == "quiet" and incident["last_500"] is not None
                and event["at"] - incident["last_500"] >= config["quiet_minutes"] * 60
                and incident["pending"] is None):
            incident["armed"] = True
        incident["last_500"] = event["at"]
        if incident["armed"] and incident["pending"] is None:
            incident["pending"] = {
                "subject": f"Ollama HTTP 500 on {event['listener']} ({' '.join(event['model'].split())[:80]})",
                "body": report(event, state["recent"]),
                "idempotency_key": f"ollama-500-{event['listener']}-{event['at']}-{uuid.uuid4().hex}",
            }
            incident["armed"] = False
    elif mode == "success" and event["status"] < 400 and incident["pending"] is None:
        incident["armed"] = True
    state["recent"] = list(deque((*state["recent"], {
        "at": event["at"], "listener": event["listener"], "status": event["status"],
        "model": event["model"], "route": event["route"], "source_ip": event["source_ip"],
    }), maxlen=10))


def scan(state, directory, config, now):
    for path in file_paths(directory, now):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        except OSError as error:
            logging.warning("Cannot inspect %s: %s", path.name, error)
            continue
        offset = state["offsets"].get(path.name, 0)
        if size < offset:
            logging.warning("Recorder file shrank: %s; restarting at current end", path.name)
            offset = size
            state["offsets"][path.name] = offset
        try:
            with path.open("rb") as source:
                source.seek(offset)
                while True:
                    start = source.tell()
                    line = source.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_LINE_BYTES:
                        while line and not line.endswith(b"\n"):
                            line = source.readline(MAX_LINE_BYTES + 1)
                        state["offsets"][path.name] = source.tell()
                        logging.warning("Oversized recorder record in %s at %s", path.name, start)
                        continue
                    if not line.endswith(b"\n"):
                        break
                    state["offsets"][path.name] = source.tell()
                    try:
                        raw = json.loads(line)
                    except (UnicodeDecodeError, ValueError):
                        logging.warning("Invalid recorder JSON in %s at %s", path.name, start)
                        continue
                    event = _event(raw)
                    if event is None or event["model"].startswith("cw-recorder-probe-"):
                        continue
                    process_event(state, event, config)
        except OSError as error:
            logging.warning("Cannot read %s: %s", path.name, error)
        state["offsets"][path.name] = max(state["offsets"].get(path.name, 0), offset)
    state["offsets"] = {name: position for name, position in state["offsets"].items()
                        if name in {path.name for path in file_paths(directory, now)}}


def run(directory, state_path, config_path, once=False):
    config = load_config(config_path)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = new_state(directory, time.time())
        save_state(state_path, state)
        logging.info("Initialized at current recorder file ends; historical errors skipped")
    retry_after = {}
    while True:
        scan(state, directory, config, time.time())
        save_state(state_path, state)  # Persist trigger and cursor before SMTP.
        for listener, incident in state["incidents"].items():
            pending = incident.get("pending")
            if not pending or time.time() < retry_after.get(listener, 0):
                continue
            try:
                send_email(config, pending["subject"], pending["body"],
                           pending["idempotency_key"])
            except (OSError, URLError, ValueError) as error:
                logging.error("Mail delivery failed for %s: %s", listener, error)
                retry_after[listener] = time.time() + 60
            else:
                logging.info("HTTP 500 alert accepted by Resend for %s", listener)
                incident["pending"] = None
                save_state(state_path, state)
        if once:
            break
        time.sleep(POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--recorder-dir", type=Path, default=DEFAULT_RECORDER_DIR)
    parser.add_argument("--state", type=Path,
                        default=DEFAULT_CONFIG.with_name("state.json"))
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--test-email", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if not args.recorder_dir.is_dir():
        parser.error(f"Recorder directory not found: {args.recorder_dir}")
    if args.check_config:
        print("Mail configuration is valid; no email sent.")
        return
    if args.test_email:
        send_email(config, "Ollama 500 alert test", "This is a delivery test. No HTTP 500 occurred.",
                   "ollama-500-test-" + uuid.uuid4().hex)
        print("Test email accepted by Resend.")
        return
    args.state.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.state.with_name("alert.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Ollama 500 monitor starting for both listeners")
    run(args.recorder_dir, args.state, args.config, args.once)


if __name__ == "__main__":
    main()
