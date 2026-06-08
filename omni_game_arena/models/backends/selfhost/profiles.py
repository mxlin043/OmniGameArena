"""Model profile records for self-hosted OpenAI-compatible backends."""

from __future__ import annotations

from dataclasses import dataclass


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

    @property
    def canonical_name(self) -> str:
        return self.aliases[0]
