"""Backend ABC - turns OpenAI-style messages into text.

Each subclass owns its own HTTP wire format and image-content schema
(some gateways need ``{"type":"image","source":{...}}`` while others
want OpenAI's ``{"type":"image_url",...}``). The Agent layer only sees
``chat(messages)`` and ``make_image_content(img)`` - backends are
swappable without touching agent code.
"""

from __future__ import annotations

import base64
import io
import os
from abc import ABC, abstractmethod
from typing import Any

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


DEFAULT_REQUEST_TIMEOUT = _env_float("OMNI_ARENA_API_TIMEOUT", 120.0)
DEFAULT_MAX_RETRIES = _env_int("OMNI_ARENA_API_MAX_RETRIES", 3)


class Backend(ABC):
    """Abstract VLM backend.

    Args:
        model: Model identifier passed in the request body.
        resize: Whether to thumbnail images before sending.
        resize_size: Max longest edge in px (aspect ratio preserved).
            ``<=0`` disables resizing entirely.
        temperature: Decoding temperature; ``None`` skips the field
            (some endpoints reject it).
    """

    def __init__(
        self,
        model: str,
        *,
        resize: bool = True,
        resize_size: int = 512,
        temperature: float | None = 0.3,
        request_timeout: float | None = None,
        max_retries: int | None = None,
    ):
        self.model = model
        self.resize = resize
        self.resize_size = resize_size
        self.temperature = temperature
        self.request_timeout = (
            DEFAULT_REQUEST_TIMEOUT
            if request_timeout is None
            else float(request_timeout)
        )
        self.max_retries = max(
            0,
            DEFAULT_MAX_RETRIES if max_retries is None else int(max_retries),
        )
        self.last_messages: list[dict] | None = None
        self.last_response_json: dict | None = None
        self.last_call_latency: Any | None = None
        self.last_decision_latency_s: float | None = None
        self.last_decision_latency_source: str | None = None
        self.last_latency_details: dict[str, Any] = {}
        self.lcrt_timing_enabled = False
        # Optional per-call API debug sink. When set (e.g. by a runner
        # script after constructing the backend), every chat / chat_with_tools
        # call writes a JSON file with the full request and response into
        # the logger's output directory. See utils.api_debug.ApiDebugLogger.
        self.debug_logger = None

    def enable_lcrt_timing(self, enabled: bool = True) -> None:
        """Ask the backend to collect model-side timing for LCRT runs."""
        self.lcrt_timing_enabled = bool(enabled)

    def _clear_latency_metadata(self) -> None:
        self.last_call_latency = None
        self.last_decision_latency_s = None
        self.last_decision_latency_source = None
        self.last_latency_details = {}

    def _set_decision_latency(
        self,
        latency_s: float | None,
        *,
        source: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if latency_s is None:
            self.last_decision_latency_s = None
        else:
            self.last_decision_latency_s = max(0.0, float(latency_s))
        self.last_decision_latency_source = source
        self.last_latency_details = details or {}

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send a multi-turn message list, return assistant text. ``""`` on failure."""

    @abstractmethod
    def make_image_content(self, img: Image.Image) -> dict:
        """Build the image content block for one image.

        Schemas differ across providers - the backend owns this choice.
        """

    # Shared helper: encode an image to base64 with optional thumbnail.
    def encode_image(self, img: Image.Image) -> str:
        img = img.convert("RGB")
        if self.resize and self.resize_size > 0 and max(img.size) > self.resize_size:
            img.thumbnail(
                (self.resize_size, self.resize_size), Image.Resampling.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
