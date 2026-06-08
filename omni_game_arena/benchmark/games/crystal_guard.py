"""CrystalGuard GameSpec.

Two-player BaseAssault map. Each player fires baseball projectiles from a
crosshair view and tries to destroy the opponent crystal while defending their
own crystal.
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
    "LMB": "Shoot a baseball projectile",
}


@dataclass
class CrystalGuardSpec(GameSpec):
    name: str = "crystal_guard"
    prompt_key: str = "CrystalGuardPlayer1"
    default_task: str = (
        "Destroy the opposing crystal in BaseAssault while defending your "
        "own crystal. Aim with the crosshair and shoot baseball projectiles."
    )
    mode: Literal["pvp"] = "pvp"
    num_agents: int = 2
    map: str = "crystal_guard"
    player_prompt_keys: tuple[str, ...] = (
        "CrystalGuardPlayer1",
        "CrystalGuardPlayer2",
    )
    # Crosshair shooter: horizontal + vertical aim, no scroll.
    mouse_axes: tuple[str, ...] = ("X", "Y")
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("LMB",)
    chunk_steps: int = 6
    obs_delay: float = 0.1

    def extract_terminal_metrics(self, terminal_info: dict) -> dict:
        info = terminal_info or {}
        metrics = {
            "score": info.get("score"),
        }
        for key in (
            "winner",
            "team1_crystal_hp",
            "team2_crystal_hp",
            "team1_player_hp",
            "team2_player_hp",
        ):
            if key in info:
                metrics[key] = info.get(key)
        return metrics

    def aggregate_episode_metrics(self, episode_metrics: list[dict]) -> dict:
        game_parts = [(m.get("game") or {}) for m in episode_metrics]
        scores = [g.get("score") for g in game_parts if g.get("score") is not None]
        if not scores:
            return {}
        return {
            "mean_score": round(sum(float(s) for s in scores) / len(scores), 4),
        }


SPEC = CrystalGuardSpec()
