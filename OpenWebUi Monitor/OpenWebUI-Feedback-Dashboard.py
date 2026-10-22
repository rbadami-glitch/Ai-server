"""Local, read-only, auto-refreshing Open WebUI feedback dashboard.

Run on the Open WebUI server with Python 3:
    python -B OpenWebUI-Feedback-Dashboard.py

Only this computer can open the page (127.0.0.1). A fresh random access key is
required for every run. No API key, package installation, or external service is
used. The Open WebUI database is opened read-only; this script never edits it.
"""

import argparse
import json
import secrets
import sqlite3
import sys
import webbrowser
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_DATABASE = Path(r"C:\downloads\open-webui\data\webui.db")
HOST = "127.0.0.1"
DEFAULT_PORT = 8789
MAX_PREVIEW = 20000


def parse_json(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def text_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for part in (text_value(item) for item in value) if part)
    if isinstance(value, dict):
        if value.get("type") == "text" and "text" in value:
            return text_value(value["text"])
        for key in ("content", "text", "message", "value"):
            if key in value:
                return text_value(value[key])
    return ""


def epoch(value):
    try:
        result = int(value or 0)
        return result // 1000 if result > 10**12 else result
    except (TypeError, ValueError):
        return 0


def table_columns(db, name):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{name}")')}


def db_connection(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=4)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def read_feedback(path):
    with closing(db_connection(path)) as db:
        required = {"id", "user_id", "data", "meta", "created_at", "updated_at"}
        if not required <= table_columns(db, "feedback"):
            raise RuntimeError("This Open WebUI database has no supported feedback table.")

        model_names = {}
        if {"id", "name"} <= table_columns(db, "model"):
            model_names = {row["id"]: row["name"] for row in
                           db.execute("SELECT id, name FROM model")}
        user_names = {}
        if {"id", "name"} <= table_columns(db, "user"):
            user_names = {row["id"]: row["name"] for row in
                          db.execute('SELECT id, name FROM "user"')}

        records = []
        for row in db.execute(
            "SELECT id, user_id, data, meta, created_at, updated_at "
            "FROM feedback WHERE type = 'rating' ORDER BY created_at DESC"
        ):
            data = parse_json(row["data"])
            meta = parse_json(row["meta"])
            if not isinstance(data, dict):
                continue
            if not isinstance(meta, dict):
                meta = {}
            try:
                rating = int(data.get("rating"))
            except (TypeError, ValueError):
                continue
            if rating not in (-1, 1):
                continue
            model_id = str(data.get("model_id") or meta.get("model_id") or "unknown")
            tags = data.get("tags") or meta.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            extra_details = data.get("details")
            if isinstance(extra_details, (dict, list)):
                extra_details = json.dumps(extra_details, ensure_ascii=False)
            siblings = data.get("sibling_model_ids") or []
            if not isinstance(siblings, list):
                siblings = []
            base_models = meta.get("base_models") or []
            if isinstance(base_models, dict):
                base_models = list(base_models)
            if not isinstance(base_models, list):
                base_models = []
            records.append({
                "id": row["id"],
                "rating": rating,
                "model_id": model_id,
                "model_name": model_names.get(model_id) or model_id,
                "user": user_names.get(row["user_id"]) or "Unknown user",
                "user_id": str(row["user_id"] or ""),
                "reason": text_value(data.get("reason"))[:1000],
                "comment": text_value(data.get("comment"))[:2000],
                "details": text_value(extra_details)[:3000],
                "tags": [str(tag)[:80] for tag in tags[:20]],
                "sibling_models": [str(item)[:200] for item in siblings[:12]],
                "base_models": [str(item)[:200] for item in base_models[:12]],
                "chat_id": str(meta.get("chat_id") or ""),
                "message_index": meta.get("message_index"),
                "created_at": epoch(row["created_at"]),
                "updated_at": epoch(row["updated_at"]),
            })
        counts = Counter(item["rating"] for item in records)
        return {
            "items": records,
            "total": len(records),
            "positive": counts[1],
            "negative": counts[-1],
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }


