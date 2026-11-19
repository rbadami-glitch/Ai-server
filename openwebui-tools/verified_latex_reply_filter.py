"""Open WebUI outlet filter for the private document-to-LaTeX canary.

When the conversion tool ran in this response, show its verified result in place
of any model-authored final answer. This does not decide when to call the tool.
Attach this filter only to the private test model until its behavior is reviewed.
"""

from __future__ import annotations

from uuid import uuid4


TOOL_NAME = "convert_attached_document_to_latex"


def _tool_name(value):
    if not isinstance(value, str):
        return False
    return value == TOOL_NAME or value.endswith("." + TOOL_NAME)


def _result_text(item):
    parts = item.get("output")
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
        and part.get("type") in {"input_text", "output_text"}
        and isinstance(part.get("text"), str)
    ).strip()


def _verified_tool_result(output):
    if not isinstance(output, list):
        return None
    calls = {
        item.get("call_id") or item.get("id")
        for item in output
        if isinstance(item, dict)
        and item.get("type") == "function_call"
        and _tool_name(item.get("name"))
    }
    if not calls:
        return None
    for item in reversed(output):
        if (isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") in calls):
            result = _result_text(item)
            if result:
                return result
    return None


def _source_tool_result(sources):
    """Open WebUI's legacy tool path stores the result as a citation source."""
    if not isinstance(sources, list):
        return None
    for item in reversed(sources):
        if not isinstance(item, dict) or item.get("tool_result") is not True:
            continue
        source = item.get("source")
        name = source.get("name") if isinstance(source, dict) else None
        if not isinstance(name, str) or name.split("/")[-1] != TOOL_NAME:
            continue
        documents = item.get("document")
        if isinstance(documents, list) and len(documents) == 1 and isinstance(documents[0], str):
            result = documents[0].strip()
            if result:
                return result
    return None


class Filter:
    async def stream(self, event: dict, __metadata__: dict = None) -> dict:
        """Show the verified legacy Tool result instead of model draft tokens.

        The legacy Tool executes before model generation and its result is in
        request metadata. Keep completion, usage, error, and file events intact.
        """
        if not isinstance(event, dict) or not isinstance(__metadata__, dict):
            return event
        result = _source_tool_result(__metadata__.get("sources"))
        if result is None:
            return event

        choices = event.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str) and delta["content"]:
                    if not __metadata__.get("_verified_latex_stream_sent"):
                        delta["content"] = result
                        __metadata__["_verified_latex_stream_sent"] = True
                    else:
                        delta["content"] = ""
            return event

        kind = event.get("type")
        if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
            if not __metadata__.get("_verified_latex_stream_sent"):
                event["delta"] = result
                __metadata__["_verified_latex_stream_sent"] = True
            else:
                event["delta"] = ""
        elif kind == "response.output_text.done" and isinstance(event.get("text"), str):
            event["text"] = result
        elif kind == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        part["text"] = result
        return event

    async def outlet(self, body: dict) -> dict:
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            return body
        message = messages[-1]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return body
        output = message.get("output")
        result = _verified_tool_result(output) or _source_tool_result(message.get("sources"))
        if result is None:
            return body

        # Keep function calls and outputs, including their verified file cards.
        # Discard the model's answer and insert the exact tool response.
        output = output if isinstance(output, list) else []
        final_message = next(
            (item for item in reversed(output)
             if isinstance(item, dict)
             and item.get("type") == "message"
             and item.get("role", "assistant") == "assistant"),
            None,
        )
        message_id = final_message.get("id") if final_message else None
        message["content"] = result
        message["output"] = [
            item for item in output
            if not (isinstance(item, dict)
                    and item.get("type") == "message"
                    and item.get("role", "assistant") == "assistant")
        ] + [{
            "type": "message",
            "id": message_id or f"msg_{uuid4().hex}",
            "role": "assistant",
            "content": [{"type": "output_text", "text": result}],
        }]
        return body
