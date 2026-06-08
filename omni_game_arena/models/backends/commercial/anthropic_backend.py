"""Anthropic Messages API backend for Claude-family commercial models.

Ships with raw ``requests`` for one reason: some proxies expect
``Authorization: Bearer`` while the official Anthropic SDK injects an
``x-api-key`` header. Using the SDK works against the official endpoint
but is finicky against ``Bearer``-style proxies. Raw POST keeps the
wire format explicit and easy to swap. Endpoint and key are read from
``configs/router.yaml``.
"""

from __future__ import annotations

import json
import logging
import random
import time

import requests
from PIL import Image

from ..base import Backend
from .router_config import commercial_value

logger = logging.getLogger(__name__)

# Retry policy. Anthropic / proxy gateways will return 429 on rate limit;
# 5xx on transient server issues; sometimes timeouts mid-stream. The retry
# count comes from Backend.max_retries. 4xx other than 429 (auth errors,
# invalid request) are NOT retried - they are permanent.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 30.0
RETRY_AFTER_CAP_S = 60.0

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 4096

AVAILABLE_MODELS = [
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-6",
]

LCRT_LATENCY_POLICY = {
    "claude-sonnet-4-6": "header.x-amzn-bedrock-invocation-latency",
    "claude-opus-4-6": "header.x-amzn-bedrock-invocation-latency",
    "claude-opus-4-7": "header.x-amzn-bedrock-invocation-latency",
}

# Model-name prefixes that reject the deprecated `temperature` param on this
# endpoint (opus 4.7+ dropped it). Prefix match also covers suffixed variants
# such as "claude-opus-4-8[1m]".
NO_TEMPERATURE_PREFIXES = ("claude-opus-4-7", "claude-opus-4-8")


def _effective_base_url() -> str:
    return commercial_value("anthropic", "base_url")


def _effective_api_key() -> str:
    return commercial_value("anthropic", "api_key")


# Convenience constants for callers that POST raw HTTP themselves.
BASE_URL = _effective_base_url()
API_KEY = _effective_api_key()
MESSAGES_URL = f"{BASE_URL}/v1/messages"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "anthropic-version": ANTHROPIC_VERSION,
    "Content-Type": "application/json",
}


