"""Step trajectory recorder - saves screenshots and action logs."""

import json
import os
import logging
import threading

logger = logging.getLogger(__name__)


def _fmt_num(v) -> str:
    """Render a numeric value without trailing ``.0`` - keeps the compact
    action string short (``0`` instead of ``0.0``).
    """
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def compact_action(action) -> str | None:
    """Render a chunked-adapter action dict as the one-line string the
    VLM originally emitted, e.g. ``"D ; D ; D Space ; D ; D ; D ; D ; D"``.

    When parser metadata includes ``mouse_axes``, exactly that many
    VLM-facing mouse values are emitted, even when all are zero. Without
    that metadata, the legacy fallback emits a mouse prefix only when at
    least one axis is non-zero.
    Non-dict actions (or unknown shapes) are ``str()``-ified. ``None``
    passes through as ``None``.
    """
    if action is None:
        return None
    if not isinstance(action, dict):
        return str(action)

    parts: list[str] = []
    mouse = action.get("mouse_output", action.get("mouse"))
    mouse_axes = action.get("mouse_axes")
    if mouse_axes is not None:
        axis_count = len(mouse_axes)
        if mouse is not None and axis_count > 0:
            parts.append(" ".join(_fmt_num(v) for v in mouse[:axis_count]))
    else:
        mouse = mouse or ()
        if mouse and any(v != 0 for v in mouse):
            parts.append(" ".join(_fmt_num(v) for v in mouse))

    steps = action.get("steps") or []
    for step_keys in steps:
        if step_keys:
            parts.append(" ".join(step_keys))
        else:
            parts.append("")

    if not parts:
        # Fall back to JSON so unusual shapes aren't silently dropped.
        return json.dumps(action, ensure_ascii=False)
    return " ; ".join(parts)


class StepRecorder:
    """Records each step's observation, action, and metadata.

    Files land directly under ``output_dir`` (the runner already
    supplies a timestamped path). Each ``step_NNNN`` image
    is the observation the agent saw while choosing that same step's
    ``action``. The JSON ``done`` field means game over was observed
    after executing that action.
    ``observation_timestamp`` is the observation/screenshot capture time,
    not the decision time or action start time.
    The terminal game-over observation is saved separately as
    ``terminal_observation.jpg`` so it does not break the step/action
    alignment.

    The stored ``action`` is a compact one-line string matching the
    VLM's own chunked output format. Mouse prefixes are preserved using
    parser metadata, so a two-axis ``0 0`` prefix stays ``0 0`` instead
    of being dropped or expanded. Reward is not stored: this benchmark
    computes reward/metrics externally (see ``CLAUDE.md``), and
    ``env.step()`` only returns a placeholder.
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def record(
        self,
        step: int,
        obs: dict,
        action: dict | None,
        done: bool,
        info: dict,
    ):
        """Record a single step.

        ``obs`` is the observation used to choose ``action``. Records
        without an executed action are skipped; if a terminal state is
        detected before a newly proposed action can run, the previous
        executed action is marked as ``done`` instead.
        """
        with self._lock:
            if action is None:
                return

            raw_info = info or {}
            if "done_reason" in raw_info:
                record_done = raw_info.get("done_reason") == "game_over"
            else:
                record_done = bool(done)
            info_out = {
                k: v for k, v in raw_info.items()
                if k not in {"action", "done_reason", "terminal_timing"}
            }
            if "act_latency_s" in info_out:
                info_out["wall_decision_time_s"] = info_out.pop("act_latency_s")
            if "decision_latency_s" in info_out:
                info_out["lcrt_decision_delay_s"] = info_out.pop("decision_latency_s")
            if "decision_latency_source" in info_out:
                info_out["lcrt_decision_delay_source"] = info_out.pop(
                    "decision_latency_source"
                )
            if "decision_latency_details" in info_out:
                info_out["lcrt_decision_delay_details"] = info_out.pop(
                    "decision_latency_details"
                )
            action_executed = info_out.get("action_executed", True) is not False
            if not action_executed:
                # The world may have ended while the decision was in flight.
                # That proposed response was never applied, so don't append a
                # new step. Attribute the terminal state to the last real action.
                if self.records and record_done:
                    self.records[-1]["done"] = True
                return

            image = obs.get("image")
            if image is not None:
                img_path = os.path.join(self.output_dir, f"step_{step:04d}.jpg")
                image.save(img_path, "JPEG", quality=85)

            self.records.append({
                "step": step,
                "action": compact_action(action) if action_executed else None,
                "done": record_done,
                "info": info_out,
                # Capture time for the decision image saved as step_NNNN.jpg.
                "observation_timestamp": obs.get("timestamp", 0),
                "width": obs.get("width", 0),
                "height": obs.get("height", 0),
            })

    def snapshot_records(self) -> list[dict]:
        """Return a stable copy of records for cross-thread finalization."""
        with self._lock:
            return list(self.records)

    def record_terminal_observation(self, obs: dict):
        """Save the final game-over observation outside the step sequence."""
        with self._lock:
            image = obs.get("image")
            if image is None:
                return
            img_path = os.path.join(self.output_dir, "terminal_observation.jpg")
            image.save(img_path, "JPEG", quality=85)

    def save_summary(self, records: list[dict] | None = None):
        """Save JSON summary of all steps."""
        if records is None:
            records = self.snapshot_records()
        summary_path = os.path.join(self.output_dir, "summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        logger.info("Summary saved: %s (%d steps)", summary_path, len(records))
