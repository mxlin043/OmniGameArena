"""ObstacleRun2D GameSpec.

Terminal signals surfaced by the UE5 scene:
  - ``score``       : progress in [0,1]; 1.0 iff the runner crossed the
                      finish line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import GameSpec


_KEY_BINDINGS = {
    "D": "Move right",
    "A": "Move left",
    "Space": "Jump",
}


@dataclass
class ObstacleRun2DSpec(GameSpec):
    name: str = "obstacle_run_2d"
    prompt_key: str = "ObstacleRun2D"
    default_task: str = (
        "Run to the right, avoid obstacles and gaps, "
        "and reach the finish line safely."
    )
    # Side-scroller - mouse is not used.
    mouse_axes: tuple[str, ...] = ()
    # Only A / D / Space are meaningful - no W, no S, no mouse.
    # Use default_factory so the mutable dict isn't shared between
    # instances (dataclass guard against mutable defaults).
    key_bindings: dict = field(default_factory=lambda: dict(_KEY_BINDINGS))
    # Jumping chunk finishes while the character is still mid-air;
    # wait 1000ms so the next screenshot captures the landed pose.
    obs_delay: float = 1.0

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
        # score >= 0.999 means reached the finish line.
        successes = sum(1 for s in scores if s >= 0.999)
        return {
            "success_rate": round(successes / len(scores), 4),
            "mean_score": round(sum(scores) / len(scores), 4),
        }


SPEC = ObstacleRun2DSpec()
