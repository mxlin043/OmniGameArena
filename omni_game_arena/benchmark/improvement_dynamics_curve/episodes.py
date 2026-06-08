"""Episode execution for IDC rounds."""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from omni_game_arena.eval.recorder import StepRecorder
from omni_game_arena.eval.reflection_trace import write_reflection_trace_from_summary
from omni_game_arena.models import EmptyModelResponseError

from ..config import Experiment, PlayerSpec, TwoPlayerExperiment
from ..factory import build_agent_and_adapter
from ..games.base import GameSpec
from ..logging_utils import ExperimentLogContext
from ..metrics import compute_episode_metrics
from ..runner import _configure_lcrt_timing, _print_final_score, _run_episode, make_solo_env
from ..two_player import run_two_player_match
from .io import atomic_write_json, load_json
from .metrics import score_from_result

logger = logging.getLogger(__name__)
TRACE_DIR_NAME = "reflection_trace"
IDC_EPISODE_RECORD_NAME = "idc_episode_record.json"


def run_episode_set(
    *,
    round_dir: str | Path,
    round_idx: int,
    skill_text: str,
    game: GameSpec,
    exp_template: Experiment,
    n_episodes: int,
    clock_mode: str = "pdq",
    live_viewer=None,
    log_vlm: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    api_debug: bool = False,
) -> list[dict[str, Any]]:
    episodes_dir = Path(round_dir) / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    completed: list[dict[str, Any]] = []

    for ep_idx in range(n_episodes):
        ep_id = f"ep_{ep_idx:02d}"
        ep_dir = episodes_dir / ep_id
        existing = _load_complete_episode(ep_dir)
        if existing is not None:
            completed.append(existing)
            _notify_progress(
                progress_callback,
                {
                    "event": "episode_skipped",
                    "round_idx": round_idx,
                    "episode_idx": ep_idx,
                    "episode_id": ep_id,
                    "n_episodes": n_episodes,
                    "score": existing.get("score"),
                    "completed": len(completed),
                },
            )
            continue

        if ep_dir.exists():
            _notify_progress(
                progress_callback,
                {
                    "event": "episode_overwrite_incomplete",
                    "round_idx": round_idx,
                    "episode_idx": ep_idx,
                    "episode_id": ep_id,
                    "n_episodes": n_episodes,
                    "completed": len(completed),
                },
            )
            shutil.rmtree(ep_dir)
        exp = Experiment(
            game=exp_template.game,
            env=exp_template.env,
            agent=exp_template.agent,
            params=exp_template.params,
            episode_idx=ep_idx,
            run_id=f"idc/round_{round_idx:02d}/{ep_id}",
        )
        _notify_progress(
            progress_callback,
            {
                "event": "episode_start",
                "round_idx": round_idx,
                "episode_idx": ep_idx,
                "episode_id": ep_id,
                "n_episodes": n_episodes,
                "completed": len(completed),
            },
        )
        is_coop = (getattr(game, "mode", "solo") or "solo") == "coop"
        if is_coop:
            result = run_idc_coop_episode(
                exp=exp,
                ep_dir=ep_dir,
                game=game,
                skill_text=skill_text,
                clock_mode=clock_mode,
                viewer=live_viewer,
                log_vlm=log_vlm,
                api_debug=api_debug,
            )
        else:
            result = run_idc_episode(
                exp=exp,
                ep_dir=ep_dir,
                game=game,
                skill_text=skill_text,
                clock_mode=clock_mode,
                viewer=live_viewer,
                log_vlm=log_vlm,
                api_debug=api_debug,
            )
        if result.get("status") == "ok":
            record = _episode_record(ep_id, ep_dir, result)
            completed.append(record)
            _notify_progress(
                progress_callback,
                {
                    "event": "episode_complete",
                    "round_idx": round_idx,
                    "episode_idx": ep_idx,
                    "episode_id": ep_id,
                    "n_episodes": n_episodes,
                    "score": record.get("score"),
                    "completed": len(completed),
                },
            )
        else:
            _notify_progress(
                progress_callback,
                {
                    "event": "episode_failed",
                    "round_idx": round_idx,
                    "episode_idx": ep_idx,
                    "episode_id": ep_id,
                    "n_episodes": n_episodes,
                    "completed": len(completed),
                    "status": result.get("status"),
                    "error": result.get("error"),
                },
            )
            raise RuntimeError(
                f"IDC episode failed: round={round_idx} ep={ep_id} "
                f"status={result.get('status')} error={result.get('error')}"
            )
    return completed


