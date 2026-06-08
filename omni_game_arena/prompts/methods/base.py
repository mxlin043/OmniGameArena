"""MethodStyle — prompt output-format segment + paired parser.

Each VLM method implements both the
prompt wording that instructs the model how to return an action AND the
parser that reads that response back. They are defined together so the
two can never drift out of sync.

A MethodStyle also declares how to lay out history in the message list:

  - ``"packed"``: all past frames + a compact action log in ONE user
                  turn. Token-efficient; used by Lumine for chunked play.
  - ``"turns"`` : each past frame becomes its own user/assistant pair.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

HistoryLayout = Literal["packed", "turns"]


class MethodStyle(ABC):
    """Abstract base for a VLM output-format plugin."""

    name: str  # e.g. "lumine"

    # How the VLMAgent should interleave history. Subclasses override.
    history_layout: HistoryLayout = "packed"

    @abstractmethod
    def output_format(self, action_schema: dict) -> str:
        """Return the prompt segment describing how the VLM should format its action.

        This is the **last** segment of the system prompt — it follows
        the map description and the key list. Implementations may embed
        information from ``action_schema`` (chunk size, step duration,
        etc.) when relevant to the format.
        """

    @abstractmethod
    def per_turn_user_text(self) -> str:
        """The text shown alongside each screenshot on the user turn."""

    @abstractmethod
    def parse(self, response: str, *, action_schema: dict | None = None) -> dict | None:
        """Parse the VLM's raw text response into an action dict.

        Returns ``None`` when the response cannot be parsed; the caller
        is responsible for falling back to a no-op.
        """

    def noop_action(self, action_schema: dict) -> dict:
        """Return a no-op action matching this method's action schema."""
        return {}

    def compact_history_action(self, raw_response: str) -> str:
        """Strip the reasoning prefix from a past raw VLM response.

        Used when this MethodStyle is laid out as ``"packed"`` — past
        responses are re-shown compactly alongside their frame. Default
        returns the response stripped; override to drop reasoning text.
        """
        return (raw_response or "").strip()
