"""Four independently rendered sections of the VLM system prompt."""
from __future__ import annotations

from . import load_game_prompt
from .methods import MethodStyle, get_method


def render_game_description(game: str | None) -> str:
    return load_game_prompt(game) if game else ""


def render_controls(action_schema: dict) -> str:
    lines = []
    for key in ("key_bindings", "mouse_controls"):
        if action_schema.get(key):
            lines.append(action_schema[key])
    tap_keys = action_schema.get("tap_keys") or ()
    if lines and tap_keys:
        taps = ", ".join(f"`{key}`" for key in tap_keys)
        lines.append(
            f"Tap controls: {taps}. Each is clicked once near the end of every action "
            "step where it appears; repetitions in consecutive steps are separate "
            "clicks, not a held press. Other controls in a step are held together, "
            "then released before any tap. Repeating other controls across "
            "consecutive steps without taps keeps them held."
        )
    return "Available Controls\n" + "\n".join(lines) if lines else ""


def render_skill(prompt_skill: str | None) -> str:
    return "Gameplay Skill From Prior Runs\n" + prompt_skill if prompt_skill else ""


def render_output_format(method: MethodStyle | str, action_schema: dict) -> str:
    style = method if isinstance(method, MethodStyle) else get_method(method)
    return style.output_format(action_schema)
