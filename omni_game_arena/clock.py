"""Evaluation clock names and the shared, strict LCM timing contract."""
from __future__ import annotations

import math


CLOCK_MODES = ("realtime", "lfm", "lcm")
SERVER_INFERENCE_SOURCES = frozenset({
    "usage.latency_checkpoint.engine_ttlt_ms",
    "header.x-amzn-bedrock-invocation-latency",
})


class LCMLatencyError(ValueError):
    """The current decision cannot be charged as server-side inference time."""


def normalize_clock_mode(value: str | None) -> str:
    mode = (value or "realtime").strip().lower()
    if mode not in CLOCK_MODES:
        raise ValueError(f"Unsupported clock mode {value!r}; expected realtime, lfm, or lcm")
    return mode


def finite_nonnegative(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def require_server_inference_seconds(value, source: str | None) -> float:
    seconds = finite_nonnegative(value)
    if seconds is None or not isinstance(source, str) or source not in SERVER_INFERENCE_SOURCES:
        raise LCMLatencyError(
            "LCM requires finite, nonnegative server-reported model inference time "
            "for every response. Missing/invalid timing or an unsupported source "
            f"({source!r}) cannot be replaced by wall-clock or network estimates. "
            "Use --clock-mode lfm (--clock lfm for run_cold_start.py), or a "
            "backend that reports a supported inference-time field."
        )
    return seconds


def decision_latency(agent, clock_mode: str) -> tuple[float, dict]:
    """Validate the full decision's timing before any game-time advancement."""
    if normalize_clock_mode(clock_mode) != "lcm":
        return 0.0, {"lcm_decision_delay_source": "none"}
    # If an agent tracks timing, its value is authoritative: falling through to
    # the backend could hide an unmeasured call in an agent's multi-call decision.
    owner = agent if hasattr(agent, "last_decision_latency_s") else getattr(agent, "backend", None)
    value = getattr(owner, "last_decision_latency_s", None)
    source = getattr(owner, "last_decision_latency_source", None)
    details = getattr(owner, "last_latency_details", None) or {}
    if source == "sum.server_inference":
        calls = details.get("inference_calls") if isinstance(details, dict) else None
        if not isinstance(calls, list) or not calls:
            raise LCMLatencyError("LCM inference-time sum is missing its per-response evidence")
        if not all(isinstance(call, dict) for call in calls):
            raise LCMLatencyError("LCM inference-time evidence must contain per-response records")
        total = sum(require_server_inference_seconds(call.get("seconds"), call.get("source")) for call in calls)
        seconds = finite_nonnegative(value)
        if seconds is None or not math.isclose(seconds, total, rel_tol=1e-9, abs_tol=1e-9):
            raise LCMLatencyError("LCM inference-time sum does not match its per-response evidence")
    else:
        seconds = require_server_inference_seconds(value, source)
    metadata = {"lcm_decision_delay_source": source, "lcm_decision_delay_unit": "seconds"}
    if isinstance(details, dict) and details:
        metadata["lcm_decision_delay_details"] = details
    return seconds, metadata
