"""HandoffRun GameSpec.

Two-player asymmetric cooperative CookHouse map. Player 1 supplies food from
the left side, and Player 2 receives it through transfer points, upgrades
when needed, then submits orders on the right side.
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
    "F": "Interact (pick up item / place or pick up at transfer point / submit / upgrade / discard)",
}


@dataclass
class HandoffRunSpec(GameSpec):
    name: str = "handoff_run"
    prompt_key: str = "HandoffRunPlayer1"
    default_task: str = (
        "Coordinate with the other player in an asymmetric kitchen arena: "
        "Player 1 supplies ordered food items through transfer points, and "
        "Player 2 receives, upgrades when needed, and submits orders before "
        "the match ends."
    )
    mode: Literal["coop"] = "coop"
    num_agents: int = 2
    coop_score_aggregation: Literal["sum"] = "sum"
    map: str = "handoff_run"
    player_prompt_keys: tuple[str, ...] = (
        "HandoffRunPlayer1",
        "HandoffRunPlayer2",
    )
    # Fixed top-down camera; no mouse input.
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


SPEC = HandoffRunSpec()
