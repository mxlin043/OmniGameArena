"""SharedFloor GameSpec.

Two-player cooperative top-down arena. Both players operate in the same
shared floor space and coordinate around one shared set of parts, stations,
and active orders.
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
    "F": "Interact (pick up part / submit at order counter / upgrade at WorkBench / discard)",
}


@dataclass
class SharedFloorSpec(GameSpec):
    name: str = "shared_floor"
    prompt_key: str = "SharedFloorPlayer1"
    default_task: str = (
        "Coordinate with the other player in a shared arena to complete "
        "active orders before the match ends."
    )
    mode: Literal["coop"] = "coop"
    num_agents: int = 2
    coop_score_aggregation: Literal["sum"] = "sum"
    map: str = "shared_floor"
    player_prompt_keys: tuple[str, ...] = (
        "SharedFloorPlayer1",
        "SharedFloorPlayer2",
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


SPEC = SharedFloorSpec()
