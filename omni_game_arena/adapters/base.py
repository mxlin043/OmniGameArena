"""Base action adapter interface."""

from abc import ABC, abstractmethod


class BaseActionAdapter(ABC):

    @property
    @abstractmethod
    def action_schema(self) -> dict:
        """Return JSON-serializable schema describing available actions.
        Injected into LLM system prompt so the model knows what to produce."""
        ...

    @abstractmethod
    def execute(self, client, action: dict) -> None:
        """Translate action dict from LLM into UE5 client commands."""
        ...
