"""Lossless transport translation for the text/image/function subset we use.

The player and reflection harness keep their existing chat-shaped history.
This adapter selects no reasoning, sampling, or token-budget defaults.
"""

from __future__ import annotations

from copy import deepcopy


def _content(content):
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        kind = part.get("type")
        if kind == "text":
            parts.append({"type": "input_text", "text": part["text"]})
        elif kind == "image_url":
            image = part["image_url"]
            item = {"type": "input_image", "image_url": image["url"]}
            if "detail" in image:
                item["detail"] = image["detail"]
            parts.append(item)
        else:
            raise ValueError(f"Unsupported Responses content type: {kind!r}")
    return parts


def responses_params(chat: dict) -> dict:
    """Keep message roles/order, image bytes, tool links, and explicit options."""
    out = {key: deepcopy(value) for key, value in chat.items()
           if key not in {"messages", "max_tokens", "tools", "tool_choice"}}
    items = []
    for msg in chat["messages"]:
        role, content = msg["role"], msg.get("content")
        if role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": msg["tool_call_id"],
                          "output": _content(content) if content is not None else ""})
            continue
        if role not in {"user", "assistant", "system", "developer"}:
            raise ValueError(f"Unsupported Responses message role: {role!r}")
        if content is not None:
            items.append({"role": role, "content": _content(content)})
        for call in msg.get("tool_calls") or []:
            if call["type"] != "function":
                raise ValueError("Only function tool calls are supported")
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": call["function"]["name"],
                          "arguments": call["function"]["arguments"]})
    out["input"] = items
    if "max_tokens" in chat:
        out["max_output_tokens"] = chat["max_tokens"]
    if "tools" in chat:
        out["tools"] = []
        for tool in chat["tools"]:
            if tool["type"] != "function":
                raise ValueError("Only function tools are supported")
            # Chat's default is non-strict; Responses must retain that behavior.
            out["tools"].append({"type": "function", "strict": False,
                                 **deepcopy(tool["function"])})
    if "tool_choice" in chat:
        choice = chat["tool_choice"]
        out["tool_choice"] = (
            {"type": "function", "name": choice["function"]["name"]}
            if isinstance(choice, dict) else choice
        )
    return out


def chat_response(response: dict) -> dict:
    """Normalize Responses results for existing parsing, tools, and accounting."""
    if response.get("error") or response.get("status") in {"failed", "cancelled", "queued", "in_progress"}:
        raise RuntimeError(f"Responses request did not complete: {response.get('error') or response.get('status')}")
    texts, calls = [], []
    for item in response.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part["text"])
                elif part.get("type") == "refusal":
                    texts.append(part["refusal"])
        elif item.get("type") == "function_call":
            calls.append({"id": item["call_id"], "type": "function",
                          "function": {"name": item["name"], "arguments": item["arguments"]}})
    finish = "tool_calls" if calls else "stop"
    if response.get("status") == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason")
        finish = "length" if reason == "max_output_tokens" else "content_filter"
    raw_usage = response.get("usage") or {}
    usage = {**raw_usage,
             "prompt_tokens": raw_usage.get("input_tokens", 0),
             "completion_tokens": raw_usage.get("output_tokens", 0),
             "total_tokens": raw_usage.get("total_tokens", 0),
             "prompt_tokens_details": raw_usage.get("input_tokens_details"),
             "completion_tokens_details": raw_usage.get("output_tokens_details")}
    return {"id": response.get("id") or "responses", "object": "chat.completion",
            "created": int(response.get("created_at") or 0), "model": response.get("model") or "",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": "\n".join(texts), "tool_calls": calls or None}}],
            "usage": usage, "responses_raw": response}
