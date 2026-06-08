"""Load and stage Round 0 official PDQ episodes for IDC.

Supports two source layouts:

  Solo (e.g. obstacle_run_3d, last_stand):
      pdq/<game>/<model>/<...>/<timestamp>/reflection_trace/

  Coop self-cooperation (e.g. shared_floor):
      pdq/<game>/player1-<model>_vs_player2-<model>/<...>/<timestamp>/
          player_1/reflection_trace/
          player_2/reflection_trace/

For coop, each "episode" copies BOTH player traces into
``ep_NN/player_{1,2}/reflection_trace/``. The team score is read from
either player's ``result.json["score"]`` (both report the same joint
team_score field on the SharedFloor / similar coop scenes).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from omni_game_arena.eval.reflection_trace import TRACE_DIR_NAME

from .io import atomic_write_json, load_json
from .metrics import aggregate_episode_results, score_from_result


def stage_round0_from_official_pdq(
    *,
    pdq_root: str | Path,
    game_name: str,
    model: str,
    run_dir: str | Path,
    success_threshold: float,
    mode: str = "solo",
) -> dict[str, Any]:
    """Copy official ``reflection_trace`` dirs into ``round_00``.

    ``mode``: ``"solo"`` (default) or ``"coop"``. Selects which layout to
    look for under ``pdq_root`` and how to copy per-episode artifacts.

    Only ``reflection_trace/`` is copied. Scores are read from the original
    ``result.json`` and recorded in manifests.
    """
    run_dir = Path(run_dir)
    round_dir = run_dir / "round_00"
    episodes_dir = round_dir / "episodes"
    manifest_path = run_dir / "official_source_manifest.json"
    round_result_path = round_dir / "round_result.json"

    if manifest_path.exists() and round_result_path.exists():
        return load_json(round_result_path)

    if mode == "coop":
        sources = find_official_pdq_episodes_coop(
            pdq_root=pdq_root,
            game_name=game_name,
            model=model,
        )
    else:
        sources = find_official_pdq_episodes(
            pdq_root=pdq_root,
            game_name=game_name,
            model=model,
        )
    if not sources:
        raise FileNotFoundError(
            f"No official PDQ episodes found under {pdq_root}/{game_name}/"
            f"{model} (mode={mode})"
        )

    episodes = []
    manifest_episodes = []
    for idx, source in enumerate(sources):
        dst_dir = episodes_dir / f"official_ep_{idx:02d}"
        copied: list[str] = []

        if mode == "coop":
            # source["players"] = list of {"player_label", "run_dir"}
            for player in source["players"]:
                player_label = player["player_label"]
                src_player_dir = Path(player["run_dir"])
                src_trace = src_player_dir / TRACE_DIR_NAME
                if not src_trace.exists():
                    raise FileNotFoundError(
                        f"Missing reflection_trace: {src_trace}"
                    )
                dst_player_dir = dst_dir / player_label
                dst_trace = dst_player_dir / TRACE_DIR_NAME
                if dst_trace.exists():
                    shutil.rmtree(dst_trace)
                dst_trace.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_trace, dst_trace)
                copied.append(f"{player_label}/{TRACE_DIR_NAME}")
        else:
            src_dir = Path(source["run_dir"])
            src_trace = src_dir / TRACE_DIR_NAME
            if not src_trace.exists():
                raise FileNotFoundError(f"Missing reflection_trace: {src_trace}")
            dst_trace = dst_dir / TRACE_DIR_NAME
            if dst_trace.exists():
                shutil.rmtree(dst_trace)
            dst_trace.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_trace, dst_trace)
            copied.append(TRACE_DIR_NAME)

        episode = {
            "episode_id": f"official_ep_{idx:02d}",
            "source_run_dir": str(source.get("run_dir") or source.get("match_dir")),
            "dst": str(dst_dir.relative_to(run_dir)),
            "score": source["score"],
            "status": source["status"],
            "episode_idx": source["episode_idx"],
        }
        if mode == "coop":
            episode["player_scores"] = source.get("player_scores")
            episode["mode"] = "coop"
        episodes.append(episode)
        manifest_episodes.append({
            **episode,
            "copied": copied,
        })

    aggregate = aggregate_episode_results(
        episodes,
        success_threshold=success_threshold,
    )
    round_result = {
        "round_idx": 0,
        "source": "official_pdq",
        "skill_in": "",
        "mode": mode,
        "episodes": episodes,
        **aggregate,
    }
    atomic_write_json(round_result_path, round_result)
    atomic_write_json(manifest_path, {
        "pdq_root": str(pdq_root),
        "game": game_name,
        "model": model,
        "mode": mode,
        "episodes": manifest_episodes,
    })
    return round_result


def find_official_pdq_episodes(
    *,
    pdq_root: str | Path,
    game_name: str,
    model: str,
) -> list[dict[str, Any]]:
    root = Path(pdq_root) / game_name / model
    if not root.exists():
        return []
    items = []
    for result_path in root.rglob("result.json"):
        run_dir = result_path.parent
        trace_dir = run_dir / TRACE_DIR_NAME
        if not trace_dir.exists():
            continue
        result = load_json(result_path)
        if result.get("status") != "ok":
            continue
        score = score_from_result(result)
        items.append({
            "run_dir": run_dir,
            "episode_idx": int(result.get("episode_idx", len(items))),
            "timestamp": result.get("timestamp") or run_dir.name,
            "score": score,
            "status": result.get("status"),
        })
    return sorted(items, key=lambda x: (x["episode_idx"], x["timestamp"]))


def find_official_pdq_episodes_coop(
    *,
    pdq_root: str | Path,
    game_name: str,
    model: str,
) -> list[dict[str, Any]]:
    """Find coop self-cooperation PDQ matches.

    Looks under ``pdq/<game>/player1-<model>_vs_player2-<model>/`` for
    timestamp dirs containing ``player_1/result.json`` AND
    ``player_2/result.json``. Each such timestamp dir is one episode; both
    players' run dirs and the (joint) team score are returned together.
    """
    base = Path(pdq_root) / game_name
    if not base.exists():
        return []
    pair_label = f"player1-{model}_vs_player2-{model}"
    pair_root = base / pair_label
    if not pair_root.exists():
        return []

    items: list[dict[str, Any]] = []
    seen_match_dirs: set[Path] = set()
    for result_path in pair_root.rglob("player_1/result.json"):
        player1_dir = result_path.parent
        match_dir = player1_dir.parent
        if match_dir in seen_match_dirs:
            continue
        seen_match_dirs.add(match_dir)

        player2_dir = match_dir / "player_2"
        player2_result = player2_dir / "result.json"
        if not player2_result.exists():
            continue

        # Require both players to have ok status and reflection_trace.
        p1_res = load_json(result_path)
        p2_res = load_json(player2_result)
        if p1_res.get("status") != "ok" or p2_res.get("status") != "ok":
            continue
        if not (player1_dir / TRACE_DIR_NAME).exists():
            continue
        if not (player2_dir / TRACE_DIR_NAME).exists():
            continue

        # Team score: both players report the same team_score in result.score.
        # Take it from player_1 for definiteness; cross-check with player_2.
        team_score = score_from_result(p1_res)
        team_score_2 = score_from_result(p2_res)
        if (
            team_score is not None
            and team_score_2 is not None
            and abs(team_score - team_score_2) > 1e-6
        ):
            # Should not happen for SharedFloor (both report team_score),
            # but fall back to averaging if a future coop game disagrees.
            team_score = (team_score + team_score_2) / 2.0

        # Per-player own scores (informational; mean_score uses team_score).
        own_1 = p1_res.get("raw_player_score") or (
            (p1_res.get("scores") or {}).get("own")
        )
        own_2 = p2_res.get("raw_player_score") or (
            (p2_res.get("scores") or {}).get("own")
        )

        episode_idx = int(p1_res.get("episode_idx", len(items)))
        timestamp = p1_res.get("timestamp") or match_dir.name
        items.append({
            "match_dir": match_dir,
            "run_dir": match_dir,
            "players": [
                {"player_label": "player_1", "run_dir": player1_dir},
                {"player_label": "player_2", "run_dir": player2_dir},
            ],
            "episode_idx": episode_idx,
            "timestamp": timestamp,
            "score": team_score,
            "player_scores": [own_1, own_2],
            "status": "ok",
        })
    return sorted(items, key=lambda x: (x["episode_idx"], x["timestamp"]))
