"""Configuration helpers for Improvement Dynamics Curve runs."""

from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

from ..config import ParamsPoint, AgentProfile, EnvSpec, resolve_map_name


@dataclass
class IDCConfig:
    game_name: str
    agent_profile: AgentProfile
    env_spec: EnvSpec
    params: ParamsPoint

    rounds: int = 10
    episodes_per_round: int = 3
    official_pdq_root: str = "runs/pdq"
    output_root: str = "runs/idc"
    run_dir: str = ""

    reflector_model: str = ""
    reflector_temperature: float | None = 0.0
    reflector_resize_size: int = 512
    max_reflection_iterations: int = 100
    validator_model: str = ""
    validator_temperature: float | None = 0.0
    max_validate_skill_calls: int = 5
    success_threshold: float = 0.999
    copy_full_episode: bool = False

    live: bool = False
    log_vlm: bool = False
    api_debug: bool = False

    raw_config: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "game": self.game_name,
            "agent": asdict(self.agent_profile),
            "env": asdict(self.env_spec),
            "params": asdict(self.params),
            "idc": {
                "rounds": self.rounds,
                "episodes_per_round": self.episodes_per_round,
                "official_pdq_root": self.official_pdq_root,
                "output_root": self.output_root,
                "run_dir": self.run_dir,
                "reflector_model": self.reflector_model,
                "reflector_temperature": self.reflector_temperature,
                "reflector_resize_size": self.reflector_resize_size,
                "max_reflection_iterations": self.max_reflection_iterations,
                "validator_model": self.validator_model,
                "validator_temperature": self.validator_temperature,
                "max_validate_skill_calls": self.max_validate_skill_calls,
                "success_threshold": self.success_threshold,
                "copy_full_episode": self.copy_full_episode,
            },
            "output": {
                "live": self.live,
                "log_vlm": self.log_vlm,
                "api_debug": self.api_debug,
            },
        }


def load_idc_config(path: str) -> IDCConfig:
    cfg = _load_yaml(path)
    return config_from_dict(cfg)


def config_from_dict(cfg: dict[str, Any]) -> IDCConfig:
    cfg = copy.deepcopy(cfg or {})
    game_name = cfg.get("game") or cfg.get("game_name")
    model = cfg.get("model")
    if not game_name:
        raise ValueError("IDC config requires `game`.")
    if not model:
        raise ValueError("IDC config requires `model`.")

    agent_cfg = cfg.get("agent") or {}
    agent = AgentProfile(
        model=model,
        kind=agent_cfg.get("kind", "vlm"),
        method=agent_cfg.get("method", "lumine"),
        extra=dict(agent_cfg.get("extra") or {}),
        prompt_skills=list(agent_cfg.get("prompt_skills") or []),
    )

    env_cfg = cfg.get("env") or {}
    env = EnvSpec(
        host=env_cfg.get("host", "127.0.0.1"),
        port=int(env_cfg.get("port", 12345)),
        task=env_cfg.get("task") or "",
        max_steps=int(env_cfg.get("max_steps", 220)),
        screenshot_quality=int(env_cfg.get("screenshot_quality", 85)),
        map=resolve_map_name(
            env_cfg.get("map") or "",
            cfg.get("maps_config", cfg.get("maps")),
        ),
        obs_delay=env_cfg.get("obs_delay"),
    )

    # Accept the legacy "ablation" key when loading older saved configs.
    params = cfg.get("params") or cfg.get("ablation") or {}
    params = ParamsPoint(
        history_len=int(params.get("history_len", 5)),
        history_reasoning_len=int(params.get("history_reasoning_len", 0)),
        temperature=params.get("temperature", 0.3),
        resize_size=int(params.get("resize_size", 512)),
        hold_duration=float(params.get("hold_duration", 0.2)),
        with_game_prompt=bool(params.get("with_game_prompt", True)),
        with_reasoning=bool(params.get("with_reasoning", True)),
        obs_delay=params.get("obs_delay"),
        chunk_steps=params.get("chunk_steps"),
        frame_pack=params.get("frame_pack", "none"),
        frame_pack_min_size=int(params.get("frame_pack_min_size", 112)),
    )

    idc = cfg.get("idc") or {}
    output = cfg.get("output") or {}
    return IDCConfig(
        game_name=game_name,
        agent_profile=agent,
        env_spec=env,
        params=params,
        rounds=int(idc.get("rounds", 10)),
        episodes_per_round=int(idc.get("episodes_per_round", 3)),
        official_pdq_root=idc.get("official_pdq_root", "runs/pdq"),
        output_root=idc.get("output_root", "runs/idc"),
        run_dir=output.get("run_dir") or idc.get("run_dir") or "",
        reflector_model=idc.get("reflector_model") or "",
        reflector_temperature=idc.get("reflector_temperature", 0.0),
        reflector_resize_size=int(idc.get("reflector_resize_size", 512)),
        max_reflection_iterations=int(idc.get("max_reflection_iterations", 100)),
        validator_model=idc.get("validator_model") or "",
        validator_temperature=idc.get("validator_temperature", 0.0),
        max_validate_skill_calls=int(idc.get("max_validate_skill_calls", 5)),
        success_threshold=float(idc.get("success_threshold", 0.999)),
        copy_full_episode=bool(idc.get("copy_full_episode", False)),
        live=bool(output.get("live", False)),
        log_vlm=bool(output.get("log_vlm", False)),
        api_debug=bool(output.get("api_debug", False)),
        raw_config=cfg,
    )


def _load_yaml(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"IDC config not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
