"""Per-game prompt loader.

Game-specific description texts live under
``omni_game_arena/prompts/games/<game>.txt``. The loader resolves the path and
returns the raw text. Only game facts, no strategy tips.
"""

import os
import logging

logger = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games")


def load_game_prompt(game: str) -> str:
    """Load game-specific prompt text.

    Args:
        game: Game name (e.g. "monster_shoot"). Also accepts
              CamelCase ("MonsterShoot") which is auto-converted.

    Returns:
        Prompt text, or empty string if file not found.
    """
    import re
    snake = re.sub(r'([a-z])([A-Z])', r'\1_\2', game)
    snake = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', snake)
    snake = re.sub(r'([a-zA-Z])(\d)', r'\1_\2', snake)
    snake = snake.lower()

    candidates = [snake]

    # Human-facing player prompts are easier to read as player1/player2,
    # while older files used player_0/player_1. Try both conventions.
    compact_player = re.sub(r"_player_(\d+)$", r"_player\1", snake)
    if compact_player not in candidates:
        candidates.append(compact_player)

    lower = game.lower()
    if lower not in candidates:
        candidates.append(lower)

    path = ""
    for candidate in candidates:
        candidate_path = os.path.join(_PROMPTS_DIR, f"{candidate}.txt")
        if os.path.exists(candidate_path):
            path = candidate_path
            break

    if not path:
        logger.warning(
            "No game prompt found: %s",
            os.path.join(_PROMPTS_DIR, f"{snake}.txt"),
        )
        return ""

    with open(path, encoding="utf-8") as f:
        return f.read().strip()
