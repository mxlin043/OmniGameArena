"""Generic self-hosted OpenAI-compatible chat-completions backend."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from PIL import Image

from ..base import Backend

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MAX_TOKENS = 512
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class OpenAICompatSelfHostBackend(Backend):
    """OpenAI-compatible chat completion client for self-hosted servers."""

    provider_name = "selfhost_openai_compat"
    default_enable_thinking: bool | None = True

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        request_model: str | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        chat_template_kwargs: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(model, **kwargs)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.request_model = request_model or model
        self.max_tokens = (
            DEFAULT_MAX_TOKENS if max_tokens is None else int(max_tokens)
        )
        self.enable_thinking = (
            self.default_enable_thinking
            if enable_thinking is None
            else bool(enable_thinking)
        )
        self.chat_template_kwargs = dict(chat_template_kwargs or {})
        self.extra_body = dict(extra_body or {})

    def make_image_content(self, img: Image.Image) -> dict:
        b64 = self.encode_image(img)
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        }

    def chat(self, messages: list[dict]) -> str:
        self.last_messages = messages
        self._clear_latency_metadata()
        params = self._build_request_body(messages)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body_data = json.dumps(params, ensure_ascii=False)
        total = self.max_retries + 1
        for attempt in range(total):
            attempt_no = attempt + 1
            t0 = time.time()
            try:
                resp = requests.post(
                    url=f"{self.base_url}/chat/completions",
                    data=body_data,
                    headers=headers,
                    timeout=self.request_timeout,
                )
            except requests.exceptions.Timeout:
                self.last_response_json = {
                    "error": "Timeout",
                    "text": f"request timed out after {self.request_timeout}s",
                }
                if attempt_no < total:
                    logger.warning(
                        "%s request timed out after %.1fs "
                        "(attempt %d/%d); retrying",
                        self.provider_name,
                        self.request_timeout,
                        attempt_no,
                        total,
                    )
                    time.sleep(min(2 ** attempt, 5.0))
                    continue
                logger.error("%s request timeout", self.provider_name)
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status="timeout",
                    extra={"attempts": attempt_no},
                )
                return ""
            except requests.exceptions.RequestException as exc:
                self.last_response_json = {
                    "error": type(exc).__name__,
                    "text": str(exc),
                }
                if attempt_no < total:
                    logger.warning(
                        "%s network error (attempt %d/%d): %s; retrying",
                        self.provider_name,
                        attempt_no,
                        total,
                        exc,
                    )
                    time.sleep(min(2 ** attempt, 5.0))
                    continue
                logger.error("%s network error: %s", self.provider_name, exc)
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status="error",
                    extra={"attempts": attempt_no},
                )
                return ""

            if resp.status_code in RETRYABLE_STATUS and attempt_no < total:
                logger.warning(
                    "%s HTTP %d (attempt %d/%d); retrying: %s",
                    self.provider_name,
                    resp.status_code,
                    attempt_no,
                    total,
                    resp.text[:200],
                )
                self.last_response_json = {
                    "error": resp.status_code,
                    "text": resp.text,
                }
                time.sleep(min(2 ** attempt, 5.0))
                continue

            if resp.status_code != 200:
                logger.error(
                    "%s API error: %d - %s",
                    self.provider_name,
                    resp.status_code,
                    resp.text,
                )
                self.last_response_json = {
                    "error": resp.status_code,
                    "text": resp.text,
                }
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json,
                    latency_s=time.time() - t0,
                    status=f"http_{resp.status_code}",
                    extra={"attempts": attempt_no},
                )
                return ""

            try:
                result = json.loads(resp.text)
                self.last_response_json = result
                message = result["choices"][0]["message"]
                text = message.get("content") or ""
                self._set_latency_from_response(result)
                if not text and message.get("reasoning_content"):
                    logger.warning(
                        "%s response has reasoning_content but empty content; "
                        "raise max_tokens if this happens often.",
                        self.provider_name,
                    )
                self._debug_record(
                    messages=messages,
                    response=result,
                    latency_s=time.time() - t0,
                    status="ok" if text else "empty",
                    extra={"attempts": attempt_no},
                )
                return text
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                logger.error("%s failed to parse response: %s", self.provider_name, exc)
                self._debug_record(
                    messages=messages,
                    response=self.last_response_json or resp.text,
                    latency_s=time.time() - t0,
                    status="parse_error",
                    extra={"attempts": attempt_no},
                )
                return ""

        return ""

    def _build_request_body(self, messages: list[dict]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "messages": messages,
            "model": self.request_model,
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature

        chat_kwargs = dict(self.chat_template_kwargs)
        if self.enable_thinking is not None:
            chat_kwargs["enable_thinking"] = self.enable_thinking
        if chat_kwargs:
            params["chat_template_kwargs"] = chat_kwargs

        params.update(self.extra_body)
        return params

    def _set_latency_from_response(self, body: dict[str, Any]) -> None:
        usage = body.get("usage") or {}
        self._set_decision_latency(
            None,
            source=None,
            details={
                "output_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": usage.get("reasoning_tokens")
                or (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                ),
            },
        )

    def _debug_record(
        self,
        *,
        messages,
        response,
        latency_s: float,
        status: str,
        extra: dict | None = None,
    ) -> None:
        if self.debug_logger is None:
            return
        try:
            metadata = {
                "model": self.model,
                "request_model": self.request_model,
                "backend": self.provider_name,
                "endpoint": self.base_url,
                "max_tokens": self.max_tokens,
                "enable_thinking": self.enable_thinking,
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
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - debug must never break flow
            logger.warning("api_debug record failed: %s", exc)
