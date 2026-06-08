"""OpenAI-compatible commercial VLM backend.

Handles all models that talk OpenAI ``chat/completions`` schema -
GPT-*, Gemini-*, Kimi-*, Hunyuan, GLM, etc. A single proxy can front
all of these behind one OpenAI-compatible endpoint, so we ship one
Backend for those OpenAI-compatible commercial routes instead of
separate per-vendor classes.

Endpoint and key are read from ``configs/router.yaml``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from PIL import Image

from ..base import Backend
from ..timing import CallLatency, timed_post
from .router_config import commercial_value

logger = logging.getLogger(__name__)

# Models known to respond on the OpenAI-compatible route of the
# configured proxy. New names only need to be added here.
AVAILABLE_MODELS = [
    "gpt-5.4-mini",
    "gemini-3.1-flash-lite-preview",
    "Kimi-K2.5",
    "gpt-5.5",
    "gpt-5.4",
    "gemini-3.1-pro-preview",
]

# These model families reject non-default/deprecated temperature.
NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Per-model client-side throttling (seconds between consecutive requests).
# Some models on the gateway have very tight QPS quotas. Lookup is
# case-insensitive against ``model.lower()``.
MIN_INTERVAL_SECONDS = {
    "hunyuan-turbos-vision-latest": 4.0,
}

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

LCRT_LATENCY_POLICY = {
    "gpt-5.4-mini": "usage.latency_checkpoint.engine_ttlt_ms",
    "gpt-5.4": "usage.latency_checkpoint.engine_ttlt_ms",
    "gpt-5.5": "usage.latency_checkpoint.engine_ttlt_ms",
    "gemini-3.1-flash-lite-preview": "timed_post.ttfb_minus_tcp",
    "gemini-3.1-pro-preview": "timed_post.ttfb_minus_tcp",
    "kimi-k2.5": "timed_post.ttfb_minus_tcp",
}


def _effective_base_url() -> str:
    return commercial_value("openai", "base_url")


def _effective_api_key() -> str:
    return commercial_value("openai", "api_key")


# Convenience constants for callers that POST raw HTTP themselves.
BASE_URL = _effective_base_url()
API_KEY = _effective_api_key()
API_URL = f"{BASE_URL}/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


class OpenAIBackend(Backend):
    """OpenAI SDK client for any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, model: str, **kwargs):
        super().__init__(model, **kwargs)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIBackend requires the OpenAI Python SDK. "
                "Install it with: python -m pip install openai"
            ) from exc

        self.base_url = _effective_base_url()
        self.api_key = _effective_api_key()
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.request_timeout,
            max_retries=0,
        )

        name = model.lower()
        self._send_temperature = not name.startswith(NO_TEMPERATURE_PREFIXES)
        self._min_interval = MIN_INTERVAL_SECONDS.get(name, 0.0)
        self._last_request_at: float = 0.0

    def make_image_content(self, img: Image.Image) -> dict:
        b64 = self.encode_image(img)
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        }

    def _respect_min_interval(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.time() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 5.0))

    def chat(self, messages: list[dict]) -> str:
        self.last_messages = messages
        self._clear_latency_metadata()

        params: dict = {"model": self.model, "messages": messages}
        if self.temperature is not None and self._send_temperature:
            params["temperature"] = self.temperature

        if self.lcrt_timing_enabled:
            return self._chat_timed_post(messages, params)

        total = self.max_retries + 1
        for attempt in range(total):
            attempt_no = attempt + 1
            self._respect_min_interval()
            t0 = time.time()
            try:
                self._last_request_at = t0
                result = self._client.chat.completions.create(
                    **params,
                    timeout=self.request_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_response_json = {
                    "error": type(exc).__name__,
                    "text": str(exc),
                }
                if _is_retryable_api_exception(exc) and attempt_no < total:
                    logger.warning(
                        "OpenAI endpoint error (model=%s, attempt %d/%d): %s; "
                        "retrying",
                        self.model,
                        attempt_no,
                        total,
                        exc,
                    )
                    self._sleep_before_retry(attempt)
                    continue

                logger.error(
                    "OpenAI endpoint error (model=%s, attempt %d/%d): %s",
                    self.model,
                    attempt_no,
                    total,
                    exc,
                )
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status="error",
                    extra={"attempts": attempt_no},
                )
                return ""

            latency_s = time.time() - t0
            try:
                self.last_response_json = (
                    result.model_dump() if hasattr(result, "model_dump") else None
                )
                text = result.choices[0].message.content or ""
            except (AttributeError, IndexError) as exc:
                logger.error("Failed to parse OpenAI endpoint response: %s", exc)
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=latency_s,
                    status="parse_error",
                    extra={"attempts": attempt_no},
                )
                return ""

            self._set_latency_from_response(
                body=self.last_response_json,
                call_latency=None,
            )
            self._debug_record(
                messages=messages,
                response=text,
                latency_s=latency_s,
                status="ok" if text else "empty",
                extra={"attempts": attempt_no},
            )
            return text

        return ""

    # -- debug sink ------------------------------------------------------
    def _chat_timed_post(self, messages: list[dict], params: dict) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body_data = json.dumps(params).encode("utf-8")
        total = self.max_retries + 1

        for attempt in range(total):
            attempt_no = attempt + 1
            self._respect_min_interval()
            t0 = time.time()
            try:
                self._last_request_at = t0
                status, _resp_headers, resp_body, call_latency = timed_post(
                    url,
                    headers,
                    body_data,
                    timeout=self.request_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                call_latency = getattr(exc, "_call_latency", None)
                self.last_call_latency = call_latency
                self.last_response_json = {
                    "error": type(exc).__name__,
                    "text": str(exc),
                }
                if _is_retryable_api_exception(exc) and attempt_no < total:
                    logger.warning(
                        "OpenAI timed endpoint error (model=%s, attempt %d/%d): "
                        "%s; retrying",
                        self.model,
                        attempt_no,
                        total,
                        exc,
                    )
                    self._sleep_before_retry(attempt)
                    continue

                logger.error(
                    "OpenAI timed endpoint error (model=%s, attempt %d/%d): %s",
                    self.model,
                    attempt_no,
                    total,
                    exc,
                )
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status="error",
                    extra={"attempts": attempt_no},
                )
                return ""

            self.last_call_latency = call_latency
            latency_s = call_latency.wall_ms / 1000.0

            if (
                status in RETRYABLE_STATUS
                or (call_latency.error and "download" in call_latency.error)
            ) and attempt_no < total:
                logger.warning(
                    "OpenAI timed HTTP retryable result (model=%s, attempt %d/%d): "
                    "status=%s error=%s; retrying",
                    self.model,
                    attempt_no,
                    total,
                    status,
                    call_latency.error,
                )
                self._sleep_before_retry(attempt)
                continue

            try:
                body = json.loads(resp_body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                logger.error("Failed to decode OpenAI timed response: %s", exc)
                self.last_response_json = {
                    "error": "json_decode",
                    "text": resp_body[:1000].decode("utf-8", errors="replace"),
                }
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=latency_s,
                    status="json_decode_error",
                    extra={"attempts": attempt_no},
                )
                return ""

            self.last_response_json = body
            self._set_latency_from_response(body=body, call_latency=call_latency)

            if status != 200:
                logger.error(
                    "OpenAI timed HTTP %d (model=%s): %s",
                    status,
                    self.model,
                    body,
                )
                self._debug_record(
                    messages=messages,
                    response=body,
                    latency_s=latency_s,
                    status=f"http_{status}",
                    extra={"attempts": attempt_no},
                )
                return ""

            try:
                text = body["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                logger.error("Failed to parse OpenAI timed response: %s", exc)
                self._debug_record(
                    messages=messages,
                    response=body,
                    latency_s=latency_s,
                    status="parse_error",
                    extra={"attempts": attempt_no},
                )
                return ""

            self._debug_record(
                messages=messages,
                response=text,
                latency_s=latency_s,
                status="ok" if text else "empty",
                extra={"attempts": attempt_no},
            )
            return text

        return ""

    def _set_latency_from_response(
        self,
        *,
        body: dict | None,
        call_latency: CallLatency | None,
    ) -> None:
        body = body or {}
        usage = body.get("usage") or {}
        server_ms = _extract_server_latency_ms(usage)
        if server_ms is not None and call_latency is not None:
            call_latency.server_latency_ms = float(server_ms)
        details: dict = {
            "policy": LCRT_LATENCY_POLICY.get(
                self.model.lower(), "timed_post.ttfb_minus_tcp"
            ),
            "output_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            ),
        }
        if call_latency is not None:
            details["timed_post"] = asdict(call_latency)

        if server_ms is not None:
            self._set_decision_latency(
                float(server_ms) / 1000.0,
                source="usage.latency_checkpoint.engine_ttlt_ms",
                details=details,
            )
            return

        if call_latency is not None:
            self._set_decision_latency(
                call_latency.pure_inference_ms / 1000.0,
                source="timed_post.ttfb_minus_tcp",
                details=details,
            )
            return

        self._set_decision_latency(None, source=None, details=details)

    def _debug_record(
        self,
        *,
        messages,
        response,
        latency_s: float,
        status: str,
        extra: dict | None = None,
        system: str | None = None,
        tools: list[dict] | None = None,
        mode: str = "chat",
    ) -> None:
        if self.debug_logger is None:
            return
        try:
            metadata = {
                "model": self.model,
                "backend": "openai_compat",
                "endpoint": getattr(self, "base_url", None),
                "latency_s": round(latency_s, 4),
                "status": status,
                "decision_latency_s": self.last_decision_latency_s,
                "decision_latency_source": self.last_decision_latency_source,
                "latency_details": self.last_latency_details,
                "mode": mode,
            }
            if extra:
                metadata.update(extra)
            self.debug_logger.record(
                messages=messages,
                response=response,
                system=system,
                tools=tools,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - debug must never break flow
            logger.warning("api_debug record failed: %s", exc)

    # -- chat_with_tools (Anthropic-shape adapter over OpenAI tool use) --
    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Tool-use call mirroring AnthropicBackend.chat_with_tools.

        Accepts Anthropic-shape messages / tool specs (so AnalyzerHarness
        can stay backend-agnostic), translates them to OpenAI's
        ``tools`` / ``tool_calls`` shape, calls the OpenAI Chat
        Completions API, and translates the response back to Anthropic
        shape ``{content: [...], stop_reason: ..., usage: ...}``.

        Returns ``{}`` on permanent failure / exhausted retries - same
        contract as ``chat()`` returning ``""``.
        """
        self.last_messages = messages
        self._clear_latency_metadata()

        # Gemini models route through an OpenAI-compat proxy that
        # enforces Vertex AI's thought_signature requirement on
        # follow-up tool_call messages - but the proxy DOES NOT expose
        # the signature in its OpenAI-compat response, so we cannot
        # forward it. Detect Gemini and rewrite the conversation: drop
        # assistant tool_calls, convert role:tool messages to role:user
        # with a "(Result of previous TOOL(ARGS) call: RESULT)" wrapper.
        # Gemini follows this just fine without the signature
        # requirement.
        is_gemini = self.model.lower().startswith("gemini")

        openai_messages = _to_openai_messages(
            messages, system=system, gemini_compat=is_gemini,
        )
        openai_tools = _to_openai_tool_specs(tools)

        params: dict = {
            "model": self.model,
            "messages": openai_messages,
            "tools": openai_tools,
            "tool_choice": "auto",
        }
        if self.temperature is not None and self._send_temperature:
            params["temperature"] = self.temperature
        if max_tokens is not None:
            params["max_tokens"] = int(max_tokens)

        total = self.max_retries + 1
        for attempt in range(total):
            attempt_no = attempt + 1
            self._respect_min_interval()
            t0 = time.time()
            try:
                self._last_request_at = t0
                result = self._client.chat.completions.create(
                    **params,
                    timeout=self.request_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_response_json = {
                    "error": type(exc).__name__,
                    "text": str(exc),
                }
                if _is_retryable_api_exception(exc) and attempt_no < total:
                    logger.warning(
                        "OpenAI tool-use error (model=%s, attempt %d/%d): %s; "
                        "retrying",
                        self.model, attempt_no, total, exc,
                    )
                    self._sleep_before_retry(attempt)
                    continue
                logger.error(
                    "OpenAI tool-use error (model=%s, attempt %d/%d): %s",
                    self.model, attempt_no, total, exc,
                )
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status="error",
                    extra={"attempts": attempt_no},
                    system=system,
                    tools=tools,
                    mode="tool_use",
                )
                return {}

            latency_s = time.time() - t0
            try:
                body = (
                    result.model_dump() if hasattr(result, "model_dump") else result
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to dump OpenAI tool-use response: %s", exc)
                self._debug_record(
                    messages=messages,
                    response=None,
                    latency_s=latency_s,
                    status="parse_error",
                    extra={"attempts": attempt_no},
                    system=system,
                    tools=tools,
                    mode="tool_use",
                )
                return {}

            self.last_response_json = body
            self._set_latency_from_response(body=body, call_latency=None)
            anthropic_shape = _to_anthropic_response(body)
            self._debug_record(
                messages=messages,
                response=anthropic_shape,
                latency_s=latency_s,
                status="ok" if anthropic_shape.get("content") else "empty",
                extra={"attempts": attempt_no},
                system=system,
                tools=tools,
                mode="tool_use",
            )
            return anthropic_shape

        return {}


def _extract_server_latency_ms(usage: dict) -> float | None:
    checkpoint = usage.get("latency_checkpoint") or {}
    value = checkpoint.get("engine_ttlt_ms")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_retryable_api_exception(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in RETRYABLE_STATUS:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return True
    if "connection" in name or "connection" in text:
        return True
    return False


# -- Anthropic <-> OpenAI tool-use translation -----------------------------
#
# AnalyzerHarness is written against the Anthropic Messages API shape
# (content blocks, tool_use / tool_result, stop_reason). To let any
# OpenAI-compatible model drive the same harness, we translate at the
# backend boundary:
#
#   harness -> _to_openai_tool_specs/_to_openai_messages -> OpenAI API
#   OpenAI API -> _to_anthropic_response -> harness
#
# Anthropic shapes we accept on the way in:
#   user message content:
#     str
#     [ {type:text,text:...}, {type:image,source:{...}},
#       {type:tool_result, tool_use_id:..., content: str | [blocks]} ]
#   assistant message content:
#     [ {type:text,text:...}, {type:tool_use,id:...,name:...,input:...} ]
#   tool spec:
#     {name, description, input_schema}
#
# OpenAI shapes we emit on the way out:
#   user message:
#     {role:user, content: str | [parts]}
#   assistant message:
#     {role:assistant, content: str|None, tool_calls: [...]}
#   tool result message (one per tool_result block - N tool_results in
#   one Anthropic user turn become N OpenAI tool messages):
#     {role:tool, tool_call_id:..., content: str | [parts]}
#   tool spec:
#     {type:function, function:{name, description, parameters}}
#
# Image-in-tool-message is supported by gpt-4o / gpt-5 family. Older
# OpenAI models that reject multimodal tool content will need a
# fallback (image-in-following-user-turn); for now we accept the
# possibility of error on legacy models.


def _to_openai_tool_specs(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool specs to OpenAI tool specs."""
    out: list[dict] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        # input_schema is Anthropic's name; OpenAI calls it "parameters".
        # Some specs may already use "parameters" (unlikely here but be
        # permissive).
        params = t.get("input_schema") or t.get("parameters") or {
            "type": "object", "properties": {},
        }
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": t.get("description", ""),
                "parameters": params,
            },
        })
    return out


