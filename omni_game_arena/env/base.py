"""Base environment interface (Gym-like)."""

from abc import ABC, abstractmethod
import time
from typing import Any, Dict, Tuple

Observation = Dict[str, Any]
"""Observation dict: {"image": PIL.Image, "width": int, "height": int, "timestamp": float}

``timestamp`` is the observation capture time.
"""

Info = Dict[str, Any]


class BaseEnv(ABC):

    @abstractmethod
    def reset(self) -> Observation:
        """Reset environment and return initial observation."""
        ...

    @abstractmethod
    def step(self, action: dict) -> Tuple[Observation, float, bool, Info]:
        """Execute action, return (obs, reward, done, info)."""
        ...

    @abstractmethod
    def observe(self) -> Observation:
        """Take observation without acting."""
        ...

    @abstractmethod
    def close(self):
        """Release resources."""
        ...

    def pause(self):
        """Freeze environment simulation if the backend supports it."""
        raise NotImplementedError("Environment pause is not supported")

    def resume(self):
        """Resume environment simulation if the backend supports it."""
        raise NotImplementedError("Environment resume is not supported")

    def advance_game_time(self, seconds: float, *, pause_after: bool = True):
        """Let the simulator run for ``seconds`` of wall/game time.

        Backends with normal time scale can use this as a small building
        block for latency-controlled protocols: resume, wait, then optionally
        freeze again before the next scheduled event.
        """
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.resume()
        if seconds > 0:
            time.sleep(seconds)
        if pause_after:
            self.pause()

    def sleep_game_time(self, seconds: float, *, pause_after: bool = True):
        """Alias for ``advance_game_time`` used by latency-control runners."""
        self.advance_game_time(seconds, pause_after=pause_after)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
