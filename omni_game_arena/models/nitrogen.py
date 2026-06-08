"""NitroGen HTTP policy agent for the current single-image /predict API."""

from __future__ import annotations

import io
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from PIL import Image

from .base import BaseAgent

logger = logging.getLogger(__name__)

BUTTON_PRESS_THRES = 0.5
_MENU_BUTTONS = {"START", "GUIDE", "BACK"}

_DEFAULT_BUTTON_NAMES = [
    "SOUTH",
    "EAST",
    "WEST",
    "NORTH",
    "LEFT_SHOULDER",
    "RIGHT_SHOULDER",
    "LEFT_TRIGGER",
    "RIGHT_TRIGGER",
    "LEFT_THUMB",
    "RIGHT_THUMB",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
    "START",
    "BACK",
    "GUIDE",
]


def _noop_action() -> dict:
    return {"j_left": [0.0, 0.0], "j_right": [0.0, 0.0], "buttons": {}}


def normalize_nitrogen_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(f"invalid NitroGen URL: {value!r}")
    if parsed.scheme != "http":
        raise ValueError(
            f"NitroGen /predict only supports http:// endpoints, got {parsed.scheme!r}"
        )

    netloc = parsed.netloc
    if parsed.port is None:
        netloc = f"{parsed.hostname}:8081"

    normalized = parsed._replace(netloc=netloc, path=parsed.path.rstrip("/"))
    return urlunparse(normalized).rstrip("/")


