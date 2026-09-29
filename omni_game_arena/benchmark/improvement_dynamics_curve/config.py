"""Configuration helpers for Improvement Dynamics Curve runs."""

from __future__ import annotations

from omni_game_arena.utils.public_config import public_config

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
    episodes_per_round: int = 5
    official_lfm_root: str = "runs/lfm"
    output_root: str = "runs/idc"
    run_dir: str = ""

    reflector_model: str = ""
    reflector_temperature: float | None = 0.0
    reflector_resize_size: int = 512
    max_reflection_iterations: int = 100
    validator_model: str = ""
    validator_temperature: float | None = 0.0
    max_validate_skill_calls: int = 5

    # PvP: the evaluated model is always P1; these frozen profiles are P2.
    pvp_opponents: list[AgentProfile] = field(default_factory=list)
    pvp_episodes_per_opponent: int = 1
    pvp_metric: str = "score"
    pvp_reflection_view: str = "player1"

    live: bool = False
    log_vlm: bool = False
    api_debug: bool = False

    raw_config: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "game": self.game_name,
            "agent": public_config(self.agent_profile),
            "env": asdict(self.env_spec),
            "params": asdict(self.params),
            "idc": {
                "rounds": self.rounds,
                "episodes_per_round": self.episodes_per_round,
                "official_lfm_root": self.official_lfm_root,
                "output_root": self.output_root,
                "run_dir": self.run_dir,
                "reflector_model": self.reflector_model,
                "reflector_temperature": self.reflector_temperature,
                "reflector_resize_size": self.reflector_resize_size,
                "max_reflection_iterations": self.max_reflection_iterations,
                "validator_model": self.validator_model,
                "validator_temperature": self.validator_temperature,
                "max_validate_skill_calls": self.max_validate_skill_calls,
                "pvp_opponents": [public_config(opponent) for opponent in self.pvp_opponents],
                "pvp_episodes_per_opponent": self.pvp_episodes_per_opponent,
                "pvp_metric": self.pvp_metric,
                "pvp_reflection_view": self.pvp_reflection_view,
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
        game_prompt_key=agent_cfg.get("game_prompt_key") or None,
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
        with_controls_prompt=bool(params.get("with_controls_prompt", True)),
        # IDC explicitly uses skills learned in prior rounds. Keep that
        # workflow enabled unless its config requests a skill-free ablation.
        with_skill_prompt=bool(params.get("with_skill_prompt", True)),
        with_output_format_prompt=bool(params.get("with_output_format_prompt", True)),
        with_visual_input=bool(params.get("with_visual_input", True)),
        with_reasoning=bool(params.get("with_reasoning", True)),
        obs_delay=params.get("obs_delay"),
        chunk_steps=params.get("chunk_steps"),
        frame_pack=params.get("frame_pack", "none"),
        frame_pack_min_size=int(params.get("frame_pack_min_size", 112)),
    )

    idc = cfg.get("idc") or {}
    output = cfg.get("output") or {}
    opponents = []
    for item in idc.get("pvp_opponents") or []:
        item = {"model": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not item.get("model"):
            raise ValueError("Each pvp_opponents entry requires a model")
        opponents.append(AgentProfile(**item))
    per_opponent = int(idc.get("pvp_episodes_per_opponent", 1))
    return IDCConfig(
        game_name=game_name,
        agent_profile=agent,
        env_spec=env,
        params=params,
        rounds=int(idc.get("rounds", 10)),
        episodes_per_round=int(idc.get("episodes_per_round", len(opponents) * per_opponent if opponents else 5)),
        official_lfm_root=idc.get("official_lfm_root", "runs/lfm"),
        output_root=idc.get("output_root", "runs/idc"),
        run_dir=output.get("run_dir") or idc.get("run_dir") or "",
        reflector_model=idc.get("reflector_model") or "",
        reflector_temperature=idc.get("reflector_temperature", 0.0),
        reflector_resize_size=int(idc.get("reflector_resize_size", 512)),
        max_reflection_iterations=int(idc.get("max_reflection_iterations", 100)),
        validator_model=idc.get("validator_model") or "",
        validator_temperature=idc.get("validator_temperature", 0.0),
        max_validate_skill_calls=int(idc.get("max_validate_skill_calls", 5)),
        pvp_opponents=opponents,
        pvp_episodes_per_opponent=per_opponent,
        pvp_metric=idc.get("pvp_metric", "score"),
        pvp_reflection_view=idc.get("pvp_reflection_view", "player1"),
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
