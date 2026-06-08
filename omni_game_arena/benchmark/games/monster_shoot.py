"""MonsterShoot GameSpec.

First-person shooter arena - the first game to expose a mouse button (LMB)
in the VLM action space. ``key_bindings`` here sets the vocabulary every
FPS game shares going forward: W/A/S/D/Space + LMB.

Terminal signals surfaced by the UE5 scene:
  - ``score``       : final score accumulated (per-kill points).
  - ``done_reason`` : one of ``"timeout"``, ``"max_steps"``, ``"agent"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import GameSpec


_KEY_BINDINGS = {
    "W": "Move forward",
    "A": "Move left",
    "S": "Move backward",
    "D": "Move right",
    "Space": "Jump",
    "LMB": "Fire weapon",
}


@dataclass
class MonsterShootSpec(GameSpec):
    name: str = "monster_shoot"
    prompt_key: str = "MonsterShoot"
    default_task: str = (
        "Eliminate all 10 monsters in the arena before the countdown ends "
        "while keeping your HP as high as possible."
    )
    map: str = "monster_shoot"
    # FPS camera: horizontal + vertical aim, no scroll.
    mouse_axes: tuple[str, ...] = ("X", "Y")
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))

    def extract_terminal_metrics(self, terminal_info: dict) -> dict:
        info = terminal_info or {}
        return {
            "score": info.get("score"),
        }

    def aggregate_episode_metrics(self, episode_metrics: list[dict]) -> dict:
        game_parts = [(m.get("game") or {}) for m in episode_metrics]
        scores = [g.get("score") for g in game_parts if g.get("score") is not None]
        if not scores:
            return {}
        return {
            "mean_score": round(sum(scores) / len(scores), 4),
        }


SPEC = MonsterShootSpec()
