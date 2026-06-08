"""MidlineClash GameSpec.

Two-player PvP CookHouse map, run as two parallel SoloEnv instances with one
RemoteInput port per player.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .base import GameSpec


_KEY_BINDINGS = {
    "W": "Move up",
    "A": "Move left",
    "S": "Move down",
    "D": "Move right",
    "F": "Interact (pick up weapon / submit at order counter / upgrade at WorkBench / discard)",
}


@dataclass
class MidlineClashSpec(GameSpec):
    name: str = "midline_clash"
    prompt_key: str = "MidlineClashPlayer1"
    default_task: str = (
        "Play as your assigned player in a two-player PvP arena match "
        "and outscore the opposing player before the match ends."
    )
    mode: Literal["pvp"] = "pvp"
    num_agents: int = 2
    map: str = "midline_clash"
    player_prompt_keys: tuple[str, ...] = (
        "MidlineClashPlayer1",
        "MidlineClashPlayer2",
    )
    # Fixed top-down CookHouse camera; no mouse input for the first pass.
    mouse_axes: tuple[str, ...] = ()
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("F",)
    chunk_steps: int = 6
    obs_delay: float = 0.5

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


SPEC = MidlineClashSpec()
