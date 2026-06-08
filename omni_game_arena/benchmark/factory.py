"""Agent / Adapter factory.

Composes one ``(agent, adapter)`` pair per experiment cell from three
clean axes:

  - ``profile.kind``    - agent class:   ``vlm`` | ``openp2p`` | ``nitrogen``.
  - ``profile.method``  - VLM output style (only meaningful when
    ``kind == "vlm"``):  ``lumine`` (main table).
  - ``profile.extra``   - per-agent kwargs forwarded to the constructor.

The matching adapter is chosen automatically from ``method`` so YAMLs
never need to mention adapters by name.
"""

from __future__ import annotations

import logging
from typing import Any

from omni_game_arena.adapters.base import BaseActionAdapter
from omni_game_arena.adapters.keyboard_mouse_chunked import KeyboardMouseChunkedAdapter
from omni_game_arena.adapters.nitrogen_adapter import NitroGenAdapter
from omni_game_arena.adapters.p2p_adapter import P2PAdapter
from omni_game_arena.models import NitroGenAgent, OpenP2PAgent, VLMAgent
from omni_game_arena.prompts.skill_injection import load_prompt_skills

from .config import ParamsPoint, AgentProfile
from .frame_pack import FramePackConfig
from .frame_pack_wrapper import attach_frame_pack
from .games.base import GameSpec

logger = logging.getLogger(__name__)


# -- Public API ----------------------------------------------------------

def build_agent_and_adapter(
    profile: AgentProfile,
    params: ParamsPoint,
    game: GameSpec,
    player_index: int | None = None,
) -> tuple[Any, BaseActionAdapter]:
    """Construct an (agent, adapter) pair for one experiment cell."""
    kind = profile.kind.lower()

    if kind == "vlm":
        return _build_vlm(profile, params, game, player_index=player_index)
    if kind == "openp2p":
        return _build_openp2p(profile)
    if kind == "nitrogen":
        return _build_nitrogen(profile)
    raise ValueError(
        "Unknown agent kind: "
        f"{profile.kind!r} (expected vlm | openp2p | nitrogen)"
    )


# -- VLM path (Claude / Gemini / GPT / Qwen-VL / ...) --------------------

def _build_vlm(
    profile: AgentProfile,
    params: ParamsPoint,
    game: GameSpec,
    player_index: int | None = None,
) -> tuple[VLMAgent, BaseActionAdapter]:
    method = (profile.method or "lumine").lower()
    adapter = _adapter_for_method(method, game, params)
    method_style = _method_for_params(method, params)

    # ``extra`` carries endpoint kwargs (``base_url``, ``api_key``) and any
    # agent-side override (``include_history_actions``).
    # ``key_bindings`` lives there too for legacy YAMLs but is consumed by
    # the adapter, not the agent - strip it before forwarding.
    agent_extra = {k: v for k, v in profile.extra.items() if k != "key_bindings"}

    agent = VLMAgent(
        model=profile.model,
        method=method_style,
        resize_size=params.resize_size,
        temperature=params.temperature,
        history_len=params.history_len,
        history_reasoning_len=params.history_reasoning_len,
        game=(
            game.prompt_key_for_player(player_index)
            if params.with_game_prompt
            else None
        ),
        **agent_extra,
    )
    prompt_skill_text = load_prompt_skills(profile.prompt_skills)
    agent.system_experience = prompt_skill_text
    if prompt_skill_text:
        logger.info(
            "Loaded %d prompt skill file(s) into VLM system prompt (%d chars)",
            len(profile.prompt_skills),
            len(prompt_skill_text),
        )
    _apply_frame_pack(agent, params)
    return agent, adapter


def _method_for_params(method: str, params: ParamsPoint):
    if method == "lumine":
        from omni_game_arena.prompts.methods.lumine import LumineStyle

        return LumineStyle(thinking=params.with_reasoning)
    return method


def _adapter_for_method(
    method: str,
    game: GameSpec,
    params: ParamsPoint,
) -> BaseActionAdapter:
    """Return the adapter paired with ``method``.

    Lumine uses the chunked keyboard+mouse adapter parameterised by the
    game's mouse axes / chunk steps.
    """
    if method == "lumine":
        chunk_steps = (
            params.chunk_steps
            if params.chunk_steps is not None
            else game.chunk_steps
        )
        return KeyboardMouseChunkedAdapter(
            key_bindings=game.key_bindings,
            mouse_axes=game.mouse_axes,
            chunk_steps=chunk_steps,
            tap_keys=game.tap_keys,
            step_duration=params.hold_duration,
        )
    raise ValueError(f"Unknown method: {method!r} (expected lumine)")


# -- Specialized policy paths --------------------------------------------

def _build_openp2p(profile: AgentProfile) -> tuple[OpenP2PAgent, P2PAdapter]:
    url = profile.extra.get("url", "http://127.0.0.1:8081")
    adapter = P2PAdapter()
    agent = OpenP2PAgent(
        url=url,
        text=profile.extra.get("text"),
        timeout=profile.extra.get("timeout", 180.0),
        health_timeout=profile.extra.get("health_timeout", 5.0),
        skip_health=profile.extra.get("skip_health", False),
        skip_reset=profile.extra.get("skip_reset", False),
        image_format=profile.extra.get("image_format", "JPEG"),
        image_quality=profile.extra.get("image_quality", 85),
    )
    if hasattr(agent, "health_check") and not agent.health_check():
        logger.warning("OpenP2P server at %s is unreachable", url)
    return agent, adapter


def _build_nitrogen(profile: AgentProfile) -> tuple[NitroGenAgent, NitroGenAdapter]:
    url = profile.extra.get("url", "http://127.0.0.1:8081")
    adapter = NitroGenAdapter(
        stick_scale=profile.extra.get("stick_scale", 80.0),
        invert_y=profile.extra.get("invert_y", True),
        axis_deadzone=profile.extra.get("axis_deadzone", 0.0),
    )
    fps = profile.extra.get("fps", 20)
    step_interval = 1.0 / fps if fps > 0 else 0.0
    agent = NitroGenAgent(
        url=url,
        text=profile.extra.get("text"),
        action_downsample_ratio=profile.extra.get("action_repeat", 1),
        step_interval=step_interval,
        allow_menu=profile.extra.get("allow_menu", False),
        timeout=profile.extra.get("timeout", 180.0),
        health_timeout=profile.extra.get("health_timeout", 5.0),
        image_format=profile.extra.get("image_format", "JPEG"),
        image_quality=profile.extra.get("image_quality", 85),
    )
    if hasattr(agent, "health_check") and not agent.health_check():
        logger.warning("NitroGen server at %s is unreachable", url)
    return agent, adapter


# -- Helpers -------------------------------------------------------------

def _apply_frame_pack(agent, params: ParamsPoint) -> None:
    """Attach FramePack compression; no-op when kernel == 'none'."""
    if params.frame_pack == "none":
        return
    cfg = FramePackConfig(
        kernel=params.frame_pack,
        base_size=params.resize_size,
        min_size=params.frame_pack_min_size,
    )
    attach_frame_pack(agent, cfg)
