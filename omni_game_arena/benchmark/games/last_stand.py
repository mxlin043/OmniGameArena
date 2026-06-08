"""LastStand GameSpec.

Platform survival arena: random platform areas fall away over time, and the
agent scores higher by staying alive longer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import GameSpec

_KEY_BINDINGS = {
    "W": "Move forward",
    "A": "Move left",
    "S": "Move backward",
    "D": "Move right",
    "Space": "Jump / dodge",
}



@dataclass
class LastStandSpec(GameSpec):
    name: str = "last_stand"
    prompt_key: str = "LastStand"
    default_task: str = (
        "Survive on the platform for as long as possible. Avoid falling off "
        "or into dropped areas."
    )
    mouse_axes: tuple[str, ...] = ("X", "Y")
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    chunk_steps: int = 6
    obs_delay: float = 0.0

    def extract_terminal_metrics(self, terminal_info: dict) -> dict:
        info = terminal_info or {}
        return {
            "score": info.get("score"),
            "survival_time": info.get("survival_time"),
        }



    def aggregate_episode_metrics(self, episode_metrics: list[dict]) -> dict:
        game_parts = [(m.get("game") or {}) for m in episode_metrics]
        scores = [g.get("score") for g in game_parts if g.get("score") is not None]
        survival_times = [
            g.get("survival_time")
            for g in game_parts
            if g.get("survival_time") is not None
        ]
        out = {}
        if scores:
            out["mean_score"] = round(sum(scores) / len(scores), 4)
        if survival_times:
            out["mean_survival_time"] = round(
                sum(survival_times) / len(survival_times), 4
            )
        return out




SPEC = LastStandSpec()
