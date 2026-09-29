"""VLM HTTP backend resolver.

Commercial APIs and OpenAI-compatible VLM engines share the same agent-facing
``Backend`` interface. ``pick_backend()`` is the only entry point the agent
layer uses. NitroGen/OpenP2P are separate agent kinds, not VLM backends.
"""

from __future__ import annotations

from .base import Backend
from .commercial import (
    AVAILABLE_MODELS as _GATEWAY_MODELS,
    resolve as _resolve_commercial,
    sends_temperature as _commercial_sends_temperature,
)
from .selfhost import SelfHostBackend, resolve as _resolve_openai_compat_engine
from .selfhost.models import get_profile as _get_openai_compat_profile

_GATEWAY_MODEL_SET = {m.lower() for m in _GATEWAY_MODELS}


def pick_backend(
    model: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    request_model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
    resize: bool = True,
    resize_size: int = 512,
    temperature: float | None = 0.3,
    request_timeout: float | None = None,
    max_retries: int | None = None,
) -> Backend:
    """Return a Backend instance for ``model``.

    Selection order:
      1. Registered model profile, e.g. qwen3.5-397b-a17b.
      2. ``base_url`` set means an OpenAI-compatible VLM endpoint.
      3. Registered commercial gateway model or GPT/Gemini/Claude prefix.
    Any other name is an error, so a model is never silently served by a
    different endpoint.
    """
    kwargs = {
        "resize": resize,
        "resize_size": resize_size,
        "temperature": temperature,
        "request_timeout": request_timeout,
        "max_retries": max_retries,
    }

    openai_compat_profile = _get_openai_compat_profile(model)

    # The model registry owns routing for named VLMs. This lets YAMLs use
    # Qwen like a normal model name and keeps endpoint details out of configs.
    if openai_compat_profile is not None:
        return _resolve_openai_compat_engine(
            model,
            engine=openai_compat_profile.engine,
            base_url=base_url,
            api_key=api_key,
            request_model=request_model,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            **kwargs,
        )

    if base_url:
        return _resolve_openai_compat_engine(
            model,
            engine="openai_compat",
            base_url=base_url,
            api_key=api_key,
            request_model=request_model,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            **kwargs,
        )

    name = model.lower()
    if name in _GATEWAY_MODEL_SET or name.startswith(("claude", "gemini", "gpt")):
        return _resolve_commercial(model, max_tokens=max_tokens, **kwargs)

    raise ValueError(
        f"Unknown model {model!r}: register it under vlm.models in "
        "configs/router.yaml, or give it an explicit base_url."
    )


def sends_temperature(model: str, *, base_url: str | None = None) -> bool:
    """Whether the backend ``pick_backend`` selects sends a temperature.

    Some commercial APIs (the GPT-5 family, Claude Opus 4.7) reject the
    parameter, so those models run at their provider default.
    """
    if _get_openai_compat_profile(model) is not None or base_url:
        return True
    name = model.lower()
    if name in _GATEWAY_MODEL_SET or name.startswith(("claude", "gemini", "gpt")):
        return _commercial_sends_temperature(model)
    return True


__all__ = ["Backend", "SelfHostBackend", "pick_backend", "sends_temperature"]
