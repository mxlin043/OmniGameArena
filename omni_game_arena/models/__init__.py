"""Agent implementations.

Three kinds of agent live here; each satisfies ``BaseAgent``:

  - ``VLMAgent``       : general VLM (Claude / Gemini / GPT / Qwen-VL).
                         Driven by a Backend + a MethodStyle.
  - ``OpenP2PAgent``   : OpenP2P specialized game policy (HTTP server).
  - ``NitroGenAgent``  : NitroGen specialized game policy (HTTP server).

Backends live in ``omni_game_arena.models.backends``; prompt methods live in
``omni_game_arena.prompts.methods``.
"""

from .base import BaseAgent
from .nitrogen import NitroGenAgent
from .openp2p import OpenP2PAgent
from .vlm import EmptyModelResponseError, VLMAgent

__all__ = [
    "BaseAgent",
    "VLMAgent",
    "EmptyModelResponseError",
    "OpenP2PAgent",
    "NitroGenAgent",
]
