"""Registry of benchmark GameSpecs.

Add a new game by importing its ``SPEC`` and registering it below.
"""

from __future__ import annotations

from .base import GameSpec
from .crystal_guard import SPEC as _CRYSTAL_GUARD
from .cue_chase import SPEC as _CUE_CHASE
from .handoff_run import SPEC as _HANDOFF_RUN
from .last_stand import SPEC as _LAST_STAND
from .midline_clash import SPEC as _MIDLINE_CLASH
from .monster_shoot import SPEC as _MONSTER_SHOOT
from .obstacle_run_2d import SPEC as _OBSTACLE_RUN_2D
from .obstacle_run_3d import SPEC as _OBSTACLE_RUN_3D
from .scene_escape import SPEC as _SCENE_ESCAPE
from .shared_floor import SPEC as _SHARED_FLOOR
from .sky_duel import SPEC as _SKY_DUEL
from .solo_craft import SPEC as _SOLO_CRAFT

REGISTRY: dict[str, GameSpec] = {
    _CRYSTAL_GUARD.name: _CRYSTAL_GUARD,
    _CUE_CHASE.name: _CUE_CHASE,
    _HANDOFF_RUN.name: _HANDOFF_RUN,
    _LAST_STAND.name: _LAST_STAND,
    _MIDLINE_CLASH.name: _MIDLINE_CLASH,
    _MONSTER_SHOOT.name: _MONSTER_SHOOT,
    _OBSTACLE_RUN_2D.name: _OBSTACLE_RUN_2D,
    _OBSTACLE_RUN_3D.name: _OBSTACLE_RUN_3D,
    _SCENE_ESCAPE.name: _SCENE_ESCAPE,
    _SHARED_FLOOR.name: _SHARED_FLOOR,
    _SKY_DUEL.name: _SKY_DUEL,
    _SOLO_CRAFT.name: _SOLO_CRAFT,
}


def get_game(name: str) -> GameSpec:
    if name not in REGISTRY:
        avail = ", ".join(sorted(REGISTRY.keys()))
        raise ValueError(
            f"Unknown game {name!r}. Registered games: {avail}"
        )
    return REGISTRY[name]


def list_games() -> list[str]:
    return sorted(REGISTRY.keys())


__all__ = ["GameSpec", "REGISTRY", "get_game", "list_games"]
