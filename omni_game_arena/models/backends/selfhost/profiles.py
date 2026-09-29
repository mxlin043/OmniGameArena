"""Model profile records for self-hosted OpenAI-compatible backends."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SelfHostModelProfile:
    """Defaults for one deployed self-host model family."""

    aliases: tuple[str, ...]
    request_model: str
    base_url: str | None = None
    engine: str = "sglang"
    max_tokens: int = 512
    enable_thinking: bool | None = True
    request_timeout: float | None = None
    # Extra request fields merged into every call, for sampling settings a
    # model's own card prescribes (Qwen3.5 Instruct mode needs
    # presence_penalty to avoid endless repetition). Model-scoped rather
    # than game-scoped, since the requirement comes from the checkpoint.
    extra_body: dict | None = None
    api_key: str | None = field(default=None, repr=False)

    @property
    def canonical_name(self) -> str:
        return self.aliases[0]
