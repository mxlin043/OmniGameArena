"""SceneEscape GameSpec.

Third-person task-chain scene hosted by the CueChase UE5 map. The agent
must read the active on-screen task, find the corresponding object, and
interact with it before the countdown ends.

Terminal signals surfaced by the UE5 scene:
  - ``score``       : expected to represent task progress / completed tasks.
  - ``done_reason`` : one of ``"game_over"``, ``"max_steps"``, ``"agent"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import GameSpec


_TASK_COUNT = 10

_KEY_BINDINGS = {
    "W": "Move forward",
    "A": "Move left",
    "S": "Move backward",
    "D": "Move right",
    "F": (
        "Interact with the highlighted task object when the yellow "
        "[Interact] prompt is visible"
    ),
    "Space": "Jump",
}


def _as_progress(score) -> float | None:
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value <= 1.0:
        return max(0.0, min(1.0, value))
    return max(0.0, min(1.0, value / _TASK_COUNT))


@dataclass
class SceneEscapeSpec(GameSpec):
    name: str = "scene_escape"
    prompt_key: str = "SceneEscape"
    default_task: str = (
        "Complete all 10 on-screen tasks before the countdown ends. Read the "
        "current task in the top-left corner of the screen, find the matching "
        "object, and press F only when the yellow [Interact] prompt for that "
        "object is visible."
    )
    map: str = "scene_escape"
    # Third-person camera: horizontal + vertical rotation (no scroll).
    mouse_axes: tuple[str, ...] = ("X", "Y")
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("F",)
    chunk_steps: int = 6
    obs_delay: float = 0.5

    def extract_terminal_metrics(self, terminal_info: dict) -> dict:
        info = terminal_info or {}
        score = info.get("score")
        return {
            "score": score,
            "completion_rate": _as_progress(score),
        }

    def aggregate_episode_metrics(self, episode_metrics: list[dict]) -> dict:
        game_parts = [(m.get("game") or {}) for m in episode_metrics]
        scores = []
        for g in game_parts:
            if g.get("score") is None:
                continue
            try:
                scores.append(float(g["score"]))
            except (TypeError, ValueError):
                continue
        progress = [
            g.get("completion_rate")
            for g in game_parts
            if g.get("completion_rate") is not None
        ]
        out = {}
        if scores:
            out["mean_score"] = round(sum(scores) / len(scores), 4)
        if progress:
            out["mean_completion_rate"] = round(sum(progress) / len(progress), 4)
            out["success_rate"] = round(
                sum(1 for p in progress if p >= 0.999) / len(progress),
                4,
            )
        return out


SPEC = SceneEscapeSpec()
