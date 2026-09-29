"""Commercial closed-weight VLM backend router.

The agent layer calls ``resolve()`` once and receives a normal Backend.
Two backends live here, one per wire format:

  - ``OpenAIBackend``: any OpenAI-compatible chat-completions endpoint.
    Handles GPT, Gemini and Kimi, fronted by the same
    proxy. ``base_url`` and ``api_key`` are read from
    ``configs/router.yaml``; point them at ``https://api.openai.com/v1``
    for the official path or at any OpenAI-compatible proxy.
  - ``AnthropicBackend``: native ``/v1/messages`` for Claude family.
    ``base_url`` and ``api_key`` are read from ``configs/router.yaml``;
    point them at ``https://api.anthropic.com`` for the official path
    or at any Anthropic-compatible proxy.
"""

from __future__ import annotations

import os

from ..base import Backend
from .anthropic_backend import (
    AVAILABLE_MODELS as _ANTHROPIC_MODELS,
    AnthropicBackend,
    sends_temperature as _anthropic_sends_temperature,
)
from .openai_backend import (
    AVAILABLE_MODELS as _OPENAI_MODELS,
    OpenAIBackend,
    sends_temperature as _openai_sends_temperature,
)

# Combined list - source of truth for "is this model fronted by the
# commercial gateway?" Used by ``backends.pick_backend``.
AVAILABLE_MODELS = _OPENAI_MODELS + _ANTHROPIC_MODELS


def resolve(
    model: str,
    *,
    resize: bool = True,
    resize_size: int = 512,
    temperature: float | None = 0.3,
    request_timeout: float | None = None,
    max_retries: int | None = None,
    max_tokens: int | None = None,
) -> Backend:
    """Return the right commercial Backend for ``model``.

    Selection order:

      1. ``OMNI_ARENA_COMMERCIAL_BACKEND`` explicit override
         (``openai`` / ``anthropic``).
      2. ``claude-*`` model name -> AnthropicBackend.
      3. Anything else (GPT / Gemini / Kimi) -> OpenAIBackend.
    """
    kwargs = {
        "resize": resize,
        "resize_size": resize_size,
        "temperature": temperature,
        "request_timeout": request_timeout,
        "max_retries": max_retries,
    }

    override = os.getenv("OMNI_ARENA_COMMERCIAL_BACKEND", "").strip().lower()
    if override == "anthropic":
        return AnthropicBackend(model, **kwargs)
    if override == "openai":
        return OpenAIBackend(model, max_tokens=max_tokens, **kwargs)
    if override:
        raise ValueError(
            f"Unknown OMNI_ARENA_COMMERCIAL_BACKEND={override!r}. "
            "Register the backend in commercial.resolve()."
        )

    if model.lower().startswith("claude"):
        return AnthropicBackend(model, **kwargs)
    return OpenAIBackend(model, max_tokens=max_tokens, **kwargs)


def sends_temperature(model: str) -> bool:
    """Whether the backend ``resolve()`` picks for ``model`` sends temperature."""
    override = os.getenv("OMNI_ARENA_COMMERCIAL_BACKEND", "").strip().lower()
    if override == "anthropic" or (not override and model.lower().startswith("claude")):
        return _anthropic_sends_temperature(model)
    return _openai_sends_temperature(model)


__all__ = [
    "AVAILABLE_MODELS",
    "AnthropicBackend",
    "OpenAIBackend",
    "resolve",
    "sends_temperature",
]
