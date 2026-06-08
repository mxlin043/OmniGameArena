"""OpenP2P HTTP policy agent for the current multipart /predict API."""

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

_NOOP_ACTION = {"keys": [], "mouse_buttons": [], "mouse_delta_x": 0, "mouse_delta_y": 0}


def normalize_openp2p_url(value: str) -> str:
    """Accept a full URL or a bare host/IP and normalize to http://host:port."""
    raw = value.strip().rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(f"invalid OpenP2P URL: {value!r}")
    if parsed.scheme != "http":
        raise ValueError(
            f"OpenP2P /predict only supports http:// endpoints, got {parsed.scheme!r}"
        )

    netloc = parsed.netloc
    if parsed.port is None:
        netloc = f"{parsed.hostname}:8081"

    normalized = parsed._replace(netloc=netloc, path=parsed.path.rstrip("/"))
    return urlunparse(normalized).rstrip("/")


class OpenP2PAgent(BaseAgent):
    """Game-playing agent for the current OpenP2P deployment.

    This is the framework-native OpenP2P client used by the benchmark runner.
    It intentionally uses the newer API:

      - ``GET /health`` for reachability checks
      - ``POST /reset`` for optional policy-session reset
      - ``POST /predict?text=...`` with multipart field ``image``

    The benchmark runner is the maintained entry point for this deployment API.
    """

    def __init__(
        self,
        url: str | None = None,
        text: str | None = None,
        timeout: float = 180.0,
        health_timeout: float = 5.0,
        skip_health: bool = False,
        skip_reset: bool = False,
        image_format: str = "JPEG",
        image_quality: int = 85,
    ):
        self.url = normalize_openp2p_url(
            url or os.getenv("OMNI_P2P_URL", "http://127.0.0.1:8081")
        )
        self.text = text
        self.timeout = float(timeout)
        self.health_timeout = float(health_timeout)
        self.skip_health = bool(skip_health)
        self.skip_reset = bool(skip_reset)
        self.image_format = (image_format or "JPEG").upper()
        self.image_quality = int(image_quality)
        self._session = requests.Session()
        self.last_vlm_response: str | None = None
        self.last_action_log: str | None = None
        self.last_raw_response: Any | None = None
        self.last_predict_ms: float | None = None

    def act(self, obs: dict, task: str, action_schema: dict) -> dict:  # noqa: ARG002
        image: Image.Image = obs["image"]
        instruction = self.text if self.text is not None else task
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
            self.last_raw_response = raw
            action = self._parse_action(raw)
        except (requests.RequestException, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.last_predict_ms = elapsed_ms
            logger.error("OpenP2P predict failed after %.1fms: %s", elapsed_ms, exc)
            action = dict(_NOOP_ACTION)
            raw = {"error": str(exc)}
            self.last_raw_response = raw

        self.last_action_log = _format_action_log(
            predict_ms=self.last_predict_ms or 0.0,
            action=action,
            raw=raw,
        )
        # The benchmark viewer splits VLM responses into reason/action using
        # Lumine tags. OpenP2P has no reasoning, so place the compact policy
        # log in the action slot and leave the reason slot empty.
        self.last_vlm_response = (
            f"<|action_start|>{self.last_action_log}<|action_end|>"
        )
        logger.info(
            "OpenP2P [%dx%d, %.1fKB] predict=%.0fms | %s",
            image.width,
            image.height,
            len(image_bytes) / 1024,
            self.last_predict_ms or 0.0,
            action,
        )
        return action

    def reset(self) -> None:
        """Reset the policy server's episode state unless disabled in YAML."""
        self.last_vlm_response = None
        self.last_action_log = None
        self.last_raw_response = None
        self.last_predict_ms = None
        if self.skip_reset:
            logger.info("OpenP2P reset skipped for %s", self.url)
            return
        try:
            resp = self._session.post(f"{self.url}/reset", timeout=self.health_timeout)
            if resp.status_code >= 400:
                logger.warning(
                    "OpenP2P reset returned HTTP %s: %s",
                    resp.status_code,
                    _response_body(resp),
                )
            else:
                logger.info("OpenP2P reset ok for %s", self.url)
        except requests.RequestException as exc:
            logger.warning("OpenP2P reset failed for %s: %s", self.url, exc)

    def reset_history(self) -> None:
        self.reset()

    def health_check(self) -> bool:
        if self.skip_health:
            return True
        try:
            resp = self._session.get(f"{self.url}/health", timeout=self.health_timeout)
            if resp.status_code >= 400:
                logger.warning(
                    "OpenP2P health returned HTTP %s: %s",
                    resp.status_code,
                    _response_body(resp),
                )
            return resp.status_code < 400
        except requests.RequestException as exc:
            logger.warning("OpenP2P health failed for %s: %s", self.url, exc)
            return False

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
        payload = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else raw
        if not isinstance(payload, dict):
            return dict(_NOOP_ACTION)

        return {
            "keys": list(payload.get("keys") or []),
            "mouse_buttons": [str(v) for v in (payload.get("mouse_buttons") or [])],
            "mouse_delta_x": self._float(payload.get("mouse_delta_x", 0)),
            "mouse_delta_y": self._float(payload.get("mouse_delta_y", 0)),
        }

    @staticmethod
    def _float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def _response_body(resp: requests.Response, max_chars: int = 500) -> str:
    try:
        body = json.dumps(resp.json(), ensure_ascii=False)
    except ValueError:
        body = resp.text.strip()
    if len(body) > max_chars:
        return body[:max_chars] + "...<truncated>"
    return body or "<empty body>"


def _format_action_log(
    *,
    predict_ms: float,
    action: dict[str, Any],
    raw: Any,
) -> str:
    payload = {
        "predict_ms": round(predict_ms, 3),
        "action": action,
    }
    if isinstance(raw, dict) and raw.get("error"):
        payload["error"] = raw["error"]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
