"""Read metadata-only direct Ollama events produced by the proposed recorder.

One UTF-8 JSON object per line. Files are named ollama-PORT-YYYY-MM-DD.jsonl,
where the date is UTC and PORT is 11434 or 11435. This module never writes data.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone


PORTS = ("11434", "11435")
CALLER_LABELS = {
    ("11434", "10.104.74.10", "/v1/chat/completions"): "Open WebUI",
    ("11434", "10.104.74.10", "/api/chat"): "AI server (local)",
    ("11434", "10.104.74.13", "/api/chat"): "LiteLLM",
    ("11434", "10.104.74.21", "/v1/chat/completions"): "PCB server",
    ("11434", "10.104.74.21", "/api/chat"): "PCB server",
    ("11435", "127.0.0.1", "/v1/chat/completions"): "Open WebUI",
}
PROBE_MODEL_PREFIX = "cw-recorder-probe-"
MAX_LINE_BYTES = 8192
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ACCESS_LOG_BYTES = 8 * 1024 * 1024
ACCESS_LINE = re.compile(
    r'^\[GIN\]\s+(\d{4}/\d{2}/\d{2})\s+-\s+(\d{2}:\d{2}:\d{2})'
    r'\s*\|\s*(\d{3})\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([A-Z]+)\s+"([^"]+)"'
)
INFERENCE_ROUTES = frozenset({
    "/api/chat", "/api/generate", "/api/embed", "/api/embeddings",
    "/v1/chat/completions", "/v1/completions", "/v1/embeddings",
    "/v1/responses", "/v1/messages", "/v1/images/generations",
    "/v1/images/edits", "/v1/audio/transcriptions",
})


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _duration_stats(values):
    if not values:
        return {"duration_coverage": 0, "avg_ms": None,
                "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {"duration_coverage": len(ordered),
            "avg_ms": round(sum(ordered) / len(ordered), 1),
            "p50_ms": ordered[(len(ordered) - 1) // 2],
            "p95_ms": ordered[(95 * len(ordered) + 99) // 100 - 1],
            "max_ms": ordered[-1]}


def _caller_label(event):
    """Label only the observed listener, peer IP, and route combinations."""
    return CALLER_LABELS.get(
        (event["listener"], event["source_ip"], event["route"]),
        "Unidentified",
    )


def _event(raw):
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        return None
    at, status = _count(raw.get("at")), _count(raw.get("status"))
    if at is None or status is None or not 100 <= status <= 599:
        return None
    listener = str(raw.get("listener") or "")
    if listener not in PORTS:
        return None
    model = raw.get("model")
    source_ip = raw.get("source_ip")
    route = raw.get("route")
    if not all(isinstance(value, str) and len(value) <= 256
               for value in (model, source_ip, route)):
        return None
    prompt = _count(raw.get("prompt_tokens"))
    completion = _count(raw.get("completion_tokens"))
    total = _count(raw.get("total_tokens"))
    duration_ms = _count(raw.get("duration_ms"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {"at": at, "listener": listener, "model": model or "Unknown model",
            "source_ip": source_ip or "Unknown IP", "route": route,
            "status": status, "prompt": prompt, "completion": completion,
            "total": total, "duration_ms": duration_ms}


def _files(directory, start, end):
    day = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    while day <= last:
        for port in PORTS:
            yield directory / f"ollama-{port}-{day.isoformat()}.jsonl"
        day += timedelta(days=1)


def _access_events(path, start_epoch, end_epoch):
    """Read a bounded tail of Ollama's existing Gin access log."""
    events = []
    with path.open("rb") as source:
        source.seek(0, 2)
        size = source.tell()
        offset = max(0, size - MAX_ACCESS_LOG_BYTES)
        source.seek(offset)
        if offset:
            source.readline()  # Drop the first partial line.
        for line in source:
            match = ACCESS_LINE.match(line.decode("utf-8", errors="replace").strip())
            if not match:
                continue
            day, clock, status, address, method, path_text = match.groups()
            route = path_text.split("?", 1)[0]
            if method != "POST" or route not in INFERENCE_ROUTES:
                continue
            try:
                # Gin prints the AI server's local time without a UTC offset.
                at = int(datetime.strptime(day + " " + clock,
                                           "%Y/%m/%d %H:%M:%S").timestamp())
            except ValueError:
                continue
            if start_epoch <= at < end_epoch:
                events.append({"at": at, "listener": "11434", "route": route,
                               "model": "Not recorded", "source_ip": address.strip(),
                               "status": int(status), "prompt": None,
                               "completion": None, "total": None,
                               "duration_ms": None})
    return events, offset > 0


