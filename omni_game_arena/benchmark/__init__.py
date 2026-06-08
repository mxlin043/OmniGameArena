"""Game-agnostic benchmark runner for Omni Game Arena.

Entry point: ``scripts/run_benchmark.py --config configs/<game>/<cfg>.yaml``.

The YAML declares ``game: <name>`` at the top level; each game ships a
``GameSpec`` in ``omni_game_arena.benchmark.games`` that plugs in the
game-specific bits (prompt key, terminal-info extraction, metric
aggregation) while the rest of the pipeline stays shared.
"""

from .config import (
    ParamsPoint,
    AgentProfile,
    EnvSpec,
    Experiment,
    expand_experiments,
    load_benchmark_config,
)
from .games import GameSpec, get_game, list_games
from .runner import run_benchmark, run_one_experiment

__all__ = [
    "ParamsPoint",
    "AgentProfile",
    "EnvSpec",
    "Experiment",
    "GameSpec",
    "expand_experiments",
    "get_game",
    "list_games",
    "load_benchmark_config",
    "run_benchmark",
    "run_one_experiment",
]
