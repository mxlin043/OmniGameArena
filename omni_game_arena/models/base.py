"""Agent ABC - minimum contract every game-playing agent must satisfy.

Three Agent kinds live in this package:

  - ``VLMAgent``      : general VLM driven by a Backend + a MethodStyle.
                        Used for Claude / Gemini / GPT / Qwen-VL / etc.
  - ``OpenP2PAgent``  : OpenP2P specialized game policy (HTTP server).
  - ``NitroGenAgent`` : NitroGen specialized game policy (HTTP server).

The benchmark loop only depends on ``act`` / ``reset``, so any
class implementing those can plug in without touching the runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Minimal interface for a game-playing agent."""

    @abstractmethod
    def act(self, obs: dict, task: str, action_schema: dict) -> dict:
        """Decide the next action.

        Args:
            obs: ``{"image": PIL.Image, "width": int, "height": int, "timestamp": float}``.
                ``timestamp`` is the observation capture time.
            task: Natural-language task description (may be ignored by
                long-prompt agents that put task semantics in the map prompt).
            action_schema: Adapter-defined dict describing the action space.
                The agent should return a dict the matching adapter knows
                how to ``execute()``.
        """

    def reset_history(self) -> None:
        """Clear per-episode state (e.g. KV cache, frame buffer). No-op by default."""

    def reset(self) -> None:
        """Reset per-episode agent state before a fresh game episode.

        ``reset_history`` remains the backward-compatible name used by older
        scripts; new benchmark code should call ``reset``.
        """
        self.reset_history()

    def health_check(self) -> bool:
        """Return True if the agent's backend is reachable. Default: True."""
        return True
