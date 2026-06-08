"""SGLang self-host backend."""

from __future__ import annotations

from .openai_compat import OpenAICompatSelfHostBackend


class SGLangBackend(OpenAICompatSelfHostBackend):
    """SGLang OpenAI-compatible backend.

    Qwen thinking is enabled by default. Override with
    ``extra.enable_thinking: false`` or ``null`` in YAML if needed.
    """

    provider_name = "sglang_openai_compat"
    default_enable_thinking = True