def read_direct_usage(directory, start, end, view, amount, oldest, today,
                      access_logs=()):
    """Aggregate completed records in the selected local-time window.

    Missing files mean no observed records, not proof that no calls happened.
    Oversized or unreadable files make the reported period incomplete.
    """
    start_epoch, end_epoch = start.timestamp(), end.timestamp()
    events, errors, available_files, dropped_records = [], [], 0, 0
    jsonl_ports = set()
    access_log_files = 0
    access_log_truncated = False
    for path in _files(directory, start, end):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        except OSError:
            errors.append(f"Could not inspect {path.name}.")
            continue
        available_files += 1
        jsonl_ports.add(path.name.split("-")[1])
        if size > MAX_FILE_BYTES:
            errors.append(f"{path.name} exceeds the reading limit.")
            continue
        try:
            with path.open("rb") as source:
                for line_number, line in enumerate(source, 1):
                    if len(line) > MAX_LINE_BYTES:
                        errors.append(f"Oversized record in {path.name}:{line_number}.")
                        continue
                    if not line.endswith(b"\n"):
                        # The writer may still be appending its final line.
                        continue
                    try:
                        raw = json.loads(line)
                    except (UnicodeDecodeError, ValueError):
                        raw = None
                    if (isinstance(raw, dict) and raw.get("schema") == 1
                            and raw.get("kind") == "dropped"
                            and raw.get("listener") in PORTS
                            and _count(raw.get("at")) is not None
                            and _count(raw.get("dropped")) is not None):
                        if start_epoch <= raw["at"] < end_epoch:
                            dropped_records += raw["dropped"]
                        continue
                    event = _event(raw)
                    if event is None:
                        errors.append(f"Invalid record in {path.name}:{line_number}.")
                    elif (event["model"].startswith(PROBE_MODEL_PREFIX)
                          and event["route"] == "/api/generate"
                          and event["status"] == 404):
                        # Cutover probes remain on disk but are not user traffic.
                        continue
                    elif start_epoch <= event["at"] < end_epoch:
                        events.append(event)
        except OSError:
            errors.append(f"Could not read {path.name}.")

    # A recorder file takes priority for 11434, preventing double counting.
    # Existing access logs give real request metadata while no recorder runs.
    if "11434" not in jsonl_ports:
        for path in access_logs:
            try:
                if not path.is_file():
                    continue
                access_log_files += 1
                observed, truncated = _access_events(path, start_epoch, end_epoch)
                events.extend(observed)
                access_log_truncated |= truncated
            except OSError:
                errors.append(f"Could not read {path.name}.")

    events.sort(key=lambda row: row["at"], reverse=True)
    totals = {"requests": 0, "successes": 0, "failures": 0,
              "token_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "total_tokens": 0, "prompt_coverage": 0,
              "completion_coverage": 0, "total_coverage": 0}
    duration_values = []
    matrix = defaultdict(lambda: {"requests": 0, "http_4xx": 0,
                                  "http_5xx": 0, "duration_values": [],
                                  "prompt_tokens": 0, "completion_tokens": 0,
                                  "prompt_coverage": 0,
                                  "completion_coverage": 0})

    def empty_group():
        return {"requests": 0, "token_requests": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "prompt_coverage": 0, "completion_coverage": 0,
                "total_coverage": 0}

    models = defaultdict(empty_group)
    callers = defaultdict(empty_group)
    periods = defaultdict(empty_group)
    duration = (end - start).total_seconds()
    bucket_seconds = max(60, int((duration / 72 + 59) // 60) * 60)
    buckets = {}
    period_unit = ("day" if view == "week" else "hour"
                   if view in ("day", "hours") or (view == "minutes" and amount > 60)
                   else "minute")
    for event in events:
        event["caller_name"] = _caller_label(event)
        totals["requests"] += 1
        totals["successes" if event["status"] < 400 else "failures"] += 1
        metric = matrix[(event["listener"], event["model"],
                         event["caller_name"], event["route"])]
        metric["requests"] += 1
        if 400 <= event["status"] < 500:
            metric["http_4xx"] += 1
        elif event["status"] >= 500:
            metric["http_5xx"] += 1
        if event["duration_ms"] is not None:
            duration_values.append(event["duration_ms"])
            metric["duration_values"].append(event["duration_ms"])
        for token_key in ("prompt", "completion"):
            if event[token_key] is not None:
                metric[f"{token_key}_tokens"] += event[token_key]
                metric[f"{token_key}_coverage"] += 1
        local_at = datetime.fromtimestamp(event["at"], start.tzinfo)
        period = (local_at.strftime("%Y-%m-%d") if period_unit == "day"
                  else local_at.strftime("%Y-%m-%d %H:00") if period_unit == "hour"
                  else local_at.strftime("%Y-%m-%d %H:%M"))
        groups = (totals, models[event["model"]],
                  callers[(event["caller_name"], event["source_ip"])],
                  periods[(period, event["caller_name"],
                           event["source_ip"], event["model"])])
        for group in groups[1:]:
            group["requests"] += 1
        if any(event[key] is not None for key in ("prompt", "completion", "total")):
            for group in groups:
                group["token_requests"] += 1
        for key, target in (("prompt", "prompt_tokens"),
                            ("completion", "completion_tokens"),
                            ("total", "total_tokens")):
            value = event[key]
            if value is not None:
                for group in groups:
                    group[target] += value
                    group[f"{key}_coverage"] += 1
        bucket_at = int(start_epoch + ((event["at"] - start_epoch) // bucket_seconds) * bucket_seconds)
        bucket = buckets.setdefault(bucket_at, {"at": bucket_at, "requests": 0, "tokens": 0})
        bucket["requests"] += 1
        if event["total"] is not None:
            bucket["tokens"] += event["total"]

    for bucket_at in range(int(start_epoch), int(end_epoch), bucket_seconds):
        buckets.setdefault(bucket_at, {"at": bucket_at, "requests": 0, "tokens": 0})
    model_rows = [{"model": name, **values} for name, values in models.items()]
    caller_rows = [{"caller_name": label, "caller": ip, **values}
                   for (label, ip), values in callers.items()]
    detail_rows = [{"period": period, "caller_name": label,
                    "caller": ip, "model": model, **values}
                   for (period, label, ip, model), values in periods.items()]
    model_rows.sort(key=lambda row: (-row["requests"], row["model"]))
    caller_rows.sort(key=lambda row: (-row["requests"], row["caller"]))
    detail_rows.sort(key=lambda row: (row["period"], row["caller"], row["model"]), reverse=True)
    matrix_rows = []
    for (listener, model, caller_name, route), values in matrix.items():
        durations = values.pop("duration_values")
        matrix_rows.append({"listener": listener, "model": model,
                            "caller_name": caller_name, "route": route,
                            **values, **_duration_stats(durations)})
    matrix_rows.sort(key=lambda row: (-row["requests"], row["listener"],
                                      row["model"], row["caller_name"], row["route"]))
    metrics = {**_duration_stats(duration_values),
               "requests_per_minute": round(totals["requests"] / max(duration / 60, 1 / 60), 2),
               "http_4xx": sum(row["http_4xx"] for row in matrix_rows),
               "http_5xx": sum(row["http_5xx"] for row in matrix_rows)}
    return {"view": view, "start": start.isoformat(), "end": end.isoformat(),
            "oldest_day": oldest.isoformat(), "today": today.isoformat(),
            "totals": totals, "buckets": sorted(buckets.values(), key=lambda item: item["at"]),
            "models": model_rows, "callers": caller_rows, "periods": detail_rows,
            "metrics": metrics, "matrix": matrix_rows,
            "source_ip_count": len({event["source_ip"] for event in events}),
            "period_unit": period_unit, "recent": events[:100],
            "available_files": available_files + access_log_files,
            "access_log_files": access_log_files,
            "access_log_truncated": access_log_truncated,
            "complete": not errors and not dropped_records and not access_log_truncated
                        and not access_log_files,
            "dropped_records": dropped_records,
            "errors": errors[:10], "error_count": len(errors)}


DIRECT_USAGE_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ollama request usage</title><style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#e7eef5;background:#0b111a;color-scheme:dark}
*{box-sizing:border-box}body{margin:0}main{max-width:1450px;margin:auto;padding:30px 28px 60px}
h1{font-size:30px;margin:8px 0}h2{font-size:17px;margin:0 0 12px}.muted{color:#9bb0bf;font-size:13px;line-height:1.5}
.tabs{display:flex;gap:9px;flex-wrap:wrap;margin:21px 0}.tabs a{display:inline-block;padding:10px 16px;border:1px solid #365260;border-radius:9px;background:#142431;color:#d5e8ed;text-decoration:none;font-size:13px;font-weight:700}.tabs a.active{background:#1e5349;border-color:#58d5b3;color:#e8fff6}
.controls{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:24px 0}.controls label{display:grid;gap:6px;color:#a9bfca;font-size:12px;font-weight:700}
[hidden]{display:none!important}select,input,button{font:inherit;color:#e7eef5;background:#112432;border:1px solid #365260;border-radius:8px;padding:9px 11px;min-height:39px}
input[type=number]{width:95px}button{cursor:pointer}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #70e3c1;outline-offset:2px}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}.card,.panel{background:#12202d;border:1px solid #2a4350;border-radius:13px;padding:19px}
.label{color:#a5b9c5;font-size:12px}.value{font-size:29px;font-weight:750;margin-top:10px}.hint{font-size:11px;color:#92abb8;margin-top:7px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.panel{margin-top:14px}.grid .panel{margin-top:0}
.chart{height:205px;display:flex;align-items:end;gap:2px;border-bottom:1px solid #4a6270;margin-top:14px;overflow:hidden}
.bar{flex:1;min-width:2px;background:#55dcb7;border-radius:3px 3px 0 0}.tokens .bar{background:#70aef1}.axis{display:flex;justify-content:space-between;color:#93aab8;font-size:11px;margin-top:8px}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:11px 10px;border-bottom:1px solid #2a4050;font-size:12px}th{color:#a4b8c4}td{color:#d6e4e9}
.notice{border-left:3px solid #70aef1;padding:11px 13px;background:#172735;margin:15px 0;color:#b5c8d4;font-size:12px;line-height:1.5}
@media(max-width:950px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}}@media(max-width:520px){main{padding:20px 13px}.cards{grid-template-columns:1fr 1fr}.value{font-size:22px}}
</style></head><body><main>
<div class="muted">Ollama · Monitoring</div><h1>Ollama request usage</h1><div class="muted">Observed inference requests from the general 11434 access log or recorder files. Private 11435 requests require recorder files. Refreshed every 15 seconds. Caller names use the current listener, source IP and route mapping; unmatched traffic is Unidentified.</div>
<nav class="tabs" aria-label="Dashboard pages"><a id="feedback-link" href="#">Feedback data</a><a id="usage-link" href="#">Open WebUI usage</a><a class="active" aria-current="page" href="#">Ollama requests</a></nav>
<div class="notice" id="notice">Access logs provide time, route, status and source IP, but not model or tokens. Only the last 8 MiB of each access log is read. No prompts or answers are shown.</div>
<div class="controls"><label>Range<select id="mode"><option value="range">Last N minutes or hours</option><option value="day">Selected day</option><option value="week">Last 7 days</option></select></label><label id="amount-box">Amount<input id="amount" type="number" min="1" max="168" value="2"></label><label id="unit-box">Unit<select id="unit"><option value="hours">Hours</option><option value="minutes">Minutes</option></select></label><label id="day-box" hidden>Day<input id="day" type="date"></label><button id="refresh">Refresh</button><span id="updated" class="muted">Loading…</span></div>
<div class="cards"><div class="card"><div class="label">Observed requests</div><div class="value" id="requests">—</div><div class="hint">Completed records only</div></div><div class="card"><div class="label">HTTP failures</div><div class="value" id="failures">—</div><div class="hint">Status 400 or higher</div></div><div class="card"><div class="label">Recorded total tokens</div><div class="value" id="tokens">—</div><div class="hint" id="coverage">Only requests with counts</div></div><div class="card"><div class="label">Source IPs</div><div class="value" id="callers">—</div><div class="hint">A proxy may appear as localhost</div></div></div>
<section class="panel"><h2>Request performance</h2>
<p class="muted">Completed Ollama inference requests in the selected period. Latency measures request start through handler completion, including streaming responses. Older recorder rows have no latency reading.</p>
<div class="cards"><div><div class="label">Average latency</div><div class="value" id="latency-avg">--</div></div><div><div class="label">Median latency (p50)</div><div class="value" id="latency-p50">--</div></div><div><div class="label">95th percentile (p95)</div><div class="value" id="latency-p95">--</div></div><div><div class="label">Slowest request</div><div class="value" id="latency-max">--</div></div></div>
<p class="muted" id="latency-coverage"></p>
<div class="cards"><div><div class="label">Requests per minute</div><div class="value" id="request-rate">--</div></div><div><div class="label">HTTP 4xx</div><div class="value" id="http-4xx">--</div></div><div><div class="label">HTTP 5xx</div><div class="value" id="http-5xx">--</div></div><div><div class="label">Failure rate</div><div class="value" id="failure-rate">--</div></div></div></section>
<div class="grid"><section class="panel"><h2>Requests over time</h2><div class="chart" id="request-chart" role="img" aria-label="Direct requests over time"></div><div class="axis"><span id="request-start">—</span><span id="request-scale">—</span><span id="request-end">—</span></div></section><section class="panel tokens"><h2>Recorded tokens over time</h2><div class="chart" id="token-chart" role="img" aria-label="Recorded tokens over time"></div><div class="axis"><span id="token-start">—</span><span id="token-scale">—</span><span id="token-end">—</span></div></section></div>
<div class="grid"><section class="panel"><h2>Token detail</h2><div class="cards" style="grid-template-columns:repeat(2,1fr)"><div><div class="label">Prompt tokens</div><div class="value" id="prompt-tokens">—</div></div><div><div class="label">Completion tokens</div><div class="value" id="completion-tokens">—</div></div></div><p class="muted" id="token-note"></p></section><section class="panel"><h2>Requests by model</h2><div class="table-wrap"><table><thead><tr><th>Model</th><th>Requests</th><th>With totals</th><th>Input</th><th>Output</th><th>Total</th></tr></thead><tbody id="models"></tbody></table></div></section></div>
<section class="panel"><h2>Requests by caller and source IP</h2><p class="muted">Caller names are based on the current listener, source IP and route. Forwarded requests may show the local proxy address; unmatched traffic is Unidentified.</p><div class="table-wrap"><table><thead><tr><th>Caller</th><th>Source IP</th><th>Requests</th><th>With totals</th><th>Input</th><th>Output</th><th>Total</th></tr></thead><tbody id="sources"></tbody></table></div></section>
<section class="panel"><h2 id="period-title">Usage by time, caller, source IP and model</h2><div class="controls"><input id="search" type="search" placeholder="Filter by caller, IP or model" aria-label="Filter breakdown"><span class="muted" id="shown"></span></div><div class="table-wrap"><table><thead><tr><th>Period</th><th>Caller</th><th>Source IP</th><th>Model</th><th>Requests</th><th>With totals</th><th>Input</th><th>Output</th><th>Total</th></tr></thead><tbody id="breakdown"></tbody></table></div></section>
<section class="panel"><h2>Performance matrix</h2><p class="muted">All observed requests in the selected period, grouped by listener, model, caller and route. Percentiles use only recorded durations; token totals use only recorded counts.</p>
<div class="controls"><label>Search<input id="matrix-search" type="search" placeholder="Listener, model, caller or route"></label><label>Sort by<select id="matrix-sort"><option value="requests">Requests</option><option value="p95_ms">p95 latency</option><option value="p50_ms">p50 latency</option><option value="http_5xx">HTTP 5xx</option><option value="http_4xx">HTTP 4xx</option></select></label><label>Order<select id="matrix-order"><option value="desc">Descending</option><option value="asc">Ascending</option></select></label><span id="matrix-count" class="muted"></span></div>
<div class="table-wrap"><table><thead><tr><th>Listener</th><th>Model</th><th>Caller</th><th>Route</th><th>Requests</th><th>4xx</th><th>5xx</th><th>Failure rate</th><th>p50</th><th>p95</th><th>Latency coverage</th><th>Input tokens</th><th>Output tokens</th></tr></thead><tbody id="matrix"></tbody></table></div></section>
<section class="panel"><h2>Recent observed requests</h2><p class="muted">Filters and sorting apply to the newest 100 observed requests in the selected period. Metadata only; no prompts, answers or credentials.</p>
<div class="controls"><label>Search<input id="recent-search" type="search" placeholder="Search request metadata"></label><label>Listener<select id="recent-listener"><option value="">All listeners</option></select></label><label>Route<select id="recent-route"><option value="">All routes</option></select></label><label>Model<select id="recent-model"><option value="">All models</option></select></label><label>Caller<select id="recent-caller"><option value="">All callers</option></select></label><label>Source IP<select id="recent-ip"><option value="">All IPs</option></select></label><label>Status<select id="recent-status"><option value="">All statuses</option></select></label><label>Token count<select id="recent-coverage"><option value="">Any</option><option value="recorded">Recorded</option><option value="missing">Not recorded</option></select></label><label>Latency<select id="recent-latency-coverage"><option value="">Any</option><option value="recorded">Recorded</option><option value="missing">Not recorded</option></select></label><label>Minimum tokens<input id="recent-min" type="number" min="0" placeholder="Any"></label><label>Maximum tokens<input id="recent-max" type="number" min="0" placeholder="Any"></label><label>Minimum latency (ms)<input id="recent-latency-min" type="number" min="0" placeholder="Any"></label><label>Maximum latency (ms)<input id="recent-latency-max" type="number" min="0" placeholder="Any"></label><label>Sort by<select id="recent-sort"><option value="at">Time</option><option value="listener">Listener</option><option value="route">Route</option><option value="model">Model</option><option value="caller_name">Caller</option><option value="source_ip">Source IP</option><option value="status">Status</option><option value="total">Total tokens</option><option value="duration_ms">Latency</option></select></label><label>Order<select id="recent-order"><option value="desc">Descending</option><option value="asc">Ascending</option></select></label><button id="recent-reset" type="button">Clear filters</button><span id="recent-count" class="muted" aria-live="polite"></span></div>
<div class="table-wrap"><table><thead><tr><th>Time</th><th>Listener</th><th>Route</th><th>Model</th><th>Caller</th><th>Source IP</th><th>Status</th><th>Total tokens</th><th>Latency</th></tr></thead><tbody id="recent"></tbody></table></div></section>
</main><script>
const key=new URLSearchParams(location.search).get('key')||'';
const $=id=>document.getElementById(id),fmt=n=>Number(n).toLocaleString(),stamp=n=>new Date(n*1000).toLocaleString();
const node=(tag,value)=>{const item=document.createElement(tag);item.textContent=value;return item};
$('feedback-link').href='/?key='+encodeURIComponent(key);$('usage-link').href='/usage?key='+encodeURIComponent(key);
function controls(){const mode=$('mode').value,range=mode==='range';$('amount-box').hidden=!range;$('unit-box').hidden=!range;$('day-box').hidden=mode!=='day';$('amount').max=$('unit').value==='minutes'?10080:168;if(+$('amount').value>+$('amount').max)$('amount').value=$('amount').max}
function draw(rootId,points,field,start,end,scaleId,startId,endId){const root=$(rootId);root.replaceChildren();const max=Math.max(0,...points.map(x=>x[field]));for(const item of points){const bar=node('div','');bar.className='bar';bar.style.height=(item[field]===0?0:Math.max(2,item[field]/max*195))+'px';bar.title=stamp(item.at)+' · '+fmt(item[field]);root.append(bar)}$(scaleId).textContent='Peak '+fmt(max)+' / bucket';$(startId).textContent=new Date(start).toLocaleString();$(endId).textContent=new Date(end).toLocaleString()}
function rows(rootId,items,columns,emptyMessage='No observed records in this period.'){const root=$(rootId);root.replaceChildren();if(!items.length){const row=node('tr',''),cell=node('td',emptyMessage);cell.colSpan=columns.length;row.append(cell);root.append(row);return}for(const item of items){const row=node('tr','');for(const column of columns)row.append(node('td',column(item)));root.append(row)}}
const recorded=(value,count)=>count?fmt(value):'Not recorded';let busy=false,lastData=null;
const ms=value=>value===null?'Not recorded':fmt(value)+' ms';
const pct=(part,total)=>total?(100*part/total).toFixed(1)+'%':'0.0%';
function breakdown(){if(!lastData)return;const query=$('search').value.trim().toLowerCase();const matching=lastData.periods.filter(x=>!query||[x.caller_name,x.caller,x.model,x.period].join(' ').toLowerCase().includes(query));rows('breakdown',matching.slice(0,300),[x=>x.period,x=>x.caller_name,x=>x.caller,x=>x.model,x=>fmt(x.requests),x=>fmt(x.total_coverage)+' / '+fmt(x.requests),x=>recorded(x.prompt_tokens,x.prompt_coverage),x=>recorded(x.completion_tokens,x.completion_coverage),x=>recorded(x.total_tokens,x.total_coverage)]);$('shown').textContent='Showing '+fmt(Math.min(300,matching.length))+' of '+fmt(matching.length)+' matching groups'}
function renderMatrix(){
  if(!lastData)return;
  const query=$('matrix-search').value.trim().toLowerCase(),field=$('matrix-sort').value,direction=$('matrix-order').value==='asc'?1:-1;
  const matching=lastData.matrix.filter(x=>!query||[x.listener,x.model,x.caller_name,x.route].join(' ').toLowerCase().includes(query));
  matching.sort((a,b)=>{const av=a[field],bv=b[field];if(av===null||bv===null)return av===null&&bv===null?b.requests-a.requests:av===null?1:-1;return (av-bv)*direction||b.requests-a.requests});
  rows('matrix',matching.slice(0,300),[x=>x.listener,x=>x.model,x=>x.caller_name,x=>x.route,x=>fmt(x.requests),x=>fmt(x.http_4xx),x=>fmt(x.http_5xx),x=>pct(x.http_4xx+x.http_5xx,x.requests),x=>ms(x.p50_ms),x=>ms(x.p95_ms),x=>fmt(x.duration_coverage)+' / '+fmt(x.requests),x=>recorded(x.prompt_tokens,x.prompt_coverage),x=>recorded(x.completion_tokens,x.completion_coverage)]);
  $('matrix-count').textContent='Showing '+fmt(Math.min(300,matching.length))+' of '+fmt(matching.length)+' groups';
}
function recentOptions(id,field,label){const select=$(id),current=select.value;select.replaceChildren(new Option('All '+label,''));for(const value of [...new Set(lastData.recent.map(x=>String(x[field])))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true,sensitivity:'base'})))select.add(new Option(value,value));if(current&&![...select.options].some(option=>option.value===current))select.add(new Option(current,current));select.value=current}
function renderRecent(){
  if(!lastData)return;
  const query=$('recent-search').value.trim().toLowerCase(),coverage=$('recent-coverage').value,latencyCoverage=$('recent-latency-coverage').value;
  const min=$('recent-min').value===''?null:Number($('recent-min').value),max=$('recent-max').value===''?null:Number($('recent-max').value);
  const latencyMin=$('recent-latency-min').value===''?null:Number($('recent-latency-min').value),latencyMax=$('recent-latency-max').value===''?null:Number($('recent-latency-max').value);
  const selections=[['recent-listener','listener'],['recent-route','route'],['recent-model','model'],['recent-caller','caller_name'],['recent-ip','source_ip'],['recent-status','status']];
  const field=$('recent-sort').value,direction=$('recent-order').value==='asc'?1:-1;
  const matching=lastData.recent.filter(x=>(!query||[stamp(x.at),x.listener,x.route,x.model,x.caller_name,x.source_ip,x.status,x.total,x.duration_ms].join(' ').toLowerCase().includes(query))&&selections.every(([id,name])=>!$(id).value||String(x[name])===$(id).value)&&(!coverage||(coverage==='recorded'?x.total!==null:x.total===null))&&(!latencyCoverage||(latencyCoverage==='recorded'?x.duration_ms!==null:x.duration_ms===null))&&(min===null||x.total!==null&&x.total>=min)&&(max===null||x.total!==null&&x.total<=max)&&(latencyMin===null||x.duration_ms!==null&&x.duration_ms>=latencyMin)&&(latencyMax===null||x.duration_ms!==null&&x.duration_ms<=latencyMax));
  matching.sort((a,b)=>{const av=a[field],bv=b[field];if(av===null||bv===null)return av===null&&bv===null?b.at-a.at:av===null?1:-1;const comparison=typeof av==='number'?av-bv:String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'});return comparison*direction||b.at-a.at});
  rows('recent',matching,[x=>stamp(x.at),x=>x.listener,x=>x.route,x=>x.model,x=>x.caller_name,x=>x.source_ip,x=>String(x.status),x=>x.total===null?'Not recorded':fmt(x.total),x=>ms(x.duration_ms)],'No requests match these filters.');
  $('recent-count').textContent='Showing '+fmt(matching.length)+' of '+fmt(lastData.recent.length)+' recent requests';
}
async function load(){if(busy)return;busy=true;try{const params=new URLSearchParams(),mode=$('mode').value;params.set('view',mode==='range'?$('unit').value:mode);if(mode==='range')params.set('amount',$('amount').value);if(mode==='day')params.set('date',$('day').value);const response=await fetch('/api/direct-usage?'+params,{headers:{'X-Local-Token':key},cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'Usage request failed');lastData=data;$('day').min=data.oldest_day;$('day').max=data.today;if(!$('day').value)$('day').value=data.today;const t=data.totals;$('requests').textContent=fmt(t.requests);$('failures').textContent=fmt(t.failures);$('tokens').textContent=recorded(t.total_tokens,t.total_coverage);$('coverage').textContent=fmt(t.total_coverage)+' of '+fmt(t.requests)+' requests have total-token counts';$('callers').textContent=fmt(data.source_ip_count);$('prompt-tokens').textContent=recorded(t.prompt_tokens,t.prompt_coverage);$('completion-tokens').textContent=recorded(t.completion_tokens,t.completion_coverage);$('token-note').textContent=fmt(t.token_requests)+' requests have some token usage; input and output totals may cover different requests.';const m=data.metrics;
for(const [id,field] of [['latency-avg','avg_ms'],['latency-p50','p50_ms'],['latency-p95','p95_ms'],['latency-max','max_ms']])$(id).textContent=ms(m[field]);
$('latency-coverage').textContent=fmt(m.duration_coverage)+' of '+fmt(t.requests)+' requests have latency readings';
$('request-rate').textContent=fmt(m.requests_per_minute);
$('http-4xx').textContent=fmt(m.http_4xx);
$('http-5xx').textContent=fmt(m.http_5xx);
$('failure-rate').textContent=pct(m.http_4xx+m.http_5xx,t.requests);
const issues=[];if(data.dropped_records)issues.push(fmt(data.dropped_records)+' requests dropped by the recorder.');if(data.error_count)issues.push(data.error_count+' unreadable or invalid records. '+data.errors.join(' '));$('notice').textContent=issues.length?'Collection incomplete: '+issues.join(' '):data.available_files===0?'No recorder files were found for this period. Zero observed requests does not mean zero actual requests.':'Counts include observed requests reaching Ollama, including Open WebUI or proxy traffic. A file does not prove every request was captured.';draw('request-chart',data.buckets,'requests',data.start,data.end,'request-scale','request-start','request-end');draw('token-chart',data.buckets,'tokens',data.start,data.end,'token-scale','token-start','token-end');const cols=[x=>fmt(x.requests),x=>fmt(x.total_coverage)+' / '+fmt(x.requests),x=>recorded(x.prompt_tokens,x.prompt_coverage),x=>recorded(x.completion_tokens,x.completion_coverage),x=>recorded(x.total_tokens,x.total_coverage)];rows('models',data.models,[x=>x.model,...cols]);rows('sources',data.callers,[x=>x.caller_name,x=>x.caller,...cols]);$('period-title').textContent='Usage by '+data.period_unit+', caller, source IP and model';breakdown();renderMatrix();$('updated').textContent='Updated '+new Date().toLocaleTimeString()}catch(error){$('updated').textContent='Could not load: '+error.message}finally{busy=false}}
async function refresh(){await load();if(lastData){for(const [id,field,label] of [['recent-listener','listener','listeners'],['recent-route','route','routes'],['recent-model','model','models'],['recent-caller','caller_name','callers'],['recent-ip','source_ip','IPs'],['recent-status','status','statuses']])recentOptions(id,field,label);renderRecent()}if(lastData&&lastData.access_log_files){$('notice').textContent+=' General 11434 access logs show real requests but do not record model or token counts. Private 11435 requests are absent unless recorder files exist. Only the last 8 MiB of each access log is read.';if(lastData.access_log_truncated)$('notice').textContent+=' Older access log entries are outside the read limit.'}}
for(const id of ['mode','unit','amount','day'])$(id).addEventListener('change',()=>{controls();if($('mode').value!=='day'||$('day').value)refresh()});$('refresh').onclick=refresh;$('search').addEventListener('input',breakdown);
for(const id of ['matrix-search'])$(id).addEventListener('input',renderMatrix);
for(const id of ['matrix-sort','matrix-order'])$(id).addEventListener('change',renderMatrix);
for(const id of ['recent-search','recent-min','recent-max','recent-latency-min','recent-latency-max'])$(id).addEventListener('input',renderRecent);
for(const id of ['recent-listener','recent-route','recent-model','recent-caller','recent-ip','recent-status','recent-coverage','recent-latency-coverage','recent-sort','recent-order'])$(id).addEventListener('change',renderRecent);
$('recent-reset').onclick=()=>{for(const id of ['recent-search','recent-listener','recent-route','recent-model','recent-caller','recent-ip','recent-status','recent-coverage','recent-latency-coverage','recent-min','recent-max','recent-latency-min','recent-latency-max'])$(id).value='';$('recent-sort').value='at';$('recent-order').value='desc';renderRecent()};
controls();refresh();setInterval(refresh,15000);
</script></body></html>'''
