"""SoloCraft GameSpec.

Top-down 3D cookhouse-style arena: pick up parts, optionally process them
at the workbench, then submit them at the order counter for points within a
fixed match timer.

Terminal signals surfaced by the UE5 scene:
  - ``score``       : final score the player accumulated.
  - ``done_reason`` : one of ``"timeout"``, ``"max_steps"``, ``"agent"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import GameSpec


_KEY_BINDINGS = {
    "W": "Move up",
    "A": "Move left",
    "S": "Move down",
    "D": "Move right",
    "F": "Interact (pick up item / submit at order counter / process at workbench / discard)",
}


@dataclass
class SoloCraftSpec(GameSpec):
    name: str = "solo_craft"
    prompt_key: str = "SoloCraft"
    default_task: str = (
        "Collect parts scattered around the arena and submit ones that match "
        "the active order at the order counter to maximize score before the "
        "match timer runs out. For high-grade orders, process the matching "
        "part at the workbench before submitting it."
    )
    # Fixed top-down camera, no mouse input.
    mouse_axes: tuple[str, ...] = ()
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    tap_keys: tuple[str, ...] = ("F",)
    chunk_steps: int = 6
    # Pickup / submit / upgrade animations need a moment to settle so the
    # next screenshot reflects the post-interaction inventory state.
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


SPEC = SoloCraftSpec()
