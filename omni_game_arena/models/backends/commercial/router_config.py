"""Read commercial backend endpoint settings from configs/router.yaml."""

from __future__ import annotations

import os
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from omni_game_arena.routing import router_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _router_path() -> Path:
    return router_path(root=_repo_root())


@lru_cache(maxsize=4)
def _load_router(path: str) -> dict[str, Any]:
    router_path = Path(path)
    if not router_path.exists():
        raise RuntimeError(f"Router config not found: {router_path}")
    return yaml.safe_load(router_path.read_text(encoding="utf-8")) or {}


def _commercial_route(route_name: str, model: str | None = None) -> dict[str, Any]:
    router_path = _router_path().resolve()
    router = _load_router(str(router_path))
    route = ((router.get("commercial") or {}).get(route_name) or {})
    if not isinstance(route, dict):
        raise RuntimeError(f"commercial.{route_name} must be a mapping in {router_path}")
    if model is None:
        return route
    overrides = route.get("model_overrides") or {}
    if not isinstance(overrides, dict):
        raise RuntimeError(f"commercial.{route_name}.model_overrides must be a mapping")
    name = model.lower()
    # Exact names take precedence over wildcard families. Overrides replace the
    # entire account route so credentials and headers cannot leak across accounts.
    matches = [(pattern, value) for pattern, value in overrides.items()
               if isinstance(pattern, str) and fnmatchcase(name, pattern.lower())]
    matches.sort(key=lambda item: item[0].lower() != name)
    if matches:
        selected = matches[0][1]
        if not isinstance(selected, dict):
            raise RuntimeError(f"Invalid commercial route override for {model}")
        return selected
    return route


def commercial_value(route_name: str, field: str, *, model: str | None = None) -> str:
    """Return a commercial route field from configs/router.yaml.

    The backend files themselves do not carry URL/key defaults; commercial
    endpoints come from the router only.
    """
    router_path = _router_path().resolve()
    route = _commercial_route(route_name, model)

    env_name = route.get(f"{field}_env")
    value = (os.getenv(str(env_name)) if env_name else None) or route.get(field)
    if value in (None, ""):
        hint = f"fill in {field} in {router_path}"
        if env_name:
            hint += f" or set {env_name}"
        raise RuntimeError(f"Missing commercial.{route_name}.{field}; {hint}")
    return str(value)


def commercial_headers(route_name: str, *, model: str | None = None) -> dict[str, str]:
    """Return optional gateway headers for a commercial route."""
    router_path = _router_path().resolve()
    route = _commercial_route(route_name, model)
    headers = route.get("extra_headers") or {}
    if not isinstance(headers, dict):
        raise RuntimeError(
            f"commercial.{route_name}.extra_headers must be a mapping in {router_path}"
        )
    return {str(name): str(value) for name, value in headers.items()}


def commercial_option(route_name: str, field: str, default: Any = None, *, model: str | None = None) -> Any:
    """Read an optional route setting without changing existing defaults."""
    route = _commercial_route(route_name, model)
    return route.get(field, default)
