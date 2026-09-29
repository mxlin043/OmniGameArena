"""Per-game prompt loader.

Game-specific description texts live under
``omni_game_arena/prompts/games/<game>.txt``. The loader resolves the path and
returns the raw text. Only game facts, no strategy tips.
"""

import os

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games")


def load_game_prompt(game: str) -> str:
    """Load game-specific prompt text.

    Args:
        game: Game name (e.g. "monster_shoot"). Also accepts
              CamelCase ("MonsterShoot") which is auto-converted.

    Returns:
        Prompt text. A missing description file is an error; a variant
        without its own description names the original game's file in its
        config instead.
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

    # Split the suffix in names such as ObstacleRun2DVAR2 while retaining 2d/3d.
    compact_variant = re.sub(r"([1-4])player", r"\1_player", compact_player)
    compact_variant = re.sub(r"_player_(\d+)$", r"_player\1", compact_variant)
    compact_variant = re.sub(r"([23]d)(var)(?=_?[1-4](?:_|$))", r"\1_\2", compact_variant)
    compact_variant = re.sub(r"_(var)_([1-4])(?=_|$)", r"_\1\2", compact_variant)
    if compact_variant not in candidates:
        candidates.append(compact_variant)

    lower = game.lower()
    if lower not in candidates:
        candidates.append(lower)

    # Bare or CamelCase variant names also resolve descriptions in variants/.
    # Explicit paths above retain priority; absent descriptions still fall back.
    for candidate in list(candidates):
        if not candidate.startswith("variants/") and re.fullmatch(r".+_var_?[1-4](_player_?[12])?", candidate):
            candidates.append("variants/" + candidate)

    path = ""
    for candidate in candidates:
        candidate_path = os.path.join(_PROMPTS_DIR, f"{candidate}.txt")
        if os.path.exists(candidate_path):
            path = candidate_path
            break

    if not path:
        raise FileNotFoundError(
            f"No game prompt for {game!r}: expected "
            f"{os.path.join(_PROMPTS_DIR, snake + '.txt')}"
        )

    with open(path, encoding="utf-8") as f:
        return f.read().strip()