class AnthropicBackend(Backend):
    """Anthropic Messages API client (Claude-family)."""

    def __init__(self, model: str, **kwargs):
        super().__init__(model, **kwargs)
        self.base_url = _effective_base_url()
        self.api_key = _effective_api_key()
        self.messages_url = f"{self.base_url}/v1/messages"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        _ml = model.lower()
        self._send_temperature = not any(
            _ml.startswith(p) for p in NO_TEMPERATURE_PREFIXES
        )

    def make_image_content(self, img: Image.Image) -> dict:
        b64 = self.encode_image(img)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        }

    @staticmethod
    def _text_from_content(content) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)

    def _to_payload(self, messages: list[dict]) -> dict:
        """Convert OpenAI-style message list to Anthropic Messages payload.

        ``system`` messages are pulled out into the top-level ``system``
        field; ``user`` / ``assistant`` content lists pass through
        verbatim (image source blocks are already in Anthropic shape
        because ``make_image_content`` produces them).
        """
        system_parts: list[str] = []
        anthropic_messages: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                text = self._text_from_content(content)
                if text:
                    system_parts.append(text)
                continue
            if role in ("user", "assistant"):
                anthropic_messages.append({"role": role, "content": content})

        payload: dict = {
            "model": self.model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if self.temperature is not None and self._send_temperature:
            payload["temperature"] = self.temperature
        return payload

    @staticmethod
    def _backoff_seconds(attempt: int, *, retry_after: str | None) -> float:
        """How long to sleep before the next retry.

        Honour the server's ``Retry-After`` header when present (capped so a
        misbehaving gateway can't pause us forever); otherwise fall back to
        exponential backoff with a small random jitter to avoid thundering-
        herd alignment when several agents hit 429 at once.
        """
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), RETRY_AFTER_CAP_S)
            except (TypeError, ValueError):
                pass
        base = min(INITIAL_BACKOFF_S * (2 ** attempt), MAX_BACKOFF_S)
        # +/-25% jitter
        return base * (0.75 + 0.5 * random.random())

    def chat(self, messages: list[dict]) -> str:
        """Send a chat completion request, with retry on transient failures.

        Retries up to ``self.max_retries`` extra attempts for: network
        errors, request timeouts, HTTP 408 /
        425 / 429 / 5xx, and JSON-decode failures on the response body.
        Permanent 4xx (auth / bad request) are NOT retried - those are bugs
        in our request, not transient gateway issues.

        Returns the assistant text, or ``""`` if all retries exhausted /
        request was permanently rejected. ``self.last_response_json`` and
        ``self.last_messages`` reflect the FINAL attempt.
        """
        self.last_messages = messages
        self._clear_latency_metadata()
        params = self._to_payload(messages)
        body_data = json.dumps(params)
        t0 = time.time()

        for attempt in range(self.max_retries + 1):
            attempt_no = attempt + 1
            total = self.max_retries + 1

            # -- Network layer -----------------------------------------
            try:
                resp = requests.post(
                    self.messages_url,
                    data=body_data,
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Anthropic network error (model=%s, attempt %d/%d): %s",
                    self.model, attempt_no, total, exc,
                )
                self.last_response_json = {
                    "error": type(exc).__name__, "text": str(exc),
                }
                if attempt_no < total:
                    time.sleep(self._backoff_seconds(attempt, retry_after=None))
                    continue
                logger.error(
                    "Anthropic exhausted retries on network error (model=%s)",
                    self.model,
                )
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0, status="net_exhausted",
                    extra={"attempts": attempt_no},
                )
                return ""

            # -- Retryable HTTP (rate limit, server errors) ------------
            if resp.status_code in RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                logger.warning(
                    "Anthropic HTTP %d (model=%s, attempt %d/%d) - retry after %s. body=%s",
                    resp.status_code, self.model, attempt_no, total,
                    retry_after, resp.text[:200],
                )
                self.last_response_json = {
                    "error": resp.status_code, "text": resp.text,
                }
                if attempt_no < total:
                    time.sleep(
                        self._backoff_seconds(attempt, retry_after=retry_after)
                    )
                    continue
                logger.error(
                    "Anthropic exhausted retries on HTTP %d (model=%s)",
                    resp.status_code, self.model,
                )
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status=f"http_{resp.status_code}_exhausted",
                    extra={"attempts": attempt_no},
                )
                return ""

            # -- Permanent failure ------------------------------------
            if resp.status_code != 200:
                logger.error(
                    "Anthropic HTTP %d (model=%s, NOT retried - permanent): %s",
                    resp.status_code, self.model, resp.text[:300],
                )
                self.last_response_json = {
                    "error": resp.status_code, "text": resp.text,
                }
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status=f"http_{resp.status_code}_permanent",
                )
                return ""

            # -- Parse 200 --------------------------------------------
            try:
                body = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Anthropic response JSON decode failed (model=%s, attempt %d/%d): %s",
                    self.model, attempt_no, total, exc,
                )
                if attempt_no < total:
                    time.sleep(self._backoff_seconds(attempt, retry_after=None))
                    continue
                logger.error(
                    "Anthropic exhausted retries on JSON decode (model=%s)",
                    self.model,
                )
                self._debug_record(
                    messages=messages,
                    response={"error": "json_decode", "text": resp.text[:1000]},
                    latency_s=time.time() - t0, status="json_decode_exhausted",
                )
                return ""

            self.last_response_json = body
            text = self._text_from_content(body.get("content", []))
            if attempt > 0:
                logger.info(
                    "Anthropic recovered after %d retries (model=%s)",
                    attempt, self.model,
                )
            self._set_latency_from_response(resp.headers, body)
            self._debug_record(
                messages=messages, response=text,
                latency_s=time.time() - t0,
                status="ok" if text else "empty",
                extra={"attempts": attempt_no, "usage": body.get("usage")},
            )
            return text

        # Loop fell through without hitting `return` - defensive fallback.
        self._debug_record(
            messages=messages, response="", latency_s=time.time() - t0,
            status="fell_through",
        )
        return ""

    # -- Tool-use variant -------------------------------------------------
    # Same retry policy as ``chat()`` but returns the parsed body so callers
    # running an agent loop can inspect ``content`` blocks (text + tool_use)
    # and ``stop_reason``. The Messages API is identical except we pass
    # ``tools`` and accept ``tool_use``/``tool_result`` content blocks in
    # the message stream.
    #
    # ``messages`` here may legitimately include assistant turns whose
    # content is a *list* of blocks (text + tool_use), and user turns
    # whose content is a list of tool_result blocks. ``_to_payload``
    # already passes content through verbatim for user/assistant roles,
    # so no special handling is needed beyond bypassing _to_payload's
    # default and building the payload inline.
    def _set_latency_from_response(self, headers, body: dict) -> None:
        raw = headers.get("X-Amzn-Bedrock-Invocation-Latency")
        details = {
            "policy": LCRT_LATENCY_POLICY.get(
                self.model.lower(), "header.x-amzn-bedrock-invocation-latency"
            ),
            "output_tokens": (body.get("usage") or {}).get("output_tokens"),
            "reasoning_tokens": None,
            "header_x_amzn_bedrock_invocation_latency": raw,
        }
        try:
            latency_ms = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            latency_ms = None

        if latency_ms is None:
            self._set_decision_latency(None, source=None, details=details)
            return

        self._set_decision_latency(
            latency_ms / 1000.0,
            source="header.x-amzn-bedrock-invocation-latency",
            details=details,
        )

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        system: str | None = None,
        max_tokens: int = ANTHROPIC_MAX_TOKENS,
    ) -> dict:
        """Send a tool-enabled Messages API request.

        Returns the parsed response body dict (with ``content``,
        ``stop_reason``, ``usage`` keys). Caller drives the agent loop:
        if ``stop_reason == "tool_use"``, execute the tools in
        ``content`` and append a user message with ``tool_result``
        blocks, then call again.

        Returns ``{}`` on permanent failure / exhausted retries (mirrors
        ``chat()`` returning ``""`` for the same).
        """
        self.last_messages = messages

        # Split out system from messages (if caller passed it inline) and
        # merge with the explicit ``system`` arg.
        system_parts: list[str] = []
        anthropic_messages: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                text = self._text_from_content(content)
                if text:
                    system_parts.append(text)
                continue
            if role in ("user", "assistant"):
                anthropic_messages.append({"role": role, "content": content})
        if system is not None and system.strip():
            system_parts.append(system)

        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
            "tools": tools,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if self.temperature is not None and self._send_temperature:
            payload["temperature"] = self.temperature

        body_data = json.dumps(payload)
        t0 = time.time()

        for attempt in range(self.max_retries + 1):
            attempt_no = attempt + 1
            total = self.max_retries + 1

            try:
                resp = requests.post(
                    self.messages_url,
                    data=body_data,
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Anthropic tool-use network error (model=%s, attempt %d/%d): %s",
                    self.model, attempt_no, total, exc,
                )
                self.last_response_json = {
                    "error": type(exc).__name__, "text": str(exc),
                }
                if attempt_no < total:
                    time.sleep(self._backoff_seconds(attempt, retry_after=None))
                    continue
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0, status="net_exhausted",
                    system=payload.get("system"), tools=tools, mode="tool_use",
                )
                return {}

            if resp.status_code in RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                logger.warning(
                    "Anthropic tool-use HTTP %d (model=%s, attempt %d/%d) - "
                    "retry after %s. body=%s",
                    resp.status_code, self.model, attempt_no, total,
                    retry_after, resp.text[:200],
                )
                self.last_response_json = {
                    "error": resp.status_code, "text": resp.text,
                }
                if attempt_no < total:
                    time.sleep(
                        self._backoff_seconds(attempt, retry_after=retry_after)
                    )
                    continue
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status=f"http_{resp.status_code}_exhausted",
                    system=payload.get("system"), tools=tools, mode="tool_use",
                )
                return {}

            if resp.status_code != 200:
                logger.error(
                    "Anthropic tool-use HTTP %d (model=%s, NOT retried): %s",
                    resp.status_code, self.model, resp.text[:300],
                )
                self.last_response_json = {
                    "error": resp.status_code, "text": resp.text,
                }
                self._debug_record(
                    messages=messages, response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status=f"http_{resp.status_code}_permanent",
                    system=payload.get("system"), tools=tools, mode="tool_use",
                )
                return {}

            try:
                body = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Anthropic tool-use JSON decode failed (model=%s, "
                    "attempt %d/%d): %s",
                    self.model, attempt_no, total, exc,
                )
                if attempt_no < total:
                    time.sleep(self._backoff_seconds(attempt, retry_after=None))
                    continue
                self._debug_record(
                    messages=messages,
                    response={"error": "json_decode", "text": resp.text[:1000]},
                    latency_s=time.time() - t0, status="json_decode_exhausted",
                    system=payload.get("system"), tools=tools, mode="tool_use",
                )
                return {}

            self.last_response_json = body
            if attempt > 0:
                logger.info(
                    "Anthropic tool-use recovered after %d retries (model=%s)",
                    attempt, self.model,
                )
            self._debug_record(
                messages=messages, response=body,
                latency_s=time.time() - t0,
                status="ok",
                system=payload.get("system"), tools=tools, mode="tool_use",
                extra={"attempts": attempt_no, "usage": body.get("usage")},
            )
            return body

        self._debug_record(
            messages=messages, response={}, latency_s=time.time() - t0,
            status="fell_through", system=payload.get("system"),
            tools=tools, mode="tool_use",
        )
        return {}

    # -- debug sink ------------------------------------------------------
    def _debug_record(
        self,
        *,
        messages,
        response,
        latency_s: float,
        status: str,
        system: str | None = None,
        tools: list[dict] | None = None,
        mode: str = "chat",
        extra: dict | None = None,
    ) -> None:
        """Forward one call's (request, response) to the attached debug
        logger if present. Never raises - debug must not break the call."""
        if self.debug_logger is None:
            return
        try:
            metadata = {
                "model": self.model,
                "backend": "anthropic",
                "mode": mode,
                "endpoint": self.messages_url,
                "latency_s": round(latency_s, 4),
                "status": status,
                "decision_latency_s": self.last_decision_latency_s,
                "decision_latency_source": self.last_decision_latency_source,
                "latency_details": self.last_latency_details,
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_debug record failed: %s", exc)
