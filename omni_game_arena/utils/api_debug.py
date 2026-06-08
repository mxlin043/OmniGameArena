"""Per-call API debug logger.

When attached to a backend (``backend.debug_logger = ApiDebugLogger(...)``),
every ``chat()`` / ``chat_with_tools()`` call dumps a single JSON file
containing the full request and response. Images are stripped out of the
JSON body - they get saved into a ``images/`` sub-folder, keyed by their
SHA-256, and the JSON references the relative path. This keeps each call
file human-readable while still letting you correlate what the model saw.

Layout
------
    <out_dir>/
        images/
            <sha256>.jpg                  # de-duplicated across calls
            ...
        call_0001.json
        call_0002.json
        ...

Each ``call_NNNN.json`` shape:
    {
        "call_idx": 1,
        "ts": <unix_seconds>,
        "metadata": {
            "model": "claude-opus-4-6",
            "backend": "anthropic",
            "latency_s": 8.31,
            "status": "ok" | "empty" | "error"
        },
        "system": "...",            # only when caller passes it separately
        "tools": [...],             # only for tool-use calls
        "messages": [               # original message list,
            {"role": "system", "content": "..."},
            {"role": "user",
             "content": [
                 {"type": "image_ref", "media_type": "image/jpeg",
                  "path": "images/abc123.jpg"},
                 {"type": "text", "text": "..."}
             ]},
            ...
        ],
        "response": "..."          # str (chat) or full body dict (chat_with_tools)
    }
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any


class ApiDebugLogger:
    """Per-call dump of API request + response. See module docstring."""

    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.out_dir / "images"
        self.image_dir.mkdir(exist_ok=True)
        self.call_idx = 0

    # -- public API ------------------------------------------------------
    def record(
        self,
        *,
        messages: list[dict],
        response: Any,
        system: str | None = None,
        tools: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> Path:
        """Write one debug file for this call. Returns the file path."""
        self.call_idx += 1
        payload: dict[str, Any] = {
            "call_idx": self.call_idx,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        if system is not None:
            payload["system"] = system
        if tools is not None:
            payload["tools"] = tools
        payload["messages"] = [self._sanitize_msg(m) for m in messages]
        payload["response"] = self._sanitize_response(response)

        out_path = self.out_dir / f"call_{self.call_idx:04d}.json"
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return out_path

    # -- internals: sanitize one message / block -------------------------
    def _sanitize_msg(self, msg: dict) -> dict:
        return {
            "role": msg.get("role"),
            "content": self._sanitize_content(msg.get("content")),
        }

    def _sanitize_content(self, content: Any) -> Any:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return [self._sanitize_block(b) for b in content]
        return content

    def _sanitize_block(self, block: Any) -> Any:
        if not isinstance(block, dict):
            return block
        t = block.get("type")

        # Anthropic image block: { "type":"image",
        #   "source":{"type":"base64","media_type":"image/jpeg","data":"<b64>"} }
        if t == "image":
            src = block.get("source") or {}
            if isinstance(src, dict) and src.get("type") == "base64":
                rel = self._save_b64_image(
                    src.get("data") or "",
                    src.get("media_type") or "image/jpeg",
                )
                return {
                    "type": "image_ref",
                    "media_type": src.get("media_type"),
                    "path": rel,
                }
            return block

        # OpenAI image block: { "type":"image_url",
        #   "image_url":{"url":"data:image/jpeg;base64,<b64>"} }
        if t == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            if isinstance(url, str) and url.startswith("data:"):
                head, _, b64 = url.partition(",")
                media_type = "image/jpeg"
                if ":" in head:
                    media_type = head.split(":", 1)[1].split(";", 1)[0]
                rel = self._save_b64_image(b64, media_type)
                return {
                    "type": "image_ref",
                    "media_type": media_type,
                    "path": rel,
                }
            return block

        # Anthropic tool_result block - content can be a list with nested
        # image blocks (analyzer's read_image tool). Recurse.
        if t == "tool_result":
            return {
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id"),
                "content": self._sanitize_content(block.get("content")),
                **(
                    {"is_error": block["is_error"]}
                    if "is_error" in block else {}
                ),
            }

        return block  # text / tool_use / unknown - pass through

    def _sanitize_response(self, response: Any) -> Any:
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            # chat_with_tools returns the full body; ``content`` may have
            # text and tool_use blocks. No images in the response (model
            # doesn't generate images), so blocks pass through unchanged.
            out = dict(response)
            if isinstance(out.get("content"), list):
                out["content"] = [self._sanitize_block(b) for b in out["content"]]
            return out
        return response

    # -- image extraction -------------------------------------------------
    def _save_b64_image(self, b64: str, media_type: str) -> str | None:
        try:
            data = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return None
        h = hashlib.sha256(data).hexdigest()[:32]  # 32 hex chars is plenty
        if "png" in (media_type or ""):
            ext = "png"
        elif "webp" in (media_type or ""):
            ext = "webp"
        else:
            ext = "jpg"
        path = self.image_dir / f"{h}.{ext}"
        if not path.exists():
            path.write_bytes(data)
        # Relative path from the call_*.json file (same dir as images/).
        return f"images/{h}.{ext}"
