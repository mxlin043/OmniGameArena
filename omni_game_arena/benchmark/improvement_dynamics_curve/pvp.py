"""Fixed-opponent PvP IDC protocol and complete-round checkpoints."""

from __future__ import annotations

from omni_game_arena.utils.public_config import public_config

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import IDCConfig
from .io import atomic_write_json, atomic_write_text, load_json
from .metrics import aggregate_episode_results


def validate_pvp_config(cfg: IDCConfig) -> None:
    if len(cfg.pvp_opponents) != 5:
        raise ValueError("PvP IDC requires five fixed opponents")
    if cfg.pvp_episodes_per_opponent < 1:
        raise ValueError("pvp_episodes_per_opponent must be positive")
    if cfg.episodes_per_round != 5 * cfg.pvp_episodes_per_opponent:
        raise ValueError("PvP episodes_per_round must equal 5 * pvp_episodes_per_opponent")
    names = [p.model.lower() for p in cfg.pvp_opponents]
    if len(set(names)) != 5:
        raise ValueError("PvP IDC opponent models must be distinct")
    if cfg.pvp_metric not in {"score", "win_points"}:
        raise ValueError("pvp_metric must be score or win_points")
    if cfg.pvp_reflection_view != "player1":
        raise ValueError("PvP IDC reflection may only access P1's own traces and CoT")
    if cfg.reflector_model and cfg.reflector_model != cfg.agent_profile.model:
        raise ValueError("PvP IDC reflection must use the evaluated P1 model")
    for profile in [cfg.agent_profile, *cfg.pvp_opponents]:
        if profile.kind != "vlm":
            raise ValueError("PvP IDC currently requires VLM player profiles")
        if profile.prompt_skills or any(
            "skill" in key.lower() or "experience" in key.lower() for key in profile.extra
        ):
            raise ValueError("PvP IDC cold profiles must not contain prompt skills or experience")


def protocol_for_config(cfg: IDCConfig) -> dict[str, Any]:
    return {
        "version": 1,
        "game": cfg.game_name,
        "evaluated_player": "player_1",
        "player": public_config(cfg.agent_profile),
        "opponent_player": "player_2",
        "opponents": [public_config(p) for p in cfg.pvp_opponents],
        "opponent_skill": "",
        "round0_source": "fresh_fixed_opponents",
        "episodes_per_round": cfg.episodes_per_round,
        "episodes_per_opponent": cfg.pvp_episodes_per_opponent,
        "clock_mode": "lfm",
        "params": asdict(cfg.params),
        "env": asdict(cfg.env_spec),
        "metric": cfg.pvp_metric,
        "reflection_view": cfg.pvp_reflection_view,
        "reflector_model": cfg.reflector_model or cfg.agent_profile.model,
        "reflector_temperature": cfg.reflector_temperature,
        "validator_model": cfg.validator_model or cfg.reflector_model or cfg.agent_profile.model,
        "validator_temperature": cfg.validator_temperature,
    }


def pvp_protocol_matches(run_dir: Path, saved: dict, current: dict) -> bool:
    """Allow an audited transport-only change while preserving the old protocol."""
    if saved == current:
        return True
    import copy
    import hashlib
    change_path = run_dir / "transport_change.json"
    if not change_path.exists():
        return False
    change = load_json(change_path)
    protocol_path = run_dir / "pvp_protocol.json"
    if (change.get("user_authorized") is not True
            or Path(change.get("run_dir", "")).resolve() != run_dir.resolve()
            or change.get("preserved_protocol_sha256") != hashlib.sha256(protocol_path.read_bytes()).hexdigest()):
        return False
    previous = {k: saved.get("env", {}).get(k) for k in ("host", "port")}
    following = {k: current.get("env", {}).get(k) for k in ("host", "port")}
    if previous != change.get("from") or following != change.get("to"):
        return False
    adjusted = copy.deepcopy(saved)
    adjusted["env"].update(following)
    return adjusted == current


