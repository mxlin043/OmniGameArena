"""Registry of OpenAI-compatible self-hosted model profiles from router.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..profiles import SelfHostModelProfile
from omni_game_arena.routing import router_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _router_path() -> Path:
    return router_path(root=_repo_root())


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _load_profile(name: str, data: dict[str, Any]) -> SelfHostModelProfile:
    aliases = _as_tuple(data.get("aliases"))
    aliases = (str(name),) + tuple(alias for alias in aliases if alias != name)
    request_model = str(data.get("request_model") or aliases[0])
    return SelfHostModelProfile(
        aliases=aliases,
        request_model=request_model,
        base_url=os.getenv(data.get("base_url_env") or "") or data.get("base_url"),
        api_key=os.getenv(data.get("api_key_env") or "") or data.get("api_key"),
        engine=str(data.get("engine") or "sglang"),
        max_tokens=int(data.get("max_tokens", 512)),
        enable_thinking=data.get("enable_thinking", True),
        request_timeout=data.get("request_timeout"),
        extra_body=data.get("extra_body") or None,
    )


def _load_profiles() -> tuple[SelfHostModelProfile, ...]:
    router_path = _router_path()
    if not router_path.exists():
        return ()
    router = yaml.safe_load(router_path.read_text(encoding="utf-8")) or {}
    vlm = router.get("vlm") or {}
    models = vlm.get("models") if isinstance(vlm, dict) else {}
    if not isinstance(models, dict):
        return ()
    return tuple(
        _load_profile(str(name), data)
        for name, data in sorted(models.items())
        if isinstance(data, dict)
    )


PROFILES: tuple[SelfHostModelProfile, ...] = _load_profiles()

_BY_ALIAS = {
    alias.lower(): profile
    for profile in PROFILES
    for alias in profile.aliases
}


def get_profile(model: str) -> SelfHostModelProfile | None:
    # Resolve environment settings at construction time, including IDC reflector
    # clients which do not pass through benchmark YAML expansion.
    name = (model or "").lower()
    return next((profile for profile in _load_profiles()
                 if name in {alias.lower() for alias in profile.aliases}), None)


__all__ = ["PROFILES", "get_profile"]