def snapshot_messages(snapshot, message_id):
    """Return the rated answer, its user parent, and saved message metadata."""
    chat = snapshot.get("chat") if isinstance(snapshot, dict) else None
    if not isinstance(chat, dict):
        return {}
    inner = chat.get("chat", chat)
    if not isinstance(inner, dict):
        return {}
    history = inner.get("history") or {}
    messages = history.get("messages") if isinstance(history, dict) else None
    if not isinstance(messages, dict):
        return {}
    answer = messages.get(message_id)
    if not isinstance(answer, dict):
        return {}
    answer_text = text_value(answer.get("content"))
    parent_id = answer.get("parentId") or answer.get("parent_id")
    question_text = ""
    seen = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = messages.get(parent_id)
        if not isinstance(parent, dict):
            break
        if parent.get("role") == "user":
            question_text = text_value(parent.get("content"))
            break
        parent_id = parent.get("parentId") or parent.get("parent_id")
    return {"question": question_text[:MAX_PREVIEW],
            "answer": answer_text[:MAX_PREVIEW], "message": answer,
            "chat_title": text_value(inner.get("title"))[:300]}


def http_status(message):
    """Accept only an explicitly saved HTTP status, never infer one from text."""
    for source in (message, message.get("error")):
        if not isinstance(source, dict):
            continue
        for key in ("status_code", "statusCode", "http_status", "httpStatus"):
            value = source.get(key)
            try:
                code = int(value)
            except (TypeError, ValueError):
                continue
            if 100 <= code <= 599:
                return code
    return None


def message_details(message):
    if not isinstance(message, dict):
        message = {}
    error = message.get("error")
    error_code = (error.get("code") or error.get("status")) if isinstance(error, dict) else None
    if error:
        state = "Error"
    elif message.get("done") in (True, 1):
        state = "Completed"
    elif message.get("done") in (False, 0):
        state = "Unfinished"
    else:
        state = "Not recorded"
    info = message.get("info")
    usage = message.get("usage") or (info.get("usage") if isinstance(info, dict) else None)
    if not isinstance(usage, dict):
        usage = {}
    history = message.get("status_history") or message.get("statusHistory") or []
    if not isinstance(history, list):
        history = []
    return {
        "response_state": state,
        "http_status": http_status(message),
        "error": (text_value(error) or
                  (json.dumps(error, ensure_ascii=False) if error else ""))[:2000],
        "error_code": str(error_code)[:100] if error_code is not None else "",
        "usage": {str(k)[:80]: v for k, v in usage.items()
                  if isinstance(v, (int, float, str, bool))},
        "status_history": [str(item)[:300] for item in history[-8:]],
        "message_created_at": epoch(message.get("timestamp") or message.get("created_at")),
        "message_updated_at": epoch(message.get("updated_at")),
        "message_model": str(message.get("model") or message.get("model_id") or "")[:300],
        "source_count": len(message.get("sources") or []) if isinstance(message.get("sources"), list) else 0,
        "file_count": len(message.get("files") or []) if isinstance(message.get("files"), list) else 0,
        "embed_count": len(message.get("embeds") or []) if isinstance(message.get("embeds"), list) else 0,
        "followup_count": len(message.get("followUps") or []) if isinstance(message.get("followUps"), list) else 0,
        "has_output": bool(message.get("output")),
        "has_annotation": bool(message.get("annotation")),
    }


