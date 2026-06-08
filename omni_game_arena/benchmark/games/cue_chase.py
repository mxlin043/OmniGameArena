"""CueChase GameSpec.

Single-player NPC exchange task chain. The agent reads the current task,
uses NPC hints to find the right character, and exchanges items with NPCs
before the countdown ends.

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
    "F": "Interact with the NPC when the [Interaction] prompt is visible",
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


def _best_score(*scores) -> float | None:
    values = []
    for score in scores:
        if score is None:
            continue
        try:
            values.append(float(score))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


@dataclass
class CueChaseSpec(GameSpec):
    name: str = "cue_chase"
    prompt_key: str = "CueChase"
    default_task: str = (
        "Complete all 10 NPC exchange tasks before the countdown ends. Read "
        "the task text in the top-left corner, use the NPC hint to find the "
        "right NPC, and press F when the [Interaction] prompt is visible."
    )
    map: str = "cue_chase"
    # Third-person camera: horizontal and vertical look, no scroll.
    mouse_axes: tuple[str, ...] = ("X", "Y")
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("F",)
    chunk_steps: int = 8
    obs_delay: float = 0.5

    def extract_terminal_metrics(self, terminal_info: dict) -> dict:
        info = terminal_info or {}
        final_score = info.get("score")
        max_score_seen = info.get("max_score_seen")
        score = _best_score(final_score, max_score_seen)
        return {
            "score": score,
            "final_score": final_score,
            "max_score_seen": max_score_seen,
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


SPEC = CueChaseSpec()