class NitroGenAgent(BaseAgent):
    """Gamepad policy agent for NitroGen's current single-image deployment.

    The legacy framework path expected ``POST /generate`` with base64 JSON and
    an action horizon. The current deployment is stateless: every call sees
    only the current image and returns one gamepad action. Therefore ``reset``
    only clears local bookkeeping and never calls a server reset endpoint.
    """

    def __init__(
        self,
        url: str | None = None,
        text: str | None = None,
        timeout: float = 180.0,
        health_timeout: float = 5.0,
        image_format: str = "JPEG",
        image_quality: int = 85,
        allow_menu: bool = False,
        step_interval: float = 0.0,
        action_downsample_ratio: int = 1,
    ):
        self.url = normalize_nitrogen_url(
            url or os.getenv("OMNI_NITROGEN_URL", "http://127.0.0.1:8081")
        )
        self.text = text
        self.timeout = float(timeout)
        self.health_timeout = float(health_timeout)
        self.image_format = (image_format or "JPEG").upper()
        self.image_quality = int(image_quality)
        self.allow_menu = bool(allow_menu)
        self.step_interval = float(step_interval or 0.0)
        self.action_downsample_ratio = max(1, int(action_downsample_ratio or 1))
        self._session = requests.Session()
        self._last_step_time = 0.0
        self._repeat_action: dict | None = None
        self._repeat_remaining = 0
        self.last_vlm_response: str | None = None
        self.last_action_log: str | None = None
        self.last_raw_response: Any | None = None
        self.last_predict_ms: float | None = None

    def act(self, obs: dict, task: str, action_schema: dict) -> dict:  # noqa: ARG002
        self._pace()

        if self._repeat_remaining > 0 and self._repeat_action is not None:
            self._repeat_remaining -= 1
            action = self._repeat_action
            self._set_action_log(0.0, action, {"source": "repeat"})
            return action

        image: Image.Image = obs["image"]
        instruction = self.text
        image_bytes, filename, mime_type = self._encode_image(image)
        files = {"image": (filename, image_bytes, mime_type)}
        params = {"text": instruction} if instruction else None

        t0 = time.perf_counter()
        try:
            resp = self._session.post(
                f"{self.url}/predict",
                params=params,
                files=files,
                timeout=self.timeout,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.last_predict_ms = elapsed_ms
            resp.raise_for_status()
            raw = resp.json()
            action = self._parse_action(raw)
            self.last_raw_response = raw
        except (requests.RequestException, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.last_predict_ms = elapsed_ms
            logger.error("NitroGen predict failed after %.1fms: %s", elapsed_ms, exc)
            raw = {"error": str(exc)}
            action = _noop_action()
            self.last_raw_response = raw

        self._repeat_action = action
        self._repeat_remaining = self.action_downsample_ratio - 1
        self._set_action_log(self.last_predict_ms or 0.0, action, raw)
        logger.info(
            "NitroGen [%dx%d, %.1fKB] predict=%.0fms | %s",
            image.width,
            image.height,
            len(image_bytes) / 1024,
            self.last_predict_ms or 0.0,
            action,
        )
        return action

    def reset(self) -> None:
        """Clear local pacing/repeat state. NitroGen server is stateless."""
        self._last_step_time = 0.0
        self._repeat_action = None
        self._repeat_remaining = 0
        self.last_vlm_response = None
        self.last_action_log = None
        self.last_raw_response = None
        self.last_predict_ms = None
        logger.info("NitroGen local state reset; server reset intentionally skipped")

    def reset_history(self) -> None:
        self.reset()

    def health_check(self) -> bool:
        try:
            resp = self._session.get(f"{self.url}/health", timeout=self.health_timeout)
            if resp.status_code >= 400:
                logger.warning(
                    "NitroGen health returned HTTP %s: %s",
                    resp.status_code,
                    _response_body(resp),
                )
            return resp.status_code < 400
        except requests.RequestException as exc:
            logger.warning("NitroGen health failed for %s: %s", self.url, exc)
            return False

    def _pace(self) -> None:
        if self.step_interval <= 0:
            return
        now = time.perf_counter()
        if self._last_step_time > 0:
            sleep_s = self.step_interval - (now - self._last_step_time)
            if sleep_s > 0:
                time.sleep(sleep_s)
        self._last_step_time = time.perf_counter()

    def _encode_image(self, image: Image.Image) -> tuple[bytes, str, str]:
        fmt = self.image_format
        if fmt not in {"JPEG", "JPG", "PNG"}:
            fmt = "JPEG"
        if fmt == "JPG":
            fmt = "JPEG"

        buf = io.BytesIO()
        if fmt == "PNG":
            image.save(buf, format="PNG")
            return buf.getvalue(), "ue5_screenshot.png", "image/png"

        image.convert("RGB").save(buf, format="JPEG", quality=self.image_quality)
        return buf.getvalue(), "ue5_screenshot.jpg", "image/jpeg"

    def _parse_action(self, raw: Any) -> dict:
        payload = raw
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            payload = payload["result"]
        if isinstance(payload, dict) and isinstance(payload.get("action"), dict):
            payload = payload["action"]
        if not isinstance(payload, dict):
            return _noop_action()

        j_left = payload.get("j_left", payload.get("left_stick", [0, 0]))
        j_right = payload.get("j_right", payload.get("right_stick", [0, 0]))
        j_left = _first_frame(j_left, default=[0, 0])
        j_right = _first_frame(j_right, default=[0, 0])

        raw_buttons = payload.get("buttons", {})
        raw_buttons = _first_frame(raw_buttons, default={})
        button_names = payload.get("button_names") or _DEFAULT_BUTTON_NAMES
        buttons = self._parse_buttons(raw_buttons, button_names)
        if not self.allow_menu:
            for name in _MENU_BUTTONS:
                buttons[name] = 0

        return {
            "j_left": self._pair(j_left),
            "j_right": self._pair(j_right),
            "buttons": buttons,
        }

    def _parse_buttons(self, raw_buttons, button_names: list[str]) -> dict:
        if isinstance(raw_buttons, dict):
            items = raw_buttons.items()
        else:
            items = zip(button_names, raw_buttons or [])

        out = {}
        for name, val in items:
            name = str(name)
            try:
                value = float(val)
            except (TypeError, ValueError):
                value = 0.0
            if "TRIGGER" in name:
                out[name] = value
            else:
                out[name] = 1 if value > BUTTON_PRESS_THRES else 0
        return out

    def _set_action_log(self, predict_ms: float, action: dict, raw: Any) -> None:
        self.last_action_log = _format_action_log(
            predict_ms=predict_ms,
            action=action,
            raw=raw,
        )
        self.last_vlm_response = (
            f"<|action_start|>{self.last_action_log}<|action_end|>"
        )

    @staticmethod
    def _pair(values) -> list[float]:
        try:
            return [float(values[0]), float(values[1])]
        except (TypeError, ValueError, IndexError):
            return [0.0, 0.0]


def _first_frame(value, *, default):
    """Accept a single action or a horizon and return the first frame."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, (list, tuple, dict)):
            return first
    return value


def _format_action_log(*, predict_ms: float, action: dict, raw: Any) -> str:
    payload = {
        "predict_ms": round(predict_ms, 3),
        "action": action,
    }
    if isinstance(raw, dict) and raw.get("error"):
        payload["error"] = raw["error"]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _response_body(resp: requests.Response, max_chars: int = 500) -> str:
    try:
        body = json.dumps(resp.json(), ensure_ascii=False)
    except ValueError:
        body = resp.text.strip()
    if len(body) > max_chars:
        return body[:max_chars] + "...<truncated>"
    return body or "<empty body>"
