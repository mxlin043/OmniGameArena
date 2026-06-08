"""SkyDuel GameSpec.

Two-player third-person duel on a floating arena. Each player controls one
fighter from their own viewpoint and tries to reduce the opponent's HP to
zero while avoiding the platform edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .base import GameSpec


_KEY_BINDINGS = {
    "W": "Move forward",
    "A": "Move left",
    "S": "Move backward",
    "D": "Move right",
    "Space": "Jump",
    "LMB": "Attack",
}


@dataclass
class SkyDuelSpec(GameSpec):
    name: str = "sky_duel"
    prompt_key: str = "SkyDuelPlayer1"
    default_task: str = (
        "Defeat the opposing player in a two-player sky duel. Keep your "
        "fighter on the platform, face the opponent, and attack when close "
        "enough to hit."
    )
    mode: Literal["pvp"] = "pvp"
    num_agents: int = 2
    map: str = "sky_duel"
    player_prompt_keys: tuple[str, ...] = (
        "SkyDuelPlayer1",
        "SkyDuelPlayer2",
    )
    # Third-person camera: yaw-only rotation, no vertical look/scroll.
    mouse_axes: tuple[str, ...] = ("X",)
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("LMB",)
    chunk_steps: int = 6
    obs_delay: float = 0.1

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
            "mean_score": round(sum(float(s) for s in scores) / len(scores), 4),
        }


SPEC = SkyDuelSpec()
