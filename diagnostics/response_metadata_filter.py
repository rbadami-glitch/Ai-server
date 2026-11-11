"""
title: Temporary response metadata capture
author: Local diagnostics
version: 0.1.0
description: Records numeric request settings and terminal status for one model; never records chat content.
"""

import hashlib
import json
import os
from datetime import datetime, timezone


TARGET_MODEL = __MODEL_JSON__
LOG_PATH = __LOG_PATH_JSON__
CAPTURE_UNTIL_UTC = __UNTIL_JSON__
MAX_BYTES = 5 * 1024 * 1024


def _number(value):
    return value if type(value) in (int, float) and 0 <= value <= 1000000 else None


def _reason(value):
    return value if value in ("stop", "length", "tool_calls", "content_filter", "error", "cancelled") else "other"


def _trace(metadata):
    if not isinstance(metadata, dict) or not metadata.get("session_id"):
        return None
    chat_id = metadata.get("chat_id")
    message_id = metadata.get("message_id")
    if not isinstance(chat_id, str) or not isinstance(message_id, str):
        return None
    return hashlib.sha256((chat_id + ":" + message_id).encode("utf-8")).hexdigest()[:20]


def _eligible(model, metadata):
    return (
        isinstance(model, dict)
        and model.get("id") == TARGET_MODEL
        and _trace(metadata) is not None
        and datetime.now(timezone.utc) < datetime.fromisoformat(CAPTURE_UNTIL_UTC)
    )


def _write(stage, trace, fields):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) >= MAX_BYTES:
            return
        record = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "trace": trace,
            **fields,
        }
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


class Filter:
    async def request(self, body: dict, __metadata__: dict = None, __model__: dict = None) -> dict:
        try:
            model = __model__ if isinstance(__model__, dict) else {"id": body.get("model")}
            if _eligible(model, __metadata__):
                options = body.get("options") or {}
                if not isinstance(options, dict):
                    options = {}
                _write("request", _trace(__metadata__), {
                    "max_tokens": _number(body.get("max_tokens")),
                    "num_predict": _number(options.get("num_predict")),
                    "stream": body.get("stream") is True,
                })
        except Exception:
            pass
        return body

    async def stream(self, event: dict, __metadata__: dict = None, __model__: dict = None) -> dict:
        try:
            if _eligible(__model__, __metadata__) and isinstance(event, dict):
                trace = _trace(__metadata__)
                for choice in (event.get("choices") or []):
                    if isinstance(choice, dict) and choice.get("finish_reason"):
                        _write("stream_finish", trace, {
                            "reason": _reason(choice.get("finish_reason")),
                            "completion_tokens": _number((event.get("usage") or {}).get("completion_tokens")),
                        })
                kind = event.get("type")
                if kind in ("response.completed", "response.failed", "response.incomplete"):
                    response = event.get("response") or {}
                    usage = response.get("usage") or {}
                    _write("stream_finish", trace, {
                        "reason": kind,
                        "completion_tokens": _number(usage.get("output_tokens")),
                    })
                elif kind == "chat:completion":
                    data = event.get("data") or {}
                    if data.get("done") is True or data.get("error"):
                        _write("ui_completion", trace, {
                            "done": data.get("done") is True,
                            "error": bool(data.get("error")),
                        })
        except Exception:
            pass
        return event

    async def outlet(self, body: dict, __metadata__: dict = None, __model__: dict = None) -> dict:
        try:
            if _eligible(__model__, __metadata__):
                messages = body.get("messages") or []
                last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
                _write("outlet", _trace(__metadata__), {
                    "reason": _reason(last.get("finish_reason")) if last.get("finish_reason") else None,
                    "completion_tokens": _number((last.get("usage") or {}).get("completion_tokens")),
                })
        except Exception:
            pass
        return body
