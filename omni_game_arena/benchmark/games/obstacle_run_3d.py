"""ObstacleRun3D GameSpec.

Terminal signals surfaced by the UE5 scene:
  - ``score``       : progress along the track in [0,1]; 1.0 iff the
                      runner crossed the finish line.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import GameSpec


_FINISH_THRESHOLD = 0.999


@dataclass
class ObstacleRun3DSpec(GameSpec):
    name: str = "obstacle_run_3d"
    prompt_key: str = "ObstacleRun3D"
    default_task: str = (
        "Run through the 3D obstacle course as fast as possible. "
        "Avoid obstacles, jump over gaps, and reach the finish line."
    )
    # Camera rotates both horizontally and vertically - no scroll.
    mouse_axes: tuple[str, ...] = ("X", "Y")

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
        successes = sum(1 for s in scores if s >= _FINISH_THRESHOLD)
        return {
            "success_rate": round(successes / len(scores), 4),
            "mean_score": round(sum(scores) / len(scores), 4),
        }


SPEC = ObstacleRun3DSpec()
