"""Self-hosted VLM backend package.

Provider-specific files keep SGLang quirks isolated while sharing the
OpenAI-compatible chat-completions implementation.
"""

from __future__ import annotations

from typing import Any

from .models import get_profile
from .openai_compat import OpenAICompatSelfHostBackend
from .profiles import SelfHostModelProfile
from .sglang import SGLangBackend


OPENAI_COMPAT_ENGINES = {
    "sglang": SGLangBackend,
    "openai_compat": OpenAICompatSelfHostBackend,
    "openai-compatible": OpenAICompatSelfHostBackend,
}

# Backward-compatible name for old imports.
SelfHostBackend = SGLangBackend


def resolve(
    model: str,
    *,
    engine: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    request_model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
    request_timeout: float | None = None,
    max_retries: int | None = None,
    **kwargs: Any,
):
    """Build a self-host backend using model-profile defaults when available."""
    profile = get_profile(model)
    engine_name = (engine or (profile.engine if profile else None) or "sglang")
    engine_name = engine_name.lower()
    cls = OPENAI_COMPAT_ENGINES.get(engine_name)
    if cls is None:
        expected = ", ".join(sorted(OPENAI_COMPAT_ENGINES))
        raise ValueError(f"Unknown self-host backend: {engine_name!r}; expected {expected}")

    if profile is not None:
        base_url = base_url or profile.base_url
        request_model = request_model or profile.request_model
        max_tokens = profile.max_tokens if max_tokens is None else max_tokens
        enable_thinking = (
            profile.enable_thinking if enable_thinking is None else enable_thinking
        )
        request_timeout = (
            profile.request_timeout if request_timeout is None else request_timeout
        )

    return cls(
        model,
        base_url=base_url,
        api_key=api_key,
        request_model=request_model,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        request_timeout=request_timeout,
        max_retries=max_retries,
        **kwargs,
    )


__all__ = [
    "OpenAICompatSelfHostBackend",
    "SGLangBackend",
    "SelfHostBackend",
    "SelfHostModelProfile",
    "resolve",
]
