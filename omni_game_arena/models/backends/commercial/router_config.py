"""Read commercial backend endpoint settings from configs/router.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _router_path() -> Path:
    override = os.getenv("OMNI_ARENA_ROUTER_CONFIG")
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else (_repo_root() / path)
    return _repo_root() / "configs" / "router.yaml"


@lru_cache(maxsize=4)
def _load_router(path: str) -> dict[str, Any]:
    router_path = Path(path)
    if not router_path.exists():
        raise RuntimeError(f"Router config not found: {router_path}")
    return yaml.safe_load(router_path.read_text(encoding="utf-8")) or {}


def commercial_value(route_name: str, field: str) -> str:
    """Return a commercial route field from configs/router.yaml.

    The backend files themselves do not carry URL/key defaults; commercial
    endpoints come from the router only.
    """
    router_path = _router_path().resolve()
    router = _load_router(str(router_path))
    route = ((router.get("commercial") or {}).get(route_name) or {})
    if not isinstance(route, dict):
        route = {}

    value = route.get(field)
    if value in (None, ""):
        raise RuntimeError(
            f"Missing commercial.{route_name}.{field} in {router_path}"
        )
    return str(value)
