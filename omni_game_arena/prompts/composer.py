"""Prompt composer for VLM system prompts.

Fixed order:

    1. Map description   ← GameSpec / load_game_prompt(<game>.txt)
    2. Key list          ← adapter.action_schema["key_bindings"]
    3. Output format     ← MethodStyle.output_format()

The ``task`` field is intentionally NOT appended to VLM prompts. Task
semantics belong inside the map description. The ``task`` field
is reserved for policy-style agents (OpenP2P / NitroGen) that take
short instructions instead of long prompts; use
``compose_policy_instruction`` for that path.
"""

from __future__ import annotations

from . import load_game_prompt
from .methods import MethodStyle, get_method


def compose_vlm_system(
    method: MethodStyle | str,
    action_schema: dict,
    game: str | None,
    prompt_skill: str | None = None,
) -> str:
    """Build the VLM system prompt.

    Args:
        method: MethodStyle instance, or its registered name.
        action_schema: dict produced by ``adapter.action_schema`` — must
            include ``"key_bindings"`` (and any method-specific fields
            the MethodStyle consumes, e.g. ``chunk_steps``).
        game: game name passed to ``load_game_prompt`` (e.g.
            ``"ObstacleRun3D"``). If None or the map file is missing,
            the map section is omitted.
        prompt_skill: Optional gameplay skill / reusable experience section.
            Placed before the output format so the strict action schema remains
            the final instruction in the system prompt.

    Returns:
        Full system prompt string.
    """
    style = method if isinstance(method, MethodStyle) else get_method(method)

    map_prompt = load_game_prompt(game) if game else ""
    key_bindings = action_schema.get("key_bindings", "")
    mouse_controls = action_schema.get("mouse_controls", "")
    output_format = style.output_format(action_schema)

    sections: list[str] = []
    if map_prompt:
        sections.append(map_prompt)

    controls_lines = []
    if key_bindings:
        controls_lines.append(key_bindings)
    if mouse_controls:
        controls_lines.append(mouse_controls)
    if controls_lines:
        sections.append("Available Controls\n" + "\n".join(controls_lines))

    if prompt_skill:
        sections.append("Gameplay Skill From Prior Runs\n" + prompt_skill)

    sections.append(output_format)

    return "\n\n".join(sections)


def compose_policy_instruction(task: str) -> str:
    """Build the short instruction string for a policy-style agent.

    OpenP2P / NitroGen and similar general game policies do not accept
    long prompts — they take a short task description. This helper
    exists so the calling code is symmetric with ``compose_vlm_system``.
    """
    return task or ""