def _to_openai_messages(
    messages: list[dict],
    *,
    system: str | None = None,
    gemini_compat: bool = False,
) -> list[dict]:
    """Walk Anthropic-shape messages and emit OpenAI-shape messages.

    ``gemini_compat``: when True, rewrite the conversation to avoid
    Gemini's ``thought_signature`` requirement on follow-up tool_calls.
    OpenAI-compat proxies typically enforce signatures but don't expose
    them - so we strip ``tool_calls`` from assistant messages and
    convert ``role:tool`` messages into ``role:user`` messages wrapped
    as ``(Result of your previous TOOL(ARGS) call: RESULT).``. Gemini
    follows this naturally and continues issuing tool_calls.
    """
    if gemini_compat:
        return _to_openai_messages_gemini(messages, system=system)

    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            # Inline system messages: fold into a system entry.
            text = _content_to_plain_text(content)
            if text:
                out.append({"role": "system", "content": text})
            continue

        if role == "assistant":
            text_parts, tool_calls = _split_assistant_content(content)
            assistant_msg: dict = {"role": "assistant"}
            assistant_msg["content"] = text_parts or None
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
            continue

        if role == "user":
            # User messages may contain tool_result blocks; each
            # tool_result becomes its OWN tool-role message in OpenAI
            # shape. Any non-tool_result content stays as a single
            # user message (preserving order: pre-tool_result text,
            # then tool messages, then post-tool_result text).
            if isinstance(content, str) or content is None:
                out.append({"role": "user", "content": content or ""})
                continue

            if not isinstance(content, list):
                out.append({"role": "user", "content": str(content)})
                continue

            # Walk blocks in order. tool_results become separate tool
            # messages; everything else accumulates into a user-message
            # content list that gets flushed when we hit a tool_result.
            buf: list[dict] = []

            def flush_buf():
                nonlocal buf
                if not buf:
                    return
                # Normalize: if everything in buf is text, collapse to
                # a string (OpenAI prefers it that way).
                if all(b.get("type") == "text" for b in buf):
                    out.append({
                        "role": "user",
                        "content": "\n".join(b.get("text", "") for b in buf),
                    })
                else:
                    out.append({"role": "user", "content": list(buf)})
                buf = []

            for block in content:
                if not isinstance(block, dict):
                    buf.append({"type": "text", "text": str(block)})
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    flush_buf()
                    tool_msg = _tool_result_to_openai(block)
                    if tool_msg is not None:
                        out.append(tool_msg)
                elif btype == "text":
                    buf.append({"type": "text", "text": block.get("text", "")})
                elif btype == "image":
                    img_part = _anthropic_image_to_openai(block)
                    if img_part:
                        buf.append(img_part)
                elif btype == "image_url":
                    # Already OpenAI-shape (e.g. from make_image_content).
                    buf.append(block)
                else:
                    # Pass through unknown blocks as text to avoid losing info.
                    buf.append({"type": "text", "text": json.dumps(block, default=str)})
            flush_buf()
            continue

        # Unknown role - drop with a logger warning.
        logger.warning("Unknown message role %r dropped during translation.", role)

    return out