def run_idc_episode(
    *,
    exp: Experiment,
    ep_dir: str | Path,
    game: GameSpec,
    skill_text: str,
    clock_mode: str,
    viewer=None,
    log_vlm: bool = False,
    api_debug: bool = False,
) -> dict[str, Any]:
    ep_dir = Path(ep_dir)
    ep_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "run_id": exp.run_id,
        "run_dir": str(ep_dir),
        "timestamp": ep_dir.name,
        "game": exp.game,
        "agent": asdict(exp.agent),
        "parameters": asdict(exp.params),
        "episode_idx": exp.episode_idx,
        "status": "pending",
        "metrics": None,
        "idc": {
            "skill_chars": len(skill_text or ""),
        },
    }

    with ExperimentLogContext(
        str(ep_dir),
        name="experiment",
        game_name=game.name,
        write_file=True,
    ) as log:
        env, agent, adapter, recorder = None, None, None, None
        terminal_info: dict | None = None
        t_start = time.time()
        try:
            agent, adapter = build_agent_and_adapter(exp.agent, exp.params, game)
            if api_debug:
                _attach_api_debug(agent, ep_dir, log)
            agent.system_experience = skill_text or ""
            _configure_lcrt_timing(agent, clock_mode == "lcrt", log)
            env = make_solo_env(exp.env, exp.params, adapter, game)
            recorder = StepRecorder(output_dir=str(ep_dir))
            terminal_info = _run_episode(
                env,
                agent,
                adapter,
                exp,
                recorder,
                log,
                viewer=viewer,
                log_vlm=log_vlm,
                clock_mode=clock_mode,
                video_recorder=None,
            )
            result["status"] = "ok"
        except EmptyModelResponseError as e:
            result["status"] = "skipped"
            result["skip_reason"] = "model_empty_response"
            result["error"] = f"{type(e).__name__}: {e}"
            terminal_info = {"done_reason": "model_empty_response"}
        except Exception as e:  # noqa: BLE001
            result["status"] = "error"
            result["error"] = f"{type(e).__name__}: {e}"
            result["traceback"] = traceback.format_exc()
            log.error("IDC episode failed: %s\n%s", e, result["traceback"])
        finally:
            wall = time.time() - t_start
            _print_final_score(env, exp)
            if env is not None:
                try:
                    env.close()
                except Exception as e:  # noqa: BLE001
                    log.warning("env.close() raised: %s", e)
            if recorder is not None:
                try:
                    recorder.save_summary()
                except Exception as e:  # noqa: BLE001
                    log.warning("recorder.save_summary() raised: %s", e)
                records = recorder.records
            else:
                records = []
            result["metrics"] = compute_episode_metrics(records, terminal_info, wall, game)
            atomic_write_json(ep_dir / "result.json", result)
            if records:
                try:
                    write_reflection_trace_from_summary(
                        ep_dir,
                        result,
                        records,
                        clock_mode=clock_mode,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("reflection_trace generation raised: %s", e)
    if result.get("status") == "ok" and not _reflection_trace_complete(ep_dir):
        result["status"] = "error"
        result["error"] = "Missing reflection_trace after completed episode."
        atomic_write_json(ep_dir / "result.json", result)
    if result.get("status") == "ok":
        record = _episode_record(ep_dir.name, ep_dir, result)
        atomic_write_json(
            ep_dir / TRACE_DIR_NAME / IDC_EPISODE_RECORD_NAME,
            record,
        )
    return result


def run_idc_coop_episode(
    *,
    exp: Experiment,
    ep_dir: str | Path,
    game: GameSpec,
    skill_text: str,
    clock_mode: str,
    viewer=None,
    log_vlm: bool = False,
    api_debug: bool = False,
) -> dict[str, Any]:
    """Run one coop (self-cooperation) episode for IDC.

    Builds a TwoPlayerExperiment with BOTH player slots using the same
    agent profile from ``exp.agent``, injects the same ``skill_text``
    into both player agents' ``system_experience``, and runs the standard
    two-player match into ``ep_dir`` (overriding the default output_root
    construction so ep_dir/player_1/ and ep_dir/player_2/ are produced).

    After the match, synthesizes a top-level ``ep_dir/result.json`` with
    joint team score and writes ``ep_dir/idc_coop_record.json`` so resume
    detection works.
    """
    ep_dir = Path(ep_dir)
    ep_dir.mkdir(parents=True, exist_ok=True)

    # Player 1 on env.port, player 2 on env.port + 1. Matches the
    # SharedFloor convention (12345 / 12346).
    #
    # IMPORTANT: PlayerSpec.player_index is 0-based INTERNAL by the
    # codebase-wide convention in config.py (_player_display_id adds +1
    # for display / directory names). Passing 1, 2 here used to produce
    # player_2/ and player_3/ directories and "player 2 / player 3" log
    # labels, which is inconsistent with PDQ baseline (player_1/player_2)
    # and breaks the agentic reflector that expects player_1/player_2.
    p1_port = exp.env.port
    p2_port = exp.env.port + 1
    task = exp.env.task or game.default_task

    two_exp = TwoPlayerExperiment(
        game=exp.game,
        env=exp.env,
        players=[
            PlayerSpec(
                player_index=0, agent=exp.agent,
                host=exp.env.host, port=p1_port, task=task,
            ),
            PlayerSpec(
                player_index=1, agent=exp.agent,
                host=exp.env.host, port=p2_port, task=task,
            ),
        ],
        params=exp.params,
        episode_idx=exp.episode_idx,
        run_id=f"{exp.run_id}/coop",
    )

    try:
        match_result = run_two_player_match(
            exp=two_exp,
            output_root=str(ep_dir.parent),  # unused due to run_dir override
            game=game,
            viewer=viewer,
            log_vlm=log_vlm,
            api_debug=api_debug,
            clock_mode=clock_mode,
            record_video=False,
            run_dir=str(ep_dir),
            skill_text=skill_text or "",
        )
    except Exception as exc:  # noqa: BLE001
        result: dict[str, Any] = {
            "run_id": exp.run_id,
            "run_dir": str(ep_dir),
            "game": exp.game,
            "agent": asdict(exp.agent),
            "parameters": asdict(exp.params),
            "episode_idx": exp.episode_idx,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "mode": "coop",
            "idc": {"skill_chars": len(skill_text or "")},
        }
        atomic_write_json(ep_dir / "result.json", result)
        return result

    # Extract joint score from per-player results. SharedFloor reports
    # team_score on each player's result.score, so either is fine; we
    # also fall back to coop_total_score / scores.team if present.
    player_results = match_result.get("player_results") or {}
    team_score: float | None = None
    player_scores: list[float | None] = []
    for pid in sorted(player_results.keys()):
        pr = player_results[pid]
        score = pr.get("score")
        if score is not None:
            try:
                team_score = float(score)
            except (TypeError, ValueError):
                pass
        own = (pr.get("scores") or {}).get("own") if isinstance(pr, dict) else None
        if own is None:
            own = pr.get("raw_player_score") if isinstance(pr, dict) else None
        player_scores.append(own)
    if team_score is None:
        team_score = match_result.get("coop_total_score")

    status = match_result.get("status") or "ok"
    if all(
        (player_results.get(pid, {}) or {}).get("status") == "ok"
        for pid in player_results
    ):
        # Only mark ok if both player runs succeeded.
        status = "ok"
    elif status not in {"error", "interrupted", "skipped"}:
        status = "error"

    result = {
        "run_id": exp.run_id,
        "run_dir": str(ep_dir),
        "game": exp.game,
        "agent": asdict(exp.agent),
        "parameters": asdict(exp.params),
        "episode_idx": exp.episode_idx,
        "status": status,
        "score": team_score,
        "player_scores": player_scores,
        "mode": "coop",
        "metrics": {"game": {"score": team_score}},
        "idc": {"skill_chars": len(skill_text or "")},
    }
    atomic_write_json(ep_dir / "result.json", result)

    # Resume marker: idc_coop_record.json mirrors what _episode_record
    # produces, but is written outside any reflection_trace dir (which is
    # per-player in coop). _reflection_trace_complete and
    # _load_complete_episode both already check for this layout.
    if status == "ok":
        record = _episode_record(ep_dir.name, ep_dir, result)
        atomic_write_json(ep_dir / "idc_coop_record.json", record)

    return result


def _load_complete_episode(ep_dir: Path) -> dict[str, Any] | None:
    if not _reflection_trace_complete(ep_dir):
        return None

    # Solo path uses ep_dir/reflection_trace/idc_episode_record.json.
    # Coop path uses ep_dir/idc_coop_record.json (no top-level trace).
    record_path = ep_dir / TRACE_DIR_NAME / IDC_EPISODE_RECORD_NAME
    coop_record_path = ep_dir / "idc_coop_record.json"
    if record_path.exists():
        record = load_json(record_path)
        if record.get("status") == "ok":
            return record
    if coop_record_path.exists():
        record = load_json(coop_record_path)
        if record.get("status") == "ok":
            return record

    result_path = ep_dir / "result.json"
    if not result_path.exists():
        return None
    result = load_json(result_path)
    if result.get("status") != "ok":
        return None
    return _episode_record(ep_dir.name, ep_dir, result)


def _episode_record(ep_id: str, ep_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": ep_id,
        "run_dir": str(ep_dir),
        "score": score_from_result(result),
        "status": result.get("status"),
        "episode_idx": result.get("episode_idx"),
    }


def _reflection_trace_complete(ep_dir: Path) -> bool:
    """Detect a complete episode for both solo (single reflection_trace)
    and coop (per-player reflection_trace) layouts.
    """
    trace_dir = ep_dir / TRACE_DIR_NAME
    solo_ok = (
        (trace_dir / "manifest.json").exists()
        and (trace_dir / "steps.jsonl").exists()
    )
    if solo_ok:
        return True
    # Coop: every player_N/ subdir must have its own reflection_trace.
    player_dirs = sorted(p for p in ep_dir.glob("player_*") if p.is_dir())
    if not player_dirs:
        return False
    for p in player_dirs:
        ptrace = p / TRACE_DIR_NAME
        if not (ptrace / "manifest.json").exists():
            return False
        if not (ptrace / "steps.jsonl").exists():
            return False
    return True


def _notify_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception as exc:  # noqa: BLE001
        logger.debug("IDC progress callback failed: %s", exc)


def _attach_api_debug(agent, ep_dir: Path, log) -> None:
    backend = getattr(agent, "backend", None)
    if backend is None:
        return
    try:
        from omni_game_arena.utils.api_debug import ApiDebugLogger

        backend.debug_logger = ApiDebugLogger(ep_dir / "api_debug")
        log.info("api_debug enabled -> %s", ep_dir / "api_debug")
    except Exception as exc:  # noqa: BLE001
        log.warning("api_debug setup failed: %s", exc)
