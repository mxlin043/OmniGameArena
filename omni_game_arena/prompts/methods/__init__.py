"""Registry of MethodStyle plugins."""

from __future__ import annotations

from .base import MethodStyle
from .lumine import SPEC as _LUMINE

REGISTRY: dict[str, MethodStyle] = {
    _LUMINE.name: _LUMINE,
}


def get_method(name: str) -> MethodStyle:
    if name not in REGISTRY:
        avail = ", ".join(sorted(REGISTRY.keys()))
        raise ValueError(
            f"Unknown method style {name!r}. Registered: {avail}"
        )
    return REGISTRY[name]


def list_methods() -> list[str]:
    return sorted(REGISTRY.keys())


__all__ = ["MethodStyle", "REGISTRY", "get_method", "list_methods"]