def _to_openai_messages_gemini(
    messages: list[dict], *, system: str | None = None
) -> list[dict]:
    """Gemini-compat variant of message translation.

    Two key transforms vs the standard path:

    1. Assistant messages with tool_calls: KEEP any text content, DROP
       the tool_calls field entirely. The tool_calls would otherwise
       carry a missing-thought_signature error on the next turn.

    2. Tool-result blocks become USER messages with a wrapped prefix:
       ``(Result of your previous TOOL_NAME(JSON_ARGS) call: <result>).
       Continue your task.`` so Gemini knows which call produced the
       result without needing the structured tool_call_id linkage.

    Image-bearing tool_results are kept multimodal: the wrapper text +
    image parts go into a single user message.

    To wrap tool_results we need to know what tool was called and with
    what args. We collect a ``call_info`` map from prior assistant
    messages (tool_use_id -> {name, args}) as we walk.
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    call_info: dict[str, dict[str, str]] = {}

    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = _content_to_plain_text(content)
            if text:
                out.append({"role": "system", "content": text})
            continue

        if role == "assistant":
            # Harvest tool_use info so subsequent tool_results can be
            # wrapped with the right call signature. Then emit only
            # text content (drop tool_calls).
            text_buf: list[str] = []
            if isinstance(content, str):
                text_buf.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        text_buf.append(str(block))
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_buf.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tid = block.get("id")
                        if tid:
                            call_info[tid] = {
                                "name": block.get("name", ""),
                                "args": json.dumps(
                                    block.get("input") or {},
                                    ensure_ascii=False,
                                ),
                            }
                    # Other block types: skip.
            text = "\n".join(t for t in text_buf if t).strip()
            if text:
                out.append({"role": "assistant", "content": text})
            # If no text content, omit the assistant message entirely.
            continue

        if role == "user":
            if isinstance(content, str) or content is None:
                out.append({"role": "user", "content": content or ""})
                continue
            if not isinstance(content, list):
                out.append({"role": "user", "content": str(content)})
                continue

            # Build a single user message from this turn's blocks. Each
            # tool_result block contributes a wrapper text part (and
            # optional image parts).
            parts: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    parts.append({"type": "text", "text": str(block)})
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                elif btype == "image":
                    img_part = _anthropic_image_to_openai(block)
                    if img_part:
                        parts.append(img_part)
                elif btype == "image_url":
                    parts.append(block)
                elif btype == "tool_result":
                    parts.extend(
                        _tool_result_to_gemini_user_parts(block, call_info)
                    )
                else:
                    parts.append({"type": "text", "text": json.dumps(block, default=str)})

            # Collapse text-only parts to a string for cleanliness.
            if all(p.get("type") == "text" for p in parts):
                out.append({
                    "role": "user",
                    "content": "\n".join(p.get("text", "") for p in parts),
                })
            else:
                out.append({"role": "user", "content": parts})
            continue

        logger.warning("Unknown message role %r dropped during translation.", role)

    return out


def _tool_result_to_gemini_user_parts(
    block: dict, call_info: dict[str, dict[str, str]]
) -> list[dict]:
    """Wrap a tool_result block as user-message parts for Gemini.

    Produces a leading text part that names the tool + args and embeds
    the result string, followed by any image parts from the result.
    """
    tool_use_id = block.get("tool_use_id") or "?"
    info = call_info.get(tool_use_id) or {}
    name = info.get("name") or "tool"
    args = info.get("args") or "{}"
    inner = block.get("content")

    # Collect text and image parts from the result content.
    text_chunks: list[str] = []
    image_parts: list[dict] = []
    if isinstance(inner, str):
        text_chunks.append(inner)
    elif isinstance(inner, list):
        for b in inner:
            if not isinstance(b, dict):
                text_chunks.append(str(b))
                continue
            t = b.get("type")
            if t == "text":
                text_chunks.append(b.get("text", ""))
            elif t == "image":
                ip = _anthropic_image_to_openai(b)
                if ip:
                    image_parts.append(ip)
            elif t == "image_url":
                image_parts.append(b)
            else:
                text_chunks.append(json.dumps(b, default=str))
    elif inner is not None:
        text_chunks.append(str(inner))

    is_error = bool(block.get("is_error"))
    result_body = "\n".join(c for c in text_chunks if c).strip() or "(no text content)"
    err_tag = " [ERROR]" if is_error else ""
    wrapper = (
        f"(Result of your previous {name}({args}) call{err_tag}: "
        f"{result_body}). Continue your task."
    )

    parts: list[dict] = [{"type": "text", "text": wrapper}]
    parts.extend(image_parts)
    return parts


def _split_assistant_content(content) -> tuple[str | None, list[dict]]:
    """From an Anthropic assistant content list, return (text_or_none,
    list_of_openai_tool_calls).
    """
    if isinstance(content, str):
        return (content or None), []
    if not isinstance(content, list):
        return (str(content) if content is not None else None, [])
    texts: list[str] = []
    tool_calls: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            texts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id"),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(
                        block.get("input") or {},
                        ensure_ascii=False,
                    ),
                },
            })
        else:
            texts.append(json.dumps(block, default=str))
    joined = "\n".join(t for t in texts if t).strip() or None
    return joined, tool_calls


def _tool_result_to_openai(block: dict) -> dict | None:
    """Convert an Anthropic tool_result block to an OpenAI tool message."""
    tool_call_id = block.get("tool_use_id")
    if not tool_call_id:
        return None
    inner = block.get("content")
    if isinstance(inner, str):
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": inner,
        }
    if isinstance(inner, list):
        # Translate each part. text -> text; image -> image_url. Tool
        # messages with multimodal content are accepted by gpt-4o /
        # gpt-5 family. Older models may reject - we accept that.
        parts: list[dict] = []
        for b in inner:
            if not isinstance(b, dict):
                parts.append({"type": "text", "text": str(b)})
                continue
            t = b.get("type")
            if t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                ip = _anthropic_image_to_openai(b)
                if ip:
                    parts.append(ip)
            elif t == "image_url":
                parts.append(b)
            else:
                parts.append({"type": "text", "text": json.dumps(b, default=str)})
        if all(p.get("type") == "text" for p in parts):
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": "\n".join(p.get("text", "") for p in parts),
            }
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": parts,
        }
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": str(inner) if inner is not None else "",
    }


def _anthropic_image_to_openai(block: dict) -> dict | None:
    """Convert Anthropic image block to OpenAI image_url part."""
    src = block.get("source") or {}
    if not isinstance(src, dict):
        return None
    if src.get("type") != "base64":
        return None
    media_type = src.get("media_type") or "image/jpeg"
    data = src.get("data") or ""
    if not data:
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


def _content_to_plain_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    return "\n".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _to_anthropic_response(body: dict) -> dict:
    """Convert an OpenAI chat.completion response dict to Anthropic
    Messages-API response shape: ``{content: [...], stop_reason: ...,
    usage: ...}``.

    The Anthropic stop_reason values the harness checks for are
    ``"tool_use"`` and ``"end_turn"``; OpenAI's are ``"tool_calls"`` /
    ``"stop"`` / ``"length"``. Map accordingly.
    """
    if not isinstance(body, dict):
        return {}
    choices = body.get("choices") or []
    if not choices:
        return {}
    choice = choices[0]
    msg = (choice or {}).get("message") or {}

    content_blocks: list[dict] = []

    text = msg.get("content")
    if isinstance(text, str) and text.strip():
        content_blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        # Some endpoints return content as a list of parts. Pull the
        # text out.
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                content_blocks.append({
                    "type": "text",
                    "text": part.get("text", ""),
                })

    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments")
        try:
            parsed_input = (
                json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            )
        except (TypeError, ValueError):
            parsed_input = {"_raw_arguments": raw_args}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id"),
            "name": name,
            "input": parsed_input,
        })

    finish_reason = choice.get("finish_reason")
    stop_reason = _map_finish_to_stop(finish_reason)
    # Some proxies (notably Gemini's OpenAI-compat path) return
    # finish_reason="stop" even when the message includes tool_calls.
    # The Anthropic stop_reason contract requires "tool_use" whenever
    # tool_use blocks are present, so override based on content rather
    # than trusting finish_reason.
    has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
    if has_tool_use:
        stop_reason = "tool_use"

    return {
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": body.get("usage") or {},
        "id": body.get("id"),
        "model": body.get("model"),
    }


def _map_finish_to_stop(finish_reason: str | None) -> str | None:
    if finish_reason is None:
        return None
    mapping = {
        "tool_calls": "tool_use",
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "stop_sequence",
    }
    return mapping.get(finish_reason, finish_reason)
