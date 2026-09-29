"""Prompt composer for VLM system prompts.

Fixed order:

    1. Map description   ← GameSpec / load_game_prompt(<game>.txt)
    2. Key list          ← adapter.action_schema["key_bindings"]
    3. Gameplay skill   ← optional text from prior runs
    4. Output format     ← MethodStyle.output_format()

The ``task`` field is intentionally NOT appended to VLM prompts. Task
semantics belong inside the map description. The ``task`` field
is reserved for policy-style agents (OpenP2P / NitroGen) that take
short instructions instead of long prompts; use
``compose_policy_instruction`` for that path.
"""

from __future__ import annotations

from .methods import MethodStyle
from .modules import render_game_description, render_controls, render_skill, render_output_format


def compose_vlm_system(
    method: MethodStyle | str,
    action_schema: dict,
    game: str | None,
    prompt_skill: str | None = None,
    *,
    with_game_prompt: bool = True,
    with_controls_prompt: bool = True,
    with_skill_prompt: bool = False,
    with_output_format_prompt: bool = True,
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
        with_game_prompt / with_controls_prompt / with_skill_prompt /
        with_output_format_prompt: Independent section switches. Skills
            require explicit opt-in; the other sections default to enabled.
            Enabled sections preserve their original text and blank lines.

    Returns:
        Full system prompt string.
    """
    sections: list[str] = []
    if with_game_prompt:
        description = render_game_description(game)
        if description:
            sections.append(description)
    if with_controls_prompt:
        controls = render_controls(action_schema)
        if controls:
            sections.append(controls)
    if with_skill_prompt:
        skill = render_skill(prompt_skill)
        if skill:
            sections.append(skill)
    if with_output_format_prompt:
        # Preserve the legacy trailing separator even for a custom style
        # that renders an empty format string.
        sections.append(render_output_format(method, action_schema))

    return "\n\n".join(sections)


def compose_policy_instruction(task: str) -> str:
    """Build the short instruction string for a policy-style agent.

    OpenP2P / NitroGen and similar general game policies do not accept
    long prompts — they take a short task description. This helper
    exists so the calling code is symmetric with ``compose_vlm_system``.
    """
    return task or ""