def ensure_pvp_protocol(run_dir: Path, cfg: IDCConfig) -> dict[str, Any]:
    """Refuse mixed roles, sampling conditions or opponent rosters on resume."""
    validate_pvp_config(cfg)
    protocol = protocol_for_config(cfg)
    path = run_dir / "pvp_protocol.json"
    if path.exists():
        if not pvp_protocol_matches(run_dir, load_json(path), protocol):
            raise ValueError("PvP IDC protocol changed on resume; start a new run for new conditions")
    else:
        if any(run_dir.glob("round_*")):
            raise ValueError("Existing rounds have no PvP protocol; refusing to reuse unverified results")
        atomic_write_json(path, protocol)
    return protocol


def run_pvp_round(
    *, cfg: IDCConfig, run_dir: Path, round_idx: int, skill_text: str,
    game, exp_template, viewer=None, progress_callback=None,
) -> dict[str, Any]:
    from .episodes import run_episode_set

    if round_idx == 0 and skill_text:
        raise ValueError("PvP round 0 must be cold start with no skill")
    round_dir = run_dir / f"round_{round_idx:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    result_path = round_dir / "round_result.json"
    count = cfg.episodes_per_round
    if result_path.exists() and any(
        not (round_dir / "episodes" / f"ep_{i:02d}" / "idc_pvp_record.json").is_file()
        for i in range(count)
    ):
        raise ValueError(f"Completed PvP round is missing episode checkpoints: {round_dir}")
    if not (round_dir / "skill_in.md").exists():
        atomic_write_text(round_dir / "skill_in.md", skill_text)
    # Validate/reuse every checkpoint, including its fixed opponent slot.
    schedule = [p for p in cfg.pvp_opponents for _ in range(cfg.pvp_episodes_per_opponent)]
    episodes = run_episode_set(
        round_dir=round_dir, round_idx=round_idx, skill_text=skill_text,
        game=game, exp_template=exp_template, n_episodes=count, clock_mode="lfm",
        live_viewer=viewer, log_vlm=cfg.log_vlm, api_debug=cfg.api_debug,
        progress_callback=progress_callback, pvp_opponents=schedule,
        pvp_metric=cfg.pvp_metric,
    )
    result = {
        "round_idx": round_idx,
        "source": "fresh_pvp_cold_start" if round_idx == 0 else "idc_pvp_episode_run",
        "mode": "pvp",
        "skill_in": "" if round_idx == 0 else f"round_{round_idx:02d}/skill_in.md",
        "evaluated_player": "player_1",
        "opponents": [p.model for p in cfg.pvp_opponents],
        "episodes_per_opponent": cfg.pvp_episodes_per_opponent,
        "pvp_metric": cfg.pvp_metric,
        "episodes": episodes,
        **aggregate_episode_results(episodes),
        "mean_player_score": sum(ep["pvp"]["player_score"] for ep in episodes) / count,
        "mean_score_margin": sum(ep["pvp"]["score_margin"] for ep in episodes) / count,
        "mean_win_points": sum(ep["pvp"]["win_points"] for ep in episodes) / count,
        "win_rate": sum(ep["pvp"]["outcome"] == "win" for ep in episodes) / count,
        "draw_rate": sum(ep["pvp"]["outcome"] == "draw" for ep in episodes) / count,
    }
    if result_path.exists():
        if load_json(result_path) != result:
            raise ValueError(f"PvP round result disagrees with its episode checkpoints: {round_dir}")
    else:
        atomic_write_json(result_path, result)
    return result


def reflection_readable_paths(round_dir: Path) -> list[str]:
    """Expose own observations and public scores; hide P2's private traces."""
    result = load_json(round_dir / "round_result.json")
    paths = [
        "round_result.json", "idc_context.json", "skill_in.md", "best_skill.md",
        "skill_in_pre_guard.md", "notebook_so_far.md", "lints.md", "regression_guard.md",
    ]
    for episode in result["episodes"]:
        ep = episode["episode_id"]
        paths.extend([
            f"episodes/{ep}/player_1", f"episodes/{ep}/result.json",
            f"episodes/{ep}/idc_pvp_record.json",
        ])
    return paths
