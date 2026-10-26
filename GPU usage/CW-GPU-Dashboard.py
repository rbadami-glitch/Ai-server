"""Local GPU and Ollama dashboard for the Windows AI server.

Run with Python 3 on the server. The page listens only on 127.0.0.1:8791.
It reads nvidia-smi and the two Ollama /api/ps endpoints. Once per minute it
saves up to seven calendar-day JSON files under GPU-RCA-Checks on the AI server,
outside the Git source folder. It never changes Ollama services or model state.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 8791
SERVICES = (
    ("General", 11434),
    ("Private", 11435),
)
HISTORY_DIR = Path(
    r"E:\AI\ollama\OpenWebUI-Monitoring\GPU-RCA-Checks\CW-GPU-Dashboard-History"
)
SAMPLE_SECONDS = 60
RETENTION_DAYS = 7
REQUEST_LOG_DIR = Path(r"E:\AI\ollama\OpenWebUI-Monitoring\Direct-Usage")
REQUEST_LOG_PORTS = (11434, 11435)
REQUEST_TAIL_BYTES = 4 * 1024 * 1024
REQUEST_LINE_BYTES = 8192
REQUEST_ROWS = 200


def request_logs(hours=24, directory=REQUEST_LOG_DIR, now=None):
    """Read a bounded, metadata-only tail of Ollama ingress recorder files.

    This dashboard is a reader. File presence does not prove capture completeness.
    The recorder must run separately on both Ollama ingress listeners.
    """
    if hours not in (1, 24, 168):
        raise ValueError("Request range must be 1, 24, or 168 hours.")
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    day = start.date()
    rows = []
    files_found = partial_files = invalid_rows = dropped = 0
    while day <= now.date():
        for port in REQUEST_LOG_PORTS:
            path = directory / f"ollama-{port}-{day.isoformat()}.jsonl"
            try:
                with path.open("rb") as stream:
                    files_found += 1
                    size = stream.seek(0, os.SEEK_END)
                    if size > REQUEST_TAIL_BYTES:
                        partial_files += 1
                        stream.seek(size - REQUEST_TAIL_BYTES)
                        stream.readline()  # Discard the first incomplete line.
                    else:
                        stream.seek(0)
                    for line in stream:
                        if len(line) > REQUEST_LINE_BYTES or not line.endswith(b"\n"):
                            invalid_rows += 1
                            continue
                        try:
                            raw = json.loads(line)
                        except (UnicodeDecodeError, ValueError):
                            invalid_rows += 1
                            continue
                        if not isinstance(raw, dict) or raw.get("schema") != 1:
                            invalid_rows += 1
                            continue
                        at = raw.get("at")
                        if type(at) is not int or not start.timestamp() <= at <= now.timestamp():
                            continue
                        if raw.get("kind") == "dropped":
                            count = raw.get("dropped")
                            if type(count) is int and count >= 0:
                                dropped += count
                            else:
                                invalid_rows += 1
                            continue
                        status = raw.get("status")
                        if (str(raw.get("listener")) != str(port) or
                                type(status) is not int or not 100 <= status <= 599):
                            invalid_rows += 1
                            continue
                        def count(key):
                            value = raw.get(key)
                            return value if type(value) is int and value >= 0 else None
                        def label(key):
                            value = raw.get(key)
                            return value[:256] if isinstance(value, str) and value else "Unknown"
                        prompt = count("prompt_tokens")
                        completion = count("completion_tokens")
                        total = count("total_tokens")
                        if total is None and prompt is not None and completion is not None:
                            total = prompt + completion
                        rows.append({
                            "at": at, "listener": port, "status": status,
                            "model": label("model"), "route": label("route"),
                            "source_ip": label("source_ip"),
                            "prompt_tokens": prompt, "completion_tokens": completion,
                            "total_tokens": total, "duration_ms": count("duration_ms"),
                        })
            except FileNotFoundError:
                continue
            except OSError:
                invalid_rows += 1
        day += timedelta(days=1)
    rows.sort(key=lambda row: row["at"], reverse=True)
    return {"hours": hours, "rows": rows[:REQUEST_ROWS],
            "files_found": files_found, "partial_files": partial_files,
            "invalid_rows": invalid_rows, "dropped": dropped,
            "shown": min(len(rows), REQUEST_ROWS),
            "more_rows": max(0, len(rows) - REQUEST_ROWS),
            "recorder_configured": files_found > 0}


def read_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        return json.load(response)


def run_nvidia_smi(query):
    result = subprocess.run(
        ["nvidia-smi", query, "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def to_int(value):
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def snapshot():
    result = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gpus": [], "processes": [], "services": [], "errors": [],
    }
    try:
        for line in run_nvidia_smi(
            "--query-gpu=index,memory.total,memory.used,memory.free"
        ):
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 4 or any(to_int(field) is None for field in fields):
                raise ValueError("unexpected GPU memory output")
            index, total, used, free = map(to_int, fields)
            result["gpus"].append({
                "index": index, "total_mib": total,
                "used_mib": used, "free_mib": free,
            })
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        result["errors"].append("GPU memory: " + type(error).__name__)

    try:
        for line in run_nvidia_smi(
            "--query-compute-apps=pid,used_gpu_memory"
        ):
            fields = [field.strip() for field in line.split(",")]
            if len(fields) >= 2 and to_int(fields[0]) is not None:
                result["processes"].append({
                    "pid": to_int(fields[0]),
                    "gpu_mib": to_int(fields[1]),
                })
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        result["errors"].append("GPU processes: " + type(error).__name__)

    for name, port in SERVICES:
        service = {"name": name, "port": port, "online": False,
                   "models": [], "error": None}
        try:
            payload = read_json(f"http://127.0.0.1:{port}/api/ps")
            if not isinstance(payload, dict) or not isinstance(
                payload.get("models"), list
            ):
                raise ValueError("unexpected /api/ps response")
            service["online"] = True
            for item in payload["models"]:
                if not isinstance(item, dict):
                    continue
                size_vram = item.get("size_vram")
                try:
                    size_mib = round(int(size_vram) / 1048576)
                except (ValueError, TypeError):
                    size_mib = None
                service["models"].append({
                    "name": str(item.get("name") or "Unknown"),
                    "expires_at": item.get("expires_at"),
                    "size_vram_mib": size_mib,
                    "context_length": item.get("context_length"),
                })
        except (OSError, ValueError, TimeoutError) as error:
            service["error"] = type(error).__name__
        result["services"].append(service)
    return result


class HistoryStore:
    """One atomic JSON file per local day; only this directory is pruned."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError("GPU history directory must not be a symlink.")
        self.lock = threading.RLock()

    def _path(self, day):
        return self.directory / f"{day.isoformat()}.json"

    def _read_day(self, day):
        path = self._path(day)
        if not path.exists():
            return []
        if path.is_symlink():
            raise RuntimeError(f"GPU history file for {day.isoformat()} must not be a symlink.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("samples"), list):
                raise ValueError("unexpected history format")
            return payload["samples"]
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Cannot read GPU history for {day.isoformat()}: {type(error).__name__}") from error

    def record(self, data):
        checked = datetime.fromisoformat(data["checked_at"])
        day = checked.astimezone().date()
        gpu = next((item for item in data.get("gpus", []) if item.get("index") == 0), None)
        sample = {
            "at": checked.isoformat(timespec="seconds"),
            "used_mib": gpu.get("used_mib") if gpu else None,
            "total_mib": gpu.get("total_mib") if gpu else None,
            "free_mib": gpu.get("free_mib") if gpu else None,
            "loaded_models": sum(len(item.get("models", [])) for item in data.get("services", [])),
        }
        with self.lock:
            samples = self._read_day(day)
            if samples and str(samples[-1].get("at", ""))[:16] == sample["at"][:16]:
                samples[-1] = sample
            else:
                samples.append(sample)
            path = self._path(day)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.directory,
                    prefix=f".{day.isoformat()}-", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump({"date": day.isoformat(), "samples": samples},
                              stream, separators=(",", ":"))
                os.replace(temporary, path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            oldest = day - timedelta(days=RETENTION_DAYS - 1)
            for old_path in self.directory.glob("????-??-??.json"):
                try:
                    file_day = date.fromisoformat(old_path.stem)
                except ValueError:
                    continue
                if file_day < oldest or file_day > day:
                    if old_path.is_symlink() or old_path.resolve().parent != self.directory.resolve():
                        continue
                    try:
                        old_data = json.loads(old_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if (isinstance(old_data, dict) and old_data.get("date") == old_path.stem
                            and isinstance(old_data.get("samples"), list)):
                        old_path.unlink()

    def query(self, view, hours=None, selected_day=None, now=None, minutes=None):
        now = now or datetime.now().astimezone()
        today = now.date()
        oldest = today - timedelta(days=RETENTION_DAYS - 1)
        if view == "minutes":
            if not isinstance(minutes, int) or not 1 <= minutes <= 10080:
                raise ValueError("Minutes must be between 1 and 10080.")
            start = now - timedelta(minutes=minutes)
            end = now
            days = [oldest + timedelta(days=i) for i in range(RETENTION_DAYS)]
        elif view == "hours":
            if not isinstance(hours, int) or not 1 <= hours <= 168:
                raise ValueError("Hours must be between 1 and 168.")
            start = now - timedelta(hours=hours)
            end = now
            days = [oldest + timedelta(days=i) for i in range(RETENTION_DAYS)]
        elif view == "day":
            try:
                chosen = date.fromisoformat(selected_day)
            except (TypeError, ValueError) as error:
                raise ValueError("Choose a date in the last seven days.") from error
            if not oldest <= chosen <= today:
                raise ValueError("Choose a date in the last seven days.")
            days = [chosen]
            start = datetime.combine(chosen, datetime.min.time()).astimezone()
            end = start + timedelta(days=1)
        elif view == "week":
            days = [oldest + timedelta(days=i) for i in range(RETENTION_DAYS)]
            start = datetime.combine(oldest, datetime.min.time()).astimezone()
            end = now
        else:
            raise ValueError("View must be minutes, hours, day, or week.")
        with self.lock:
            samples = [item for day in days for item in self._read_day(day)]
        points = []
        for item in samples:
            try:
                at = datetime.fromisoformat(item["at"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= at <= end:
                points.append(item)
        points.sort(key=lambda item: item["at"])
        return {
            "view": view, "minutes": minutes if view == "minutes" else None,
            "hours": hours if view == "hours" else None,
            "day": selected_day if view == "day" else None,
            "oldest_day": oldest.isoformat(), "today": today.isoformat(),
            "start": start.isoformat(), "end": end.isoformat(),
            "sample_interval_seconds": SAMPLE_SECONDS,
            "samples": points,
        }


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>CW GPU Watch</title>
<style>
  :root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#eaf1fa;background:#0a1020}
  *{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 80% -20%,#173253 0,transparent 42%),#0a1020}
  .shell{max-width:1370px;margin:auto;padding:28px 30px 50px}
  header{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:25px}
  .eyebrow{color:#70d6cb;text-transform:uppercase;font-size:11px;font-weight:800;letter-spacing:.2em;margin-bottom:8px}
  h1{font-size:32px;letter-spacing:-.04em;margin:0 0 7px}h2{font-size:17px;margin:0 0 16px;letter-spacing:-.015em}
  .muted{color:#8f9eb3}.subtitle{color:#a9b7ca;font-size:14px;margin:0;line-height:1.5}
  .head-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}
  button{cursor:pointer;background:#192a40;color:#eaf1fa;border:1px solid #36506b;border-radius:10px;padding:10px 14px;font-weight:700}
  button:hover{background:#263b55}button:focus-visible{outline:2px solid #66d8cf;outline-offset:2px}
  .pill{display:inline-flex;align-items:center;gap:8px;border-radius:999px;padding:9px 12px;background:#142437;border:1px solid #294158;font-size:12px;font-weight:700;white-space:nowrap}
  .dot{height:8px;width:8px;border-radius:50%;background:#83e5a2;box-shadow:0 0 9px #83e5a2}
  .dot.bad{background:#ff8c88;box-shadow:0 0 9px #ff8c88}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.card,.panel{background:#111c2d;border:1px solid #243750;border-radius:16px;box-shadow:0 12px 35px #0002}
  .card{padding:20px;min-height:130px}.label{color:#91a4ba;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.09em}
  .value{font-size:35px;font-weight:800;letter-spacing:-.045em;margin:12px 0 3px;font-variant-numeric:tabular-nums}
  .unit{font-size:15px;color:#9eb0c6;font-weight:500;letter-spacing:0}.detail{color:#9daec2;font-size:12px}
  .status{margin:16px 0;padding:16px 18px;border:1px solid #316d66;background:#11312f;border-radius:12px;display:flex;gap:12px;align-items:flex-start}
  .status.warn{border-color:#80603b;background:#30261d}.status.error{border-color:#8c474e;background:#321e2a}
  .status strong{display:block;margin-bottom:4px}.status span{font-size:13px;color:#bed0d9;line-height:1.5}
  .main{display:grid;grid-template-columns:1.5fr 1fr;gap:14px}.panel{padding:20px;margin-bottom:14px;min-width:0}
  .panel-title{display:flex;align-items:center;justify-content:space-between;gap:12px}.small{font-size:12px;color:#93a5ba}
  .meter{height:10px;border-radius:9px;background:#26384b;overflow:hidden;margin:17px 0 8px}.meter>div{height:100%;background:linear-gradient(90deg,#56d4c2,#8fe3a1);border-radius:9px;transition:width .35s}
  .history-controls{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:14px 0 8px}
  .history-controls label{display:grid;gap:5px;color:#a8bbce;font-size:12px;font-weight:700}
  .history-controls select,.history-controls input{background:#0c1829;color:#eaf1fa;border:1px solid #3a536d;border-radius:8px;padding:8px 10px;min-height:37px;font:inherit}
  .history-controls input[type=number]{width:80px}.history-controls [hidden]{display:none}
  .history-controls select:focus-visible,.history-controls input:focus-visible{outline:2px solid #66d8cf;outline-offset:2px}
  .axis-chart{display:grid;grid-template-columns:58px minmax(0,1fr);gap:9px;margin-top:16px}
  .y-axis{height:180px;display:flex;flex-direction:column;justify-content:space-between;text-align:right;color:#a4b7cb;font-size:11px;font-variant-numeric:tabular-nums}
  svg{width:100%;height:180px;display:block;background:#0c1829;border:1px solid #30465d;border-radius:7px}
  .x-axis{display:flex;justify-content:space-between;gap:8px;margin:7px 0 0 67px;color:#a4b7cb;font-size:11px;font-variant-numeric:tabular-nums}
  .x-axis span:nth-child(2){text-align:center}.x-axis span:last-child{text-align:right}
  .history-summary{margin-top:12px;line-height:1.5}
  .legend{display:flex;justify-content:space-between;color:#879bb1;font-size:11px;margin-top:7px}
  .service{border:1px solid #30435b;background:#142236;border-radius:12px;padding:15px;margin-top:11px}
  .service-top{display:flex;justify-content:space-between;gap:8px;align-items:center}.service-name{font-weight:800}.badge{font-size:11px;border:1px solid #366e65;background:#173e38;color:#89e8c9;border-radius:99px;padding:4px 8px;font-weight:800}
  .badge.off{border-color:#7b474d;background:#442832;color:#ffaba8}.service-meta{font-size:12px;color:#9aaec2;margin-top:5px}
  .model{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;border-top:1px solid #2b4056;padding-top:11px;margin-top:11px}
  .model-name{font-weight:750;overflow-wrap:anywhere}.model-info{font-size:12px;color:#97aabd;margin-top:5px}.expiry{color:#a4ebd6;font-size:12px;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
  .empty{font-size:13px;color:#93a6ba;padding-top:12px}.route{display:grid;grid-template-columns:1fr auto;gap:7px 10px;padding:9px 0;border-bottom:1px solid #25394e;font-size:13px}.route:last-child{border-bottom:0}
  .route small{display:block;color:#8497ab;font-size:11px;margin-top:3px}.route-state{font-size:11px;color:#8d9caf;font-weight:800;white-space:nowrap}.route-state.on{color:#8de2c5}
  table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:10px 5px;border-bottom:1px solid #263a50}th{color:#8fa2b7;font-weight:700}td{color:#d9e4ef;font-variant-numeric:tabular-nums}
  .table-wrap{overflow:auto}.table-wrap table{min-width:880px}.request-note{margin:10px 0 14px;line-height:1.5}
  footer{color:#8193a8;font-size:12px;line-height:1.6;padding-top:6px}
  @media(max-width:950px){.grid{grid-template-columns:repeat(2,1fr)}.main{grid-template-columns:1fr}}
  @media(max-width:600px){.shell{padding:19px 14px}header{display:block}.head-actions{justify-content:flex-start;margin-top:17px}h1{font-size:27px}.grid{gap:8px}.card{padding:15px;min-height:118px}.value{font-size:29px}}
</style>
</head>
<body><div class="shell">
  <header><div><div class="eyebrow">Curtiss-Wright · AI operations</div><h1>GPU Watch</h1><p class="subtitle">Live GPU capacity and model residency across both Ollama services.</p></div>
    <div class="head-actions"><span class="pill"><span id="live-dot" class="dot"></span><span id="live-text">Connecting</span></span><button id="pause">Pause view</button><button id="copy">Copy snapshot</button></div></header>
  <section class="grid" aria-label="Current metrics">
    <div class="card"><div class="label">GPU free</div><div class="value" id="free">—</div><div class="detail" id="free-detail">Waiting for GPU</div></div>
    <div class="card"><div class="label">GPU used</div><div class="value" id="used">—</div><div class="detail" id="used-detail">—</div></div>
    <div class="card"><div class="label">Models loaded</div><div class="value" id="loaded">—</div><div class="detail">Across both services</div></div>
    <div class="card"><div class="label">Services online</div><div class="value" id="online">—</div><div class="detail">General 11434 · Private 11435</div></div>
  </section>
  <div class="status" id="status" role="status"><div style="font-size:20px" id="status-icon">●</div><div><strong id="status-title">Connecting</strong><span id="status-detail">Reading both Ollama services and GPU memory.</span></div></div>
  <div class="main"><div>
    <section class="panel"><div class="panel-title"><h2>GPU 0 memory used over time</h2><span class="small" id="checked">—</span></div><div class="small">Vertical axis: VRAM in use (GiB), from zero to GPU capacity—not GPU compute utilization. Horizontal axis: local date and time. History is sampled once per minute.</div><div class="history-controls"><label>Range<select id="history-mode"><option value="range">Last N minutes or hours</option><option value="day">Selected day</option><option value="week">Last 7 days</option></select></label><label id="count-control">Amount<input id="history-count" type="number" min="1" max="168" step="1" value="2"></label><label id="unit-control">Unit<select id="history-unit"><option value="hours">Hours</option><option value="minutes">Minutes</option></select></label><label id="day-control" hidden>Day<input id="history-date" type="date"></label></div><div class="meter"><div id="meter" style="width:0%"></div></div><div class="small" id="meter-label">—</div><div class="axis-chart"><div class="y-axis" aria-label="GPU memory used in GiB"><span id="y100">—</span><span id="y75">—</span><span id="y50">—</span><span id="y25">—</span><span>0 GiB</span></div><svg viewBox="0 0 600 150" preserveAspectRatio="none" role="img" aria-label="GPU 0 used memory over selected time range"><path d="M0 6 H600 M0 40.5 H600 M0 75 H600 M0 109.5 H600 M0 144 H600" fill="none" stroke="#35506a" stroke-width=".6"></path><path id="area" fill="#52d2c322" d=""></path><path id="line" fill="none" stroke="#63dece" stroke-width="2.5" d=""></path></svg></div><div class="x-axis"><span id="x-start">—</span><span id="x-mid">—</span><span id="x-end">—</span></div><div class="small history-summary" id="history-summary" role="status">Loading history…</div></section>
    <section class="panel"><h2>Loaded models by service</h2><div id="services"></div></section>
  </div><div>
    <section class="panel"><h2>Open WebUI chat routes</h2><div class="small">A chat entry and its underlying model share the same memory. “Loaded” below means the base model appears in Ollama now.</div><div id="routes" style="margin-top:13px"></div></section>
    <section class="panel"><h2>GPU processes</h2><div id="processes" class="small">Waiting for process data</div></section>
  </div></div>
  <section class="panel"><div class="panel-title"><h2>Recent Ollama requests</h2><div class="history-controls"><label>Range<select id="request-hours"><option value="1">Last hour</option><option value="24" selected>Last 24 hours</option><option value="168">Last 7 days</option></select></label><button id="request-refresh">Refresh</button></div></div><div class="small request-note" id="request-notice" role="status">Checking for request records. This dashboard reads records; it does not intercept requests.</div><div class="table-wrap"><table><thead><tr><th>Completed</th><th>Listener</th><th>Route</th><th>Model</th><th>Source IP</th><th>Status</th><th>Input</th><th>Output</th><th>Total</th><th>Duration</th></tr></thead><tbody id="request-rows"><tr><td colspan="10">Loading...</td></tr></tbody></table></div></section>
  <footer>Live status refreshes every 3 seconds. While this server script runs, it saves one GPU sample per minute in seven daily JSON files, even if this browser tab is closed. It cannot recover time before recording began or gaps while the script was stopped. The request table needs a separate recorder on both Ollama listeners; missing records do not mean there were no requests. Idle expiry is the current Ollama timer; new requests can move it. GPU memory includes other processes and overhead, so it may differ from model VRAM totals. Press Ctrl+C in PowerShell to stop recording and the dashboard.</footer>
</div>
<script>
const el=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mib=n=>Number.isFinite(n)?Math.round(n).toLocaleString()+' MiB':'—';
const routes=[
  {label:'Curtiss-Wright Corporate',base:'llama4:latest',service:'Private'},
  {label:'Curtiss-Wright Policies',base:'codestral:22b',service:'General'},
  {label:'Gemma 4 12B',base:'gemma4:12b',service:'Private'},
  {label:'Gemma 4 31B',base:'gemma4:31b',service:'Private'},
  {label:'Devstral',base:'devstral:latest',service:'General'},
  {label:'Codestral',base:'codestral:22b',service:'General'}
];
let latest=null,paused=false,historyData=null,busy=false,historyRequest=0;
function expiry(value){
  if(!value)return 'Expiry unknown';
  const end=Date.parse(value);if(!Number.isFinite(end))return 'Expiry unknown';
  const seconds=Math.ceil((end-Date.now())/1000);
  if(seconds<=0)return 'Unload due';
  if(seconds>=31536000)return 'Long keep-alive';
  return 'Unload in '+seconds+'s';
}
function render(){
  if(!latest)return;
  const data=latest,gpu=data.gpus?.[0],services=data.services||[];
  const models=services.flatMap(s=>s.models||[]);
  const up=services.filter(s=>s.online).length;
  el('free').innerHTML=gpu?esc((gpu.free_mib/1024).toFixed(1))+'<span class="unit"> GiB</span>':'—';
  el('free-detail').textContent=gpu?mib(gpu.free_mib)+' of '+mib(gpu.total_mib)+' total':'GPU data unavailable';
  el('used').innerHTML=gpu?esc((gpu.used_mib/1024).toFixed(1))+'<span class="unit"> GiB</span>':'—';
  el('used-detail').textContent=gpu?mib(gpu.used_mib)+' in use':'GPU data unavailable';
  el('loaded').textContent=models.length;el('online').textContent=up+' / '+services.length;
  el('checked').textContent='Checked '+new Date(data.checked_at).toLocaleTimeString();
  el('meter').style.width=gpu&&gpu.total_mib?Math.min(100,100*gpu.used_mib/gpu.total_mib)+'%':'0%';
  el('meter-label').textContent=gpu?Math.round(100*gpu.used_mib/gpu.total_mib)+'% used · '+mib(gpu.free_mib)+' free':'No GPU reading';
  const status=el('status'),title=el('status-title'),detail=el('status-detail');
  status.className='status';
  if(up<services.length||!gpu){status.classList.add('error');title.textContent='Monitoring data incomplete';detail.textContent=[...services.filter(s=>!s.online).map(s=>s.name+' service unavailable'),...(data.errors||[])].join(' · ')||'Check the GPU tool and Ollama services.';el('status-icon').textContent='!';}
  else if(gpu.free_mib<15360){status.classList.add('warn');title.textContent='Low free GPU memory';detail.textContent=mib(gpu.free_mib)+' free. A second large model may fail to load while the current model stays in memory.';el('status-icon').textContent='!';}
  else if(models.length){title.textContent=models.length+' model'+(models.length===1?'':'s')+' loaded';detail.textContent='Watch the expiry timer below. If the model is idle, it should leave memory after the timer expires.';el('status-icon').textContent='●';}
  else{title.textContent='No models loaded';detail.textContent='The GPU has capacity available. A new chat will load its model automatically.';el('status-icon').textContent='✓';}
  el('services').innerHTML=services.map(s=>{
    const rows=(s.models||[]).map(m=>'<div class="model"><div><div class="model-name">'+esc(m.name)+'</div><div class="model-info">'+(m.size_vram_mib==null?'VRAM unknown':mib(m.size_vram_mib)+' model VRAM')+(m.context_length?' · context '+Number(m.context_length).toLocaleString():'')+'</div></div><div class="expiry" data-expiry="'+esc(m.expires_at||'')+'">'+esc(expiry(m.expires_at))+'</div></div>').join('');
    return '<div class="service"><div class="service-top"><div class="service-name">'+esc(s.name)+'</div><span class="badge '+(s.online?'':'off')+'">'+(s.online?'ONLINE':'UNAVAILABLE')+'</span></div><div class="service-meta">Port '+esc(s.port)+' · '+(s.online?(s.models||[]).length+' loaded':'No response')+'</div>'+(s.online?(rows||'<div class="empty">No models loaded</div>'):'<div class="empty">'+esc(s.error||'Cannot read service')+'</div>')+'</div>';
  }).join('');
  el('routes').innerHTML=routes.map(r=>{
    const service=services.find(s=>s.name===r.service),isLoaded=!!service?.models?.some(m=>m.name===r.base);
    return '<div class="route"><div><strong>'+esc(r.label)+'</strong><small>'+esc(r.service)+' · '+esc(r.base)+'</small></div><div class="route-state '+(isLoaded?'on':'')+'">'+(service?.online?(isLoaded?'LOADED':'IDLE'):'UNKNOWN')+'</div></div>';
  }).join('');
  const processes=data.processes||[];
  el('processes').innerHTML=processes.length?'<table><thead><tr><th>PID</th><th>GPU memory</th></tr></thead><tbody>'+processes.map(p=>'<tr><td>'+esc(p.pid)+'</td><td>'+esc(mib(p.gpu_mib))+'</td></tr>').join('')+'</tbody></table>':'No GPU compute processes reported';
  if(!historyData?.samples?.length)drawChart();
}
function drawChart(){
  const samples=historyData?.samples||[],start=Date.parse(historyData?.start||''),end=Date.parse(historyData?.end||'');
  let total=latest?.gpus?.[0]?.total_mib||0;
  for(const sample of samples)if(Number.isFinite(sample.total_mib))total=Math.max(total,sample.total_mib);
  const gib=value=>(value/1024).toFixed(1)+' GiB';
  for(const [id,fraction] of [['y100',1],['y75',.75],['y50',.5],['y25',.25]])el(id).textContent=total?gib(total*fraction):'—';
  const formatTime=value=>Number.isFinite(value)?new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—';
  el('x-start').textContent=formatTime(start);el('x-mid').textContent=formatTime((start+end)/2);el('x-end').textContent=formatTime(end);
  if(!total||!Number.isFinite(start)||!Number.isFinite(end)){
    el('line').setAttribute('d','');el('area').setAttribute('d','');
    if(historyData)el('history-summary').textContent='No GPU capacity reading in this range. Check nvidia-smi and the dashboard server.';
    return;
  }
  const span=Math.max(1,end-start);let line='',area='',segment=[],count=0,sum=0,peak=0;
  const flush=()=>{
    if(!segment.length)return;
    line+='M '+segment.map(p=>p.join(' ')).join(' L ')+(segment.length===1?' L '+(segment[0][0]+1)+' '+segment[0][1]:'')+' ';
    area+='M '+segment[0][0]+' 144 L '+segment.map(p=>p.join(' ')).join(' L ')+' L '+segment.at(-1)[0]+' 144 Z ';
    segment=[];
  };
  for(const sample of samples){
    const time=Date.parse(sample.at),used=sample.used_mib;
    if(!Number.isFinite(time)||typeof used!=='number'||!Number.isFinite(used)){flush();continue}
    const x=Math.max(0,Math.min(600,600*(time-start)/span));
    const y=Math.max(6,Math.min(144,144-138*used/total));
    segment.push([x.toFixed(2),y.toFixed(2)]);count++;sum+=used;peak=Math.max(peak,used);
  }
  flush();el('line').setAttribute('d',line);el('area').setAttribute('d',area);
  el('history-summary').textContent=count?`${count.toLocaleString()} saved readings · Average ${gib(sum/count)} · Peak ${gib(peak)}. Gaps indicate missing GPU readings.`:'No saved GPU readings in this range. History starts when this dashboard script begins running.';
}
function updateHistoryControls(){
  const mode=el('history-mode').value;
  el('count-control').hidden=mode!=='range';el('unit-control').hidden=mode!=='range';el('day-control').hidden=mode!=='day';
  const count=el('history-count'),max=el('history-unit').value==='minutes'?10080:168;
  count.max=String(max);
  if(Number(count.value)>max)count.value=String(max);
  if(mode==='day'&&!el('history-date').value){
    const now=new Date(),localDay=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
    el('history-date').value=historyData?.today||localDay;
  }
}
async function loadHistory(){
  if(paused)return;
  const mode=el('history-mode').value,unit=el('history-unit').value;
  const params=new URLSearchParams({view:mode==='range'?unit:mode});
  if(mode==='range')params.set(unit,el('history-count').value);
  if(mode==='day')params.set('date',el('history-date').value);
  const request=++historyRequest;
  try{
    const response=await fetch('/api/history?'+params,{cache:'no-store'}),data=await response.json();
    if(!response.ok)throw Error(data.error||'History request failed');
    if(request!==historyRequest)return;
    historyData=data;el('history-date').min=data.oldest_day;el('history-date').max=data.today;
    drawChart();
  }catch(error){
    if(request!==historyRequest)return;
    el('history-summary').textContent='Cannot load history: '+error.message;
  }
}
async function refresh(){
  if(paused||busy)return;busy=true;
  try{
    const response=await fetch('/api/snapshot',{cache:'no-store'});
    if(!response.ok)throw Error('Dashboard returned HTTP '+response.status);
    latest=await response.json();
    el('live-dot').className='dot';el('live-text').textContent='Live · 3s';render();
  }catch(error){el('live-dot').className='dot bad';el('live-text').textContent='Connection lost';el('status').className='status error';el('status-title').textContent='Dashboard cannot refresh';el('status-detail').textContent=error.message;}
  finally{busy=false}
}
let requestBusy=false;
async function loadRequests(){
  if(paused||requestBusy)return;requestBusy=true;
  try{
    const response=await fetch('/api/requests?hours='+encodeURIComponent(el('request-hours').value),{cache:'no-store'});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Request log read failed');
    const issues=[];
    if(!data.recorder_configured)issues.push('No Ollama recorder files found. Request capture is not running or has not produced files.');
    else issues.push('Showing '+data.shown+' recent observed records; file presence does not prove complete capture.');
    if(data.more_rows)issues.push(data.more_rows+' older rows omitted from this view.');
    if(data.partial_files)issues.push(data.partial_files+' large file tails read only.');
    if(data.dropped)issues.push(data.dropped+' requests reported dropped by recorder.');
    if(data.invalid_rows)issues.push(data.invalid_rows+' invalid or unreadable records.');
    el('request-notice').textContent=issues.join(' ');
    el('request-rows').innerHTML=data.rows.length?data.rows.map(r=>'<tr><td>'+esc(new Date(r.at*1000).toLocaleString())+'</td><td>'+esc(r.listener)+'</td><td>'+esc(r.route)+'</td><td>'+esc(r.model)+'</td><td>'+esc(r.source_ip)+'</td><td>'+esc(r.status)+'</td><td>'+esc(r.prompt_tokens??'Not recorded')+'</td><td>'+esc(r.completion_tokens??'Not recorded')+'</td><td>'+esc(r.total_tokens??'Not recorded')+'</td><td>'+esc(r.duration_ms==null?'Not recorded':r.duration_ms+' ms')+'</td></tr>').join(''):'<tr><td colspan="10">No observed records in this range.</td></tr>';
  }catch(error){el('request-notice').textContent='Could not read request logs: '+error.message}
  finally{requestBusy=false}
}
el('pause').onclick=()=>{paused=!paused;el('pause').textContent=paused?'Resume view':'Pause view';el('live-text').textContent=paused?'View paused · recording continues':'Live · 3s';if(!paused){refresh();loadHistory();loadRequests()}};
el('history-mode').onchange=()=>{updateHistoryControls();loadHistory()};
el('history-unit').onchange=()=>{updateHistoryControls();loadHistory()};
el('history-count').onchange=loadHistory;el('history-date').onchange=loadHistory;
el('copy').onclick=async()=>{
  if(!latest)return;
  const text=JSON.stringify(latest,null,2);
  try{await navigator.clipboard.writeText(text);el('copy').textContent='Copied';setTimeout(()=>el('copy').textContent='Copy snapshot',1800)}
  catch{el('copy').textContent='Copy blocked';setTimeout(()=>el('copy').textContent='Copy snapshot',1800)}
};
el('request-hours').onchange=loadRequests;el('request-refresh').onclick=loadRequests;
updateHistoryControls();refresh();loadHistory();loadRequests();setInterval(refresh,3000);setInterval(loadHistory,60000);setInterval(loadRequests,15000);
setInterval(()=>{document.querySelectorAll('[data-expiry]').forEach(node=>node.textContent=expiry(node.dataset.expiry))},1000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, status, payload, content_type):
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; "
                         "base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in (
            f"localhost:{PORT}", f"127.0.0.1:{PORT}"
        ):
            return self.send(403, '{"error":"local host only"}', "application/json")
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if parsed.path == "/api/snapshot":
            return self.send(200, json.dumps(snapshot()),
                             "application/json; charset=utf-8")
        if parsed.path == "/api/requests":
            params = parse_qs(parsed.query)
            try:
                hours = int((params.get("hours") or ["24"])[0])
                result = request_logs(hours)
            except ValueError as error:
                return self.send(400, json.dumps({"error": str(error)}),
                                 "application/json; charset=utf-8")
            return self.send(200, json.dumps(result), "application/json; charset=utf-8")
        if parsed.path == "/api/history":
            params = parse_qs(parsed.query)
            view = (params.get("view") or ["hours"])[0]
            raw_hours = (params.get("hours") or ["2"])[0]
            raw_minutes = (params.get("minutes") or [""])[0]
            try:
                hours = int(raw_hours) if view == "hours" else None
                minutes = int(raw_minutes) if view == "minutes" else None
                result = self.server.history_store.query(
                    view, hours=hours, selected_day=(params.get("date") or [None])[0],
                    minutes=minutes,
                )
            except ValueError as error:
                return self.send(400, json.dumps({"error": str(error)}),
                                 "application/json; charset=utf-8")
            except (OSError, RuntimeError) as error:
                return self.send(500, json.dumps({"error": str(error)}),
                                 "application/json; charset=utf-8")
            return self.send(200, json.dumps(result), "application/json; charset=utf-8")
        return self.send(404, '{"error":"not found"}', "application/json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    store = HistoryStore(HISTORY_DIR)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.history_store = store
    try:
        store.record(snapshot())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Initial history sample failed: {type(error).__name__}: {error}", flush=True)
    stop = threading.Event()

    def sample_loop():
        while not stop.wait(SAMPLE_SECONDS):
            try:
                store.record(snapshot())
            except (OSError, RuntimeError, ValueError) as error:
                print(f"GPU history sample failed: {type(error).__name__}: {error}", flush=True)

    sampler = threading.Thread(target=sample_loop, name="gpu-history-sampler", daemon=True)
    sampler.start()
    url = f"http://{HOST}:{PORT}/"
    print(f"Local GPU dashboard: {url}", flush=True)
    print(f"History: {HISTORY_DIR} (one sample/minute; seven daily JSON files)", flush=True)
    print("Press Ctrl+C to stop. No Ollama service is changed.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        sampler.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