def read_detail(path, feedback_id):
    with closing(db_connection(path)) as db:
        feedback_columns = table_columns(db, "feedback")
        if "snapshot" not in feedback_columns:
            return {"question": "", "answer": "", "note": "This version has no saved chat snapshot.",
                    **message_details({})}
        row = db.execute(
            "SELECT meta, snapshot FROM feedback WHERE id = ? AND type = 'rating'",
            (feedback_id,),
        ).fetchone()
        if row is None:
            return None
        meta = parse_json(row["meta"])
        snapshot = parse_json(row["snapshot"])
        message_id = str(meta.get("message_id") or "") if isinstance(meta, dict) else ""
        saved = snapshot_messages(snapshot, message_id)
        question, answer = saved.get("question", ""), saved.get("answer", "")
        message = saved.get("message", {})
        chat_id = str(meta.get("chat_id") or "") if isinstance(meta, dict) else ""
        chat_title = saved.get("chat_title", "")
        chat_columns = table_columns(db, "chat")
        if chat_id and {"id", "chat"} <= chat_columns:
            title_select = ", title" if "title" in chat_columns else ""
            chat_row = db.execute(f"SELECT chat{title_select} FROM chat WHERE id = ?", (chat_id,)).fetchone()
            if chat_row:
                if "title" in chat_columns:
                    chat_title = text_value(chat_row["title"])[:300] or chat_title
                chat_data = parse_json(chat_row["chat"])
                if isinstance(chat_data, dict):
                    chat_title = chat_title or text_value(chat_data.get("title"))[:300]
                    live = snapshot_messages({"chat": {"chat": chat_data}}, message_id)
                    if live.get("message"):
                        message = {**live["message"], **message}
                        question = live.get("question") or question
                        answer = answer or live.get("answer", "")
        if chat_id and message_id and {"chat_id", "id"} <= table_columns(db, "chat_message"):
            columns = table_columns(db, "chat_message")
            selected = [key for key in ("content", "model_id", "done", "error", "usage",
                                      "status_history", "created_at", "updated_at") if key in columns]
            if selected:
                column_sql = ", ".join('"' + key + '"' for key in selected)
                row_message = db.execute(
                    f"SELECT {column_sql} FROM chat_message WHERE chat_id = ? AND "
                    "(id = ? OR id = ?) LIMIT 1", (chat_id, message_id, chat_id + "-" + message_id)
                ).fetchone()
                if row_message:
                    normalized = dict(row_message)
                    for key in ("content", "error", "usage", "status_history"):
                        if isinstance(normalized.get(key), str):
                            try:
                                normalized[key] = json.loads(normalized[key])
                            except ValueError:
                                pass
                    message = {**message, **{k: v for k, v in normalized.items()
                                           if v is not None and
                                           (k not in ("error", "usage", "status_history")
                                            or v not in ("", [], {}))}}
                    answer = answer or text_value(normalized.get("content"))[:MAX_PREVIEW]
        return {
            "question": question,
            "answer": answer,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "message_id": message_id,
            **message_details(message),
            "note": "" if question or answer else
                    "The question and answer were not available in this feedback snapshot.",
        }


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open WebUI Feedback Observatory</title>
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#e7eef5;background:#0b111a;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 83% 2%,#16313d 0,transparent 31%),#0b111a}
button,input,select{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #5ee2be;outline-offset:2px}
.shell{display:grid;grid-template-columns:238px minmax(0,1fr);min-height:100vh}.rail{padding:28px 20px;border-right:1px solid #263441;background:#0d1721}
.brand{display:flex;align-items:center;gap:11px;font-size:16px;font-weight:750;letter-spacing:.01em}.mark{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:#48d5af;color:#082018;font-weight:900}
.rail-note{margin:38px 6px 8px;color:#6e8598;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.14em}.nav{padding:11px 13px;border-radius:9px;background:#18332f;color:#83f0d3;font-weight:650;font-size:13px}
.privacy{margin-top:48px;padding:14px;background:#14212c;border:1px solid #29404b;border-radius:12px;font-size:12px;line-height:1.5;color:#a9c0cd}.privacy strong{display:block;color:#e3f3f4;margin-bottom:4px}
main{max-width:1510px;width:100%;margin:0 auto;padding:29px 34px 60px}.top{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.eyebrow{color:#52d7b4;font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.16em}
h1{font-size:31px;margin:8px 0 6px;letter-spacing:-.035em}h2{font-size:16px;margin:0;letter-spacing:-.01em}.sub{color:#8da3b3;font-size:13px;line-height:1.5}.live{display:flex;align-items:center;gap:7px;color:#a9c7cf;font-size:12px;white-space:nowrap}.dot{width:8px;height:8px;border-radius:50%;background:#4ddbaf;box-shadow:0 0 0 4px #4ddbaf20}.top-right{text-align:right}.refresh{margin-top:10px;color:#cbe6e5;border:1px solid #35515a;background:#172b35;padding:8px 12px;border-radius:8px}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:28px}.card,.panel{border:1px solid #273743;background:#121e29;border-radius:13px;box-shadow:0 14px 35px #00000012}.card{padding:19px}.label{font-size:12px;color:#8da4b1}.value{font-size:31px;font-weight:750;letter-spacing:-.04em;margin-top:10px}.hint{font-size:11px;color:#7f96a2;margin-top:8px}.good{color:#63ddb6}.bad{color:#ff8e86}.neutral{color:#b6c5d2}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(270px,.9fr);gap:14px;margin-top:14px}.panel{padding:20px}.panel-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:16px}.muted{color:#7f98a8;font-size:12px}.bars{height:196px;display:flex;align-items:end;gap:8px;border-bottom:1px solid #36505b;padding:0 4px 0}.day{height:100%;flex:1;min-width:0;display:flex;align-items:end;gap:2px;position:relative}.day .up,.day .down{width:50%;min-height:2px;border-radius:4px 4px 0 0}.day .up{background:#4bcda8}.day .down{background:#fa817d}.day:hover::after{content:attr(data-tip);position:absolute;bottom:100%;left:50%;transform:translateX(-50%);white-space:nowrap;background:#233645;padding:5px 7px;border-radius:5px;font-size:10px;color:#fff;z-index:3}.chart-labels{display:flex;justify-content:space-between;color:#78909e;font-size:10px;margin-top:8px}.legend{display:flex;gap:14px;margin-top:14px;color:#93a9b8;font-size:11px}.legend i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}.model{display:grid;grid-template-columns:minmax(0,1fr) 90px 36px;align-items:center;gap:10px;margin:13px 0;font-size:12px}.model-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stack{height:8px;background:#263743;border-radius:6px;overflow:hidden;display:flex}.stack .up{background:#4bcda8}.stack .down{background:#fa817d}.model-count{text-align:right;color:#a9bdc9}
.feed{margin-top:14px}.controls{display:flex;gap:9px;flex-wrap:wrap;margin:18px 0}.controls input,.controls select{background:#0f1922;border:1px solid #344550;border-radius:8px;color:#dbe9ed;padding:9px 11px;min-height:38px}.controls input{flex:1 1 260px}.controls select{flex:0 1 180px}.table-wrap{overflow:auto}.table{width:100%;border-collapse:collapse;text-align:left}.table th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#7f97a6;border-bottom:1px solid #31434e;padding:12px 10px}.table td{font-size:12px;color:#b3c5d0;padding:13px 10px;border-bottom:1px solid #263742;vertical-align:top}.table tr:last-child td{border-bottom:0}.table tbody tr{cursor:pointer}.table tbody tr:hover{background:#192b36}.name{color:#e6f1f2!important;font-weight:650}.pill{display:inline-block;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:700;white-space:nowrap}.pill.up{color:#73e7bf;background:#1a483c}.pill.down{color:#ffa49f;background:#4a282e}.truncate{max-width:330px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.empty{padding:34px 10px;color:#90a6b2;text-align:center;font-size:13px}.status{margin-top:12px;color:#90a7b3;font-size:11px}
.overlay{position:fixed;inset:0;background:#020a10c7;display:none;justify-content:flex-end;z-index:20}.overlay.open{display:flex}.drawer{width:min(650px,100%);height:100%;overflow:auto;background:#101c27;border-left:1px solid #36505e;padding:28px 28px 45px;box-shadow:-25px 0 55px #0006}.drawer-top{display:flex;justify-content:space-between;gap:15px}.close{background:#1e323d;border:1px solid #37515c;color:#dce9ed;border-radius:8px;padding:6px 11px}.detail-meta{margin:16px 0 24px;display:flex;gap:8px;flex-wrap:wrap}.detail-meta span{font-size:11px;color:#a8c2ce;background:#1b303b;border-radius:6px;padding:6px 9px}.section-label{font-size:11px;color:#64d5b6;text-transform:uppercase;letter-spacing:.12em;font-weight:750;margin:20px 0 8px}.text-box{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1721;border:1px solid #293e4b;border-radius:9px;padding:13px;color:#d6e7ea;line-height:1.55;font-size:13px;max-height:360px;overflow:auto}.warning{margin-top:18px;color:#8ba4af;font-size:11px;line-height:1.5}
.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.fact{background:#0b1721;border:1px solid #293e4b;border-radius:8px;padding:10px;min-width:0}.fact-label{display:block;color:#83a3b0;font-size:11px;margin-bottom:5px}.fact-value{display:block;color:#e0edf0;font-size:12px;overflow-wrap:anywhere}.fact-wide{grid-column:1/-1}
@media(max-width:1040px){.shell{grid-template-columns:1fr}.rail{display:none}main{padding:22px 18px 45px}.cards{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){.grid{grid-template-columns:1fr}.cards{gap:8px}.card{padding:13px}.value{font-size:25px}.top{align-items:flex-start}h1{font-size:25px}.top-right{font-size:11px}.table th:nth-child(4),.table td:nth-child(4){display:none}}
</style></head>
<body><div class="shell"><aside class="rail"><div class="brand"><span class="mark">↗</span><span>Feedback<br>Observatory</span></div><div class="rail-note">Workspace</div><div class="nav">◉ &nbsp;Overview</div><div class="privacy"><strong>Private by design</strong>Reads the local Open WebUI database. No cloud service, model call, or database write.</div></aside>
<main><div class="top"><div><div class="eyebrow">Open WebUI · Quality monitoring</div><h1>Feedback overview</h1><div class="sub">Live ratings and a review queue for model responses.</div></div><div class="top-right"><div class="live"><span class="dot"></span><span id="live">Connecting…</span></div><button class="refresh" id="refresh">↻ &nbsp; Refresh</button></div></div>
<div class="cards"><div class="card"><div class="label">Rated responses</div><div class="value neutral" id="total">—</div><div class="hint">Only responses with a thumb rating</div></div><div class="card"><div class="label">Thumbs up</div><div class="value good" id="up">—</div><div class="hint" id="upHint">Positive ratings</div></div><div class="card"><div class="label">Thumbs down</div><div class="value bad" id="down">—</div><div class="hint" id="downHint">Needs investigation</div></div><div class="card"><div class="label">Downvote share</div><div class="value" id="share">—</div><div class="hint">Downvotes ÷ rated responses, not all chats</div></div></div>
<div class="grid"><section class="panel"><div class="panel-head"><h2>Rating trend</h2><span class="muted">Last 14 days</span></div><div class="bars" id="chart"></div><div class="chart-labels"><span id="dateStart"></span><span id="dateEnd"></span></div><div class="legend"><span><i style="background:#4bcda8"></i>Thumbs up</span><span><i style="background:#fa817d"></i>Thumbs down</span></div></section><section class="panel"><div class="panel-head"><h2>Models with feedback</h2><span class="muted">By rated responses</span></div><div id="models"></div></section></div>
<section class="panel feed"><div class="panel-head"><div><h2>Feedback review queue</h2><div class="sub">Click a row to inspect its saved question and answer, when available.</div></div><span class="muted" id="shown"></span></div><div class="controls"><input id="search" type="search" placeholder="Search model, user, reason or comment" aria-label="Search feedback"><select id="modelFilter" aria-label="Model"><option value="">All models</option></select><select id="ratingFilter" aria-label="Rating"><option value="">All ratings</option><option value="-1">Thumbs down</option><option value="1">Thumbs up</option></select><select id="daysFilter" aria-label="Period"><option value="30">Last 30 days</option><option value="7">Last 7 days</option><option value="0">All time</option></select></div><div class="table-wrap"><table class="table"><thead><tr><th>Rating</th><th>Model</th><th>Reason / comment</th><th>User</th><th>Date</th></tr></thead><tbody id="rows"></tbody></table></div><div class="empty" id="empty" hidden>No feedback matches these filters.</div><div class="status" id="status"></div></section></main></div>
<div class="overlay" id="overlay" role="dialog" aria-modal="true" aria-label="Feedback details"><div class="drawer"><div class="drawer-top"><div><div class="eyebrow">Feedback details</div><h1 id="detailTitle">Response</h1></div><button class="close" id="close" aria-label="Close details">✕</button></div><div class="detail-meta" id="detailMeta"></div><div class="section-label">Saved chat details</div><div class="facts" id="facts"></div><div class="section-label">User question</div><div class="text-box" id="question">Loading…</div><div class="section-label">Model answer</div><div class="text-box" id="answer">Loading…</div><div class="section-label">User reason / comment</div><div class="text-box" id="reason">—</div><div class="section-label">Other feedback details</div><div class="text-box" id="extraDetails">—</div><div class="section-label">Saved error / status events</div><div class="text-box" id="errorInfo">—</div><div class="warning">Your database has no dedicated HTTP-status column, and the audit found no explicit HTTP-status field in rated snapshots. If an individual message saved a code inside an error, it appears above; otherwise it is not recorded. A completed answer does not prove HTTP 200. This read-only view includes rated responses only. Do not share company chat snapshots or screenshots without approval.</div></div></div>
<script>
const key=new URLSearchParams(location.search).get('key')||'';
let all=[],lastFocus=null;
const $=id=>document.getElementById(id);
const fmt=n=>Number(n).toLocaleString();
const dayKey=t=>{const d=new Date(t*1000);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const formatDate=t=>t?new Date(t*1000).toLocaleString():'Unknown';
const api=async path=>{const r=await fetch(path,{headers:{'X-Local-Token':key},cache:'no-store'});const v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v};
function el(name,cls,content){const n=document.createElement(name);if(cls)n.className=cls;if(content!==undefined)n.textContent=content;return n}
function filtered(){const q=$('search').value.trim().toLowerCase(),m=$('modelFilter').value,r=$('ratingFilter').value,days=+$('daysFilter').value,cut=days?Date.now()/1000-days*86400:0;return all.filter(x=>(!m||x.model_id===m)&&(!r||x.rating===+r)&&(!cut||x.created_at>=cut)&&(!q||[x.model_name,x.model_id,x.user,x.user_id,x.chat_id,x.reason,x.comment,x.details,...x.tags].join(' ').toLowerCase().includes(q)))}
function render(){const list=filtered(),up=list.filter(x=>x.rating===1).length,down=list.length-up;$('total').textContent=fmt(list.length);$('up').textContent=fmt(up);$('down').textContent=fmt(down);$('share').textContent=list.length?((down/list.length)*100).toFixed(1)+'%':'—';$('upHint').textContent='Positive ratings';$('downHint').textContent='Needs investigation';$('shown').textContent=fmt(list.length)+' matching ratings';renderChart(list);renderModels(list);renderRows(list)}
function renderChart(list){const now=new Date();now.setHours(0,0,0,0);const ds=[];for(let i=13;i>=0;i--){const d=new Date(now);d.setDate(d.getDate()-i);ds.push(dayKey(d.getTime()/1000))}const counts=Object.fromEntries(ds.map(d=>[d,{up:0,down:0}]));for(const x of list){const c=counts[dayKey(x.created_at)];if(c)c[x.rating===1?'up':'down']++}const max=Math.max(1,...Object.values(counts).map(c=>c.up+c.down));const root=$('chart');root.replaceChildren();for(const d of ds){const c=counts[d],wrap=el('div','day');wrap.dataset.tip=`${d}: ${c.up} up, ${c.down} down`;const u=el('div','up'),dn=el('div','down');u.style.height=Math.max(2,c.up/max*180)+'px';dn.style.height=Math.max(2,c.down/max*180)+'px';wrap.append(u,dn);root.append(wrap)}$('dateStart').textContent=ds[0];$('dateEnd').textContent=ds.at(-1)}
function renderModels(list){const by=new Map();for(const x of list){const k=x.model_id,v=by.get(k)||{name:x.model_name,up:0,down:0};v[x.rating===1?'up':'down']++;by.set(k,v)}const entries=[...by.values()].sort((a,b)=>(b.up+b.down)-(a.up+a.down)).slice(0,8),root=$('models');root.replaceChildren();if(!entries.length){root.append(el('div','empty','No ratings in this view.'));return}for(const x of entries){const row=el('div','model'),name=el('div','model-name',x.name),bar=el('div','stack'),count=el('div','model-count',fmt(x.up+x.down)),u=el('div','up'),d=el('div','down');u.style.width=(x.up/(x.up+x.down)*100)+'%';d.style.width=(x.down/(x.up+x.down)*100)+'%';bar.append(u,d);row.append(name,bar,count);root.append(row)}}
function renderRows(list){const root=$('rows');root.replaceChildren();$('empty').hidden=!!list.length;for(const x of list.slice(0,300)){const tr=el('tr');const rating=el('td');rating.append(el('span','pill '+(x.rating===1?'up':'down'),x.rating===1?'↑ Helpful':'↓ Unhelpful'));const model=el('td','name',x.model_name),reason=el('td','truncate',x.comment||x.reason||x.details||x.tags.join(', ')||'No reason supplied'),user=el('td','',x.user),date=el('td','',formatDate(x.created_at));tr.append(rating,model,reason,user,date);tr.tabIndex=0;tr.setAttribute('role','button');tr.setAttribute('aria-label','Open feedback for '+x.model_name);tr.onclick=()=>showDetail(x);tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showDetail(x)}};root.append(tr)}$('status').textContent=list.length>300?`Showing newest 300 of ${fmt(list.length)} matching ratings. Narrow the filters to see older items.`:'Refreshes automatically every 15 seconds. Ratings are stored by Open WebUI; this dashboard is read-only.'}
function addFact(label,value,wide=false){const box=el('div','fact'+(wide?' fact-wide':'')),caption=el('span','fact-label',label),body=el('span','fact-value',value===undefined||value===null||value===''?'Not recorded':String(value));box.append(caption,body);$('facts').append(box)}
async function showDetail(x){lastFocus=document.activeElement;$('detailTitle').textContent=x.model_name;$('detailMeta').replaceChildren();for(const item of [x.rating===1?'Thumbs up':'Thumbs down',x.user,formatDate(x.created_at),'Feedback '+x.id])$('detailMeta').append(el('span','',item));$('reason').textContent=[x.reason,x.comment,x.tags.length?'Tags: '+x.tags.join(', '):''].filter(Boolean).join('\n\n')||'No reason supplied';$('extraDetails').textContent=x.details||'No additional details saved.';$('question').textContent='Loading…';$('answer').textContent='Loading…';$('errorInfo').textContent='Loading…';$('facts').replaceChildren();$('overlay').classList.add('open');$('close').focus();try{const d=await api('/api/detail?id='+encodeURIComponent(x.id));$('question').textContent=d.question||d.note||'Not available';$('answer').textContent=d.answer||d.note||'Not available';addFact('Response state',d.response_state);addFact('HTTP status code',d.http_status);addFact('Saved error code (not necessarily HTTP)',d.error_code,true);addFact('Chat title',d.chat_title,true);addFact('Chat ID',d.chat_id,true);addFact('Message ID',d.message_id,true);addFact('Message index',x.message_index);addFact('User ID',x.user_id,true);addFact('Model ID',d.message_model||x.model_id,true);addFact('Base models',x.base_models.join(', ')||'Not recorded',true);addFact('Sibling models',x.sibling_models.join(', ')||'None',true);addFact('Message created',d.message_created_at?formatDate(d.message_created_at):'Not recorded');addFact('Message updated',d.message_updated_at?formatDate(d.message_updated_at):'Not recorded');addFact('Feedback updated',formatDate(x.updated_at));addFact('Sources',d.source_count);addFact('Files',d.file_count);addFact('Embeds',d.embed_count);addFact('Follow-ups',d.followup_count);addFact('Structured output saved',d.has_output?'Yes':'No');addFact('Annotation saved',d.has_annotation?'Yes':'No');const usage=Object.entries(d.usage||{}).map(([k,v])=>`${k}: ${v}`).join(' · ');addFact('Saved usage',usage||'Not recorded',true);$('errorInfo').textContent=[d.error||'',...(d.status_history||[])].filter(Boolean).join('\n\n')||'No error or status events saved.'}catch(e){$('question').textContent='Could not load detail: '+e.message;$('answer').textContent='—';$('errorInfo').textContent='—'}}
function closeDetail(){$('overlay').classList.remove('open');if(lastFocus)lastFocus.focus()}
async function load(){try{const result=await api('/api/feedback');all=result.items||[];const current=$('modelFilter').value,models=[...new Map(all.map(x=>[x.model_id,x.model_name])).entries()].sort((a,b)=>a[1].localeCompare(b[1]));$('modelFilter').replaceChildren(new Option('All models',''));for(const [id,name]of models)$('modelFilter').add(new Option(name,id));$('modelFilter').value=current;$('live').textContent='Live · updated '+new Date().toLocaleTimeString();render()}catch(e){$('live').textContent='Read error';$('status').textContent=e.message}}
for(const id of ['search','modelFilter','ratingFilter','daysFilter'])$(id).addEventListener(id==='search'?'input':'change',render);
$('refresh').onclick=load;$('close').onclick=closeDetail;$('overlay').onclick=e=>{if(e.target===$('overlay'))closeDetail()};document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail()});load();setInterval(load,15000);
</script></body></html>'''


def make_handler(database, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            # Do not print the access key or any rated content in server logs.
            pass

        def reply(self, status, content, mime):
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status, value):
            self.reply(status, json.dumps(value, ensure_ascii=False),
                       "application/json; charset=utf-8")

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            supplied = (query.get("key") or [""])[0] if parsed.path == "/" else self.headers.get("X-Local-Token", "")
            if not secrets.compare_digest(supplied, token):
                self.json_reply(403, {"error": "Access key required."})
                return
            try:
                if parsed.path == "/":
                    self.reply(200, HTML, "text/html; charset=utf-8")
                elif parsed.path == "/api/feedback":
                    self.json_reply(200, read_feedback(database))
                elif parsed.path == "/api/detail":
                    feedback_id = (query.get("id") or [""])[0]
                    if not feedback_id or len(feedback_id) > 256:
                        self.json_reply(400, {"error": "Invalid feedback ID."})
                        return
                    result = read_detail(database, feedback_id)
                    self.json_reply(200 if result is not None else 404,
                                    result if result is not None else {"error": "Feedback not found."})
                else:
                    self.json_reply(404, {"error": "Not found."})
            except (OSError, sqlite3.Error, RuntimeError) as error:
                self.json_reply(500, {"error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE,
                        help="Open WebUI SQLite database path")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"Database not found: {args.db}")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    # Fail early on unsupported database layouts.
    read_feedback(args.db)
    key = secrets.token_urlsafe(32)
    try:
        server = ThreadingHTTPServer((HOST, args.port), make_handler(args.db, key))
    except OSError as error:
        parser.error(f"Could not start the dashboard: {error}")
    server.daemon_threads = True
    url = f"http://{HOST}:{args.port}/?key={key}"
    print("Open WebUI feedback dashboard is running locally.")
    print("URL=" + url)
    print("Ctrl+C stops the dashboard. Do not share the URL; it contains the access key.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
