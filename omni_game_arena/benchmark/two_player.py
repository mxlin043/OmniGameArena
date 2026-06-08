"""Asynchronous two-player match runner.

PvP and Coop are represented as two independent SoloEnv loops running in
parallel. There is no step barrier: the faster agent acts more often, which
preserves the real-time evaluation pressure.
"""

from __future__ import annotations

import json
import logging
import os
import heapq
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any

from omni_game_arena.env.client_ue5 import UE5Client
from omni_game_arena.eval.recorder import StepRecorder
from omni_game_arena.eval.recorder import compact_action
from omni_game_arena.eval.reflection_trace import write_reflection_trace_from_summary
from omni_game_arena.eval.video_recorder import VideoRecorder
from omni_game_arena.models import EmptyModelResponseError

from .config import ParamsPoint, EnvSpec, PlayerSpec, TwoPlayerExperiment
from .factory import build_agent_and_adapter
from .frame_pack_wrapper import last_stats as _framepack_last_stats
from .games.base import GameSpec
from .logging_utils import (
    ExperimentLogContext,
    benchmark_logger_name,
    reserve_timestamped_run_dir,
)
from .metrics import compute_episode_metrics
from .runner import _split_vlm_response, make_solo_env


def _logger_for(game: GameSpec) -> logging.Logger:
    return logging.getLogger(f"{benchmark_logger_name(game.name)}.runner")


class _StopController:
    def __init__(self):
        self.event = threading.Event()
        self.reason = ""
        self.player_index: int | None = None
        self._lock = threading.Lock()

    def request(self, reason: str, player_index: int | None = None) -> None:
        with self._lock:
            if not self.event.is_set():
                self.reason = reason
                self.player_index = player_index
                self.event.set()


@dataclass
class _PlayerRuntimeState:
    player: PlayerSpec
    player_dir: str
    recorder: StepRecorder
    t_start: float = field(default_factory=time.time)
    step: int = 0
    status: str = "pending"
    error: str | None = None
    terminal_info: dict | None = None
    score: float | None = None
    env: Any | None = None
    video: dict | None = None
    finalized: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _PausedPlayerRuntime:
    player: PlayerSpec
    state: _PlayerRuntimeState
    agent: Any
    adapter: Any
    env: Any
    obs: dict
    task: str
    schema: dict
    step: int = 0
    status: str = "pending"
    error: str | None = None
    terminal_info: dict | None = None
    score: float | None = None
    video_recorder: VideoRecorder | None = None


@dataclass
class _DecisionResult:
    player_index: int
    step: int
    obs: dict
    action: dict
    raw_response: str | None
    reason_text: str
    action_text: str
    act_latency_s: float
    lcrt_decision_delay_s: float
    ready_at_s: float = 0.0


@dataclass
class _ActiveTimedAction:
    decision: _DecisionResult
    action_state: Any
    started_at_s: float
    next_event_at_s: float
    phase: str = "input"


def _push_decision_video_step(
    runtime: _PausedPlayerRuntime,
    decision: _DecisionResult,
) -> None:
    recorder = runtime.video_recorder
    if recorder is None or not recorder.with_text_panel:
        return
    recorder.push_step(
        decision.step,
        decision.reason_text,
        decision.action_text or compact_action(decision.action) or "",
    )


def run_two_player_benchmark(
    experiments: list[TwoPlayerExperiment],
    output_root: str,
    game: GameSpec,
    live: bool = False,
    log_vlm: bool = False,
    api_debug: bool = False,
    live_vlm_only: bool = False,
    live_fps: int = 30,
    clock_mode: str = "realtime",
    record_video: bool = False,
    video_fps: int = 30,
    video_with_thinking: bool = False,
    video_thinking_layout: str = "side",
    flat_output: bool = False,
) -> dict:
    """Run every two-player match experiment."""
    clock_mode = _normalize_two_player_clock_mode(clock_mode)
    logger = _logger_for(game)
    os.makedirs(output_root, exist_ok=True)
    logger.info(
        "Starting two-player benchmark: game=%s | %d match(es) | output_root=%s | clock_mode=%s",
        game.name, len(experiments), output_root, clock_mode,
    )

    viewer = None
    if live:
        from omni_game_arena.utils.two_player_viewer import TwoPlayerLiveViewer

        viewer = TwoPlayerLiveViewer(
            title=f"Omni Game Arena Live - {game.name}",
            show_progress_panel=False,  # standalone benchmark: no IDC progress panel
        )
        viewer.start()
        logger.info("Two-player live viewer enabled")

    results: list[dict] = []
    try:
        for i, exp in enumerate(experiments, start=1):
            logger.info("----- [%d/%d] %s -----", i, len(experiments), exp.run_id)
            result = run_two_player_match(
                exp,
                output_root,
                game,
                viewer=viewer,
                log_vlm=log_vlm,
                api_debug=api_debug,
                live_vlm_only=live_vlm_only,
                live_fps=live_fps,
                clock_mode=clock_mode,
                record_video=record_video,
                video_fps=video_fps,
                video_with_thinking=video_with_thinking,
                video_thinking_layout=video_thinking_layout,
                flat_output=flat_output,
            )
            results.append(result)
            if result.get("status") == "interrupted":
                logger.warning(
                    "Benchmark interrupted by user at [%d/%d]", i, len(experiments)
                )
                break
    finally:
        if viewer is not None:
            viewer.stop()

    return _finalize_summary(results, game)


def run_two_player_match(
    exp: TwoPlayerExperiment,
    output_root: str,
    game: GameSpec,
    viewer=None,
    log_vlm: bool = False,
    api_debug: bool = False,
    live_vlm_only: bool = False,
    live_fps: int = 30,
    clock_mode: str = "realtime",
    record_video: bool = False,
    video_fps: int = 30,
    video_with_thinking: bool = False,
    video_thinking_layout: str = "side",
    flat_output: bool = False,
    run_dir: str | None = None,
    skill_text: str | None = None,
) -> dict:
    """Run one asynchronous two-player match.

    ``run_dir`` overrides the default ``output_root/<slug>/<YYYYMMDD_HHMMSS>``
    construction. Used by IDC (which wants ``ep_NN/``).

    ``skill_text`` is injected into every player agent's
    ``system_experience`` (or analogous skill-prompt field) right after
    construction. None = no injection (vanilla benchmark behavior).
    """
    clock_mode = _normalize_two_player_clock_mode(clock_mode)
    params_id = exp.params.short_id()
    players_slug = _players_slug(exp.players)
    if run_dir is None:
        if flat_output:
            parent_dir = output_root
        else:
            parent_dir = os.path.join(output_root, players_slug)
        run_dir, timestamp = reserve_timestamped_run_dir(parent_dir)
    else:
        timestamp = os.path.basename(os.path.normpath(run_dir))
    print(f"[save] {run_dir}", flush=True)

    os.makedirs(run_dir, exist_ok=True)
    result: dict[str, Any] = {
        "run_id": f"{players_slug}/{params_id}",
        "run_dir": run_dir,
        "timestamp": timestamp,
        "game": exp.game,
        "mode": game.mode,
        "players": [_player_spec_output_dict(p) for p in exp.players],
        "parameters": asdict(exp.params),
        "episode_idx": exp.episode_idx,
        "clock_mode": clock_mode,
        "status": "pending",
        "stop_reason": None,
        "winner": None,
        "coop_total_score": None,
        "player_results": {},
        "error": None,
    }

    with ExperimentLogContext(
        run_dir,
        name="match",
        game_name=game.name,
        write_file=api_debug,
    ) as log:
        stop = _StopController()
        events: list[dict] = []
        events_lock = threading.Lock()
        player_results: dict[int, dict] = {}
        player_results_lock = threading.Lock()
        t_match_start = time.time()

        # Prefix each player's status line with its own UE5 endpoint (like solo).
        if viewer is not None and hasattr(viewer, "set_player_endpoint"):
            for _p in exp.players:
                viewer.set_player_endpoint(
                    _p.player_index, f"ip={_p.host} port={_p.port}"
                )

        player_states = {
            player.player_index: _PlayerRuntimeState(
                player=player,
                player_dir=os.path.join(
                    run_dir, _player_dir_name(player.player_index),
                ),
                recorder=StepRecorder(
                    output_dir=os.path.join(
                        run_dir, _player_dir_name(player.player_index),
                    ),
                ),
            )
            for player in exp.players
        }
        threads: list[threading.Thread] = []
        thread_pids: dict[str, int] = {}

        try:
            _open_match_map(exp, log)
            if clock_mode == "realtime":
                for player in exp.players:
                    thread = threading.Thread(
                        target=_run_player_loop,
                        kwargs={
                            "exp": exp,
                            "game": game,
                            "player": player,
                            "state": player_states[player.player_index],
                            "stop": stop,
                            "events": events,
                            "events_lock": events_lock,
                            "player_results": player_results,
                            "player_results_lock": player_results_lock,
                            "match_start": t_match_start,
                            "viewer": viewer,
                            "log": log,
                            "log_vlm": log_vlm,
                            "live_vlm_only": live_vlm_only,
                            "live_fps": live_fps,
                            "record_video": record_video,
                            "video_fps": video_fps,
                            "video_with_thinking": video_with_thinking,
                            "video_thinking_layout": video_thinking_layout,
                        },
                        name=f"two-player-p{player.player_index}",
                        daemon=True,
                    )
                    threads.append(thread)
                    thread_pids[thread.name] = player.player_index
                for thread in threads:
                    thread.start()
                _wait_for_player_threads(threads, stop, log)
            else:
                _run_paused_two_player_match(
                    exp=exp,
                    game=game,
                    player_states=player_states,
                    stop=stop,
                    events=events,
                    events_lock=events_lock,
                    player_results=player_results,
                    player_results_lock=player_results_lock,
                    match_start=t_match_start,
                    viewer=viewer,
                    log=log,
                    log_vlm=log_vlm,
                    api_debug=api_debug,
                    live_vlm_only=live_vlm_only,
                    live_fps=live_fps,
                    clock_mode=clock_mode,
                    record_video=record_video,
                    video_fps=video_fps,
                    video_with_thinking=video_with_thinking,
                    video_thinking_layout=video_thinking_layout,
                    skill_text=skill_text,
                )
        except KeyboardInterrupt:
            stop.request("keyboard_interrupt")
            result["status"] = "interrupted"
        except EmptyModelResponseError as exc:
            if not stop.event.is_set():
                stop.request("model_empty_response")
            result["status"] = "skipped"
            result["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("Skipping match: %s", exc)
        except Exception as exc:  # noqa: BLE001
            stop.request("match_error")
            result["status"] = "error"
            result["error"] = f"{type(exc).__name__}: {exc}"
            log.error("Match failed: %s\n%s", exc, traceback.format_exc())
        finally:
            alive_after_stop = []
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=0.2)
                if thread.is_alive():
                    alive_after_stop.append(thread.name)

            if alive_after_stop:
                _refresh_player_scores(
                    player_states,
                    [thread_pids[name] for name in alive_after_stop],
                    log,
                )
                for thread_name in alive_after_stop:
                    pid = thread_pids.get(thread_name)
                    if pid is None:
                        continue
                    _publish_player_result(
                        player_states[pid],
                        player_results,
                        player_results_lock,
                        game,
                        log,
                        status_override="stopping_timeout",
                        terminal_info_override=_stopping_terminal_info(
                            player_states[pid], stop,
                        ),
                        final=False,
                    )

            if alive_after_stop:
                log.warning(
                    "Player thread(s) still running after stop signal: %s",
                    ", ".join(alive_after_stop),
                )
                if result["status"] == "pending":
                    result["status"] = "stopping_timeout"

            result["player_results"] = {
                _player_result_key(pid): player_results.get(
                    pid, _missing_player_result(pid),
                )
                for pid in sorted(p.player_index for p in exp.players)
            }
            result["stop_reason"] = {
                "reason": stop.reason or "completed",
                "player_index": _optional_player_display_id(stop.player_index),
                "player_id": _optional_player_display_id(stop.player_index),
                "player_label": _optional_player_result_key(stop.player_index),
                "ue_player_index": stop.player_index,
            }
            if result["status"] == "pending":
                result["status"] = _match_status(result["player_results"])

            _apply_score_summary(result, game)
            _apply_player_score_context(result, game)
            _apply_player_outcomes(result, game)
            _rewrite_player_result_artifacts(result, game, log)

            with open(os.path.join(run_dir, "timeline.jsonl"), "w", encoding="utf-8") as f:
                for event in sorted(events, key=lambda e: e.get("t_s", 0)):
                    f.write(json.dumps(_event_output_dict(event), ensure_ascii=False) + "\n")

            with open(os.path.join(run_dir, "match_result.json"), "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            _print_match_result(result)

    return result


def _normalize_two_player_clock_mode(clock_mode: str) -> str:
    mode = (clock_mode or "realtime").strip().lower()
    aliases = {
        "rt": "realtime",
        "real-time": "realtime",
        "real_time": "realtime",
        "paused": "pdq",
        "pause": "pdq",
        "paused_decision_quality": "pdq",
        "latency_controlled": "lcrt",
        "latency-controlled": "lcrt",
        "latency_controlled_real_time": "lcrt",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"realtime", "pdq", "lcrt"}:
        raise ValueError(
            f"Unsupported two-player clock mode {clock_mode!r}; "
            "expected 'realtime', 'pdq', or 'lcrt'"
        )
    return mode


def _viewer_player_label(player_index: int) -> str:
    return f"player {player_index + 1}"


def _player_display_id(player_index: int) -> int:
    return player_index + 1


def _optional_player_display_id(player_index: int | None) -> int | None:
    if player_index is None:
        return None
    return _player_display_id(player_index)


def _player_result_key(player_index: int) -> str:
    return f"player_{_player_display_id(player_index)}"


def _optional_player_result_key(player_index: int | None) -> str | None:
    if player_index is None:
        return None
    return _player_result_key(player_index)


def _player_dir_name(player_index: int) -> str:
    return _player_result_key(player_index)


def _player_slug_part(player: PlayerSpec) -> str:
    return f"player{_player_display_id(player.player_index)}-{player.agent.model}"


def _player_spec_output_dict(player: PlayerSpec) -> dict:
    data = asdict(player)
    internal_index = player.player_index
    data["ue_player_index"] = internal_index
    data["player_index"] = _player_display_id(internal_index)
    data["player_id"] = _player_display_id(internal_index)
    data["player_label"] = _player_result_key(internal_index)
    return data


def _add_player_output_fields(info: dict, player_index: int) -> dict:
    info["player_index"] = _player_display_id(player_index)
    info["player_id"] = _player_display_id(player_index)
    info["player_label"] = _player_result_key(player_index)
    info["ue_player_index"] = player_index
    return info


def _terminal_info_output_dict(
    terminal_info: dict | None,
    player_index: int,
) -> dict | None:
    if terminal_info is None:
        return None
    info = dict(terminal_info)
    _add_player_output_fields(info, player_index)
    if "match_stop_player_index" in info:
        stop_index = info.get(
            "match_stop_ue_player_index",
            info.get("match_stop_player_index"),
        )
        info["match_stop_ue_player_index"] = stop_index
        info["match_stop_player_index"] = _optional_player_display_id(stop_index)
        info["match_stop_player_id"] = _optional_player_display_id(stop_index)
        info["match_stop_player_label"] = _optional_player_result_key(stop_index)
    return info


def _event_output_dict(event: dict) -> dict:
    out = dict(event)
    player_index = out.get("player_index")
    if player_index is not None:
        out["ue_player_index"] = player_index
        out["player_index"] = _player_display_id(player_index)
        out["player_id"] = _player_display_id(player_index)
        out["player_label"] = _player_result_key(player_index)
    return out


def _start_player_video(
    *,
    state: _PlayerRuntimeState,
    env,
    fps: int,
    with_thinking: bool,
    thinking_layout: str,
    log: logging.Logger,
    pid: int,
) -> VideoRecorder | None:
    try:
        recorder = VideoRecorder(
            os.path.join(state.player_dir, "episode.mp4"),
            fps=fps,
            with_text_panel=with_thinking,
            text_layout=thinking_layout,
        )
        recorder.start_streaming(env)
        log.info("player %s video recording -> %s", pid, recorder.output_path)
        return recorder
    except Exception as exc:  # noqa: BLE001
        video = {
            "path": os.path.join(state.player_dir, "episode.mp4"),
            "fps": fps,
            "frames": 0,
            "with_thinking": with_thinking,
            "thinking_layout": thinking_layout,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _update_player_state(state, video=video)
        log.warning("player %s video recording setup raised: %s", pid, exc)
        return None


def _stop_player_video(
    recorder: VideoRecorder | None,
    state: _PlayerRuntimeState,
    log: logging.Logger,
    pid: int,
) -> None:
    if recorder is None:
        return
    try:
        recorder.stop_streaming()
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s video_recorder.stop_streaming() raised: %s", pid, exc)
    video = {
        "path": recorder.output_path,
        "fps": recorder.fps,
        "frames": recorder.frame_count,
        "with_thinking": recorder.with_text_panel,
        "thinking_layout": recorder.text_layout,
        "error": recorder.error,
    }
    _update_player_state(state, video=video)
    log.info(
        "player %s video recording -> %s (%d frames)",
        pid, recorder.output_path, recorder.frame_count,
    )


def _run_paused_two_player_match(
    *,
    exp: TwoPlayerExperiment,
    game: GameSpec,
    player_states: dict[int, _PlayerRuntimeState],
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    player_results: dict[int, dict],
    player_results_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    api_debug: bool,
    live_vlm_only: bool,
    live_fps: int,
    clock_mode: str,
    record_video: bool,
    video_fps: int,
    video_with_thinking: bool,
    video_thinking_layout: str,
    skill_text: str | None = None,
) -> None:
    """Run PDQ or LCRT with the UE world paused during model calls."""
    runtimes: dict[int, _PausedPlayerRuntime] = {}
    try:
        for player in exp.players:
            pid = player.player_index
            state = player_states[pid]
            _update_player_state(state, t_start=time.time())

            agent, adapter = build_agent_and_adapter(
                player.agent, exp.params, game, player_index=pid,
            )
            if skill_text is not None and hasattr(agent, "system_experience"):
                agent.system_experience = skill_text
            if api_debug:
                _attach_api_debug(agent, state.player_dir, log, pid)

            env = make_solo_env(
                _env_for_player(exp.env, player),
                exp.params,
                adapter,
                game,
            )
            _update_player_state(state, env=env)
            obs = env.reset()
            reset_agent = getattr(agent, "reset", None)
            if callable(reset_agent):
                reset_agent()
            elif hasattr(agent, "reset_history"):
                agent.reset_history()

            runtime = _PausedPlayerRuntime(
                player=player,
                state=state,
                agent=agent,
                adapter=adapter,
                env=env,
                obs=obs,
                task=player.task or exp.env.task,
                schema=adapter.action_schema,
            )
            runtimes[pid] = runtime

            if record_video:
                runtime.video_recorder = _start_player_video(
                    state=state,
                    env=env,
                    fps=video_fps,
                    with_thinking=video_with_thinking,
                    thinking_layout=video_thinking_layout,
                    log=log,
                    pid=pid,
                )

            _append_event(
                events, events_lock, match_start,
                {"event": "reset", "player_index": pid, "step": 0},
            )
            if viewer is not None:
                # Wipe last episode's per-player thinking log so the new
                # episode starts fresh. Mirrors solo LiveViewer.clear_log
                # which the benchmark runner calls per episode. Without
                # this, coop runs accumulate hundreds of KB of step text
                # across rounds and Tk render falls behind / new steps
                # appear stuck.
                if hasattr(viewer, "clear_player_logs"):
                    header = (
                        f"=== {_viewer_player_label(pid)} | {exp.run_id} ==="
                    )
                    viewer.clear_player_logs(pid, header=header)
                if live_vlm_only:
                    viewer.update_player(
                        pid, obs.get("image"), status=f"{_viewer_player_label(pid)} reset",
                    )
                else:
                    viewer.update_player(pid, status=f"{_viewer_player_label(pid)} reset")
                    if env.client is not None:
                        viewer.start_streaming_player(pid, env.client, fps=live_fps)

        _pause_all(runtimes.values())
        log.info("Paused two-player scheduler start | clock_mode=%s", clock_mode)
        if clock_mode == "pdq":
            _run_pdq_scheduler(
                runtimes=runtimes,
                exp=exp,
                game=game,
                stop=stop,
                events=events,
                events_lock=events_lock,
                match_start=match_start,
                viewer=viewer,
                log=log,
                log_vlm=log_vlm,
                live_vlm_only=live_vlm_only,
            )
        elif clock_mode == "lcrt":
            _run_lcrt_scheduler(
                runtimes=runtimes,
                exp=exp,
                game=game,
                stop=stop,
                events=events,
                events_lock=events_lock,
                match_start=match_start,
                viewer=viewer,
                log=log,
                log_vlm=log_vlm,
                live_vlm_only=live_vlm_only,
            )
        else:
            raise ValueError(f"Unexpected paused clock mode: {clock_mode}")
    except EmptyModelResponseError:
        skipped_pid = next(
            (
                pid
                for pid, runtime in runtimes.items()
                if runtime.status == "skipped"
            ),
            None,
        )
        stop.request("model_empty_response", skipped_pid)
        raise
    finally:
        for runtime in runtimes.values():
            pid = runtime.player.player_index
            if viewer is not None:
                try:
                    viewer.stop_streaming_player(pid)
                except Exception as exc:  # noqa: BLE001
                    log.warning("player %s viewer stream stop failed: %s", pid, exc)

            _stop_player_video(runtime.video_recorder, runtime.state, log, pid)

            try:
                if runtime.env.client is not None and runtime.env.client.score is None:
                    runtime.env.client.get_score()
                runtime.score = runtime.env.client.score
                _update_player_state(runtime.state, score=runtime.score)
            except Exception as exc:  # noqa: BLE001
                log.warning("player %s score query failed: %s", pid, exc)

            try:
                runtime.env.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("player %s env.close() raised: %s", pid, exc)

            if runtime.status == "pending":
                runtime.status = "stopped" if stop.event.is_set() else "ok"
            _update_player_state(
                runtime.state,
                status=runtime.status,
                error=runtime.error,
                terminal_info=runtime.terminal_info,
                score=runtime.score,
                step=runtime.step,
            )
            _publish_player_result(
                runtime.state,
                player_results,
                player_results_lock,
                game,
                log,
                final=True,
            )


def _run_pdq_scheduler(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> None:
    """Paused Decision Quality: lockstep decisions, simultaneous actions."""
    virtual_t = 0.0
    while not stop.event.is_set():
        active = [rt for rt in runtimes.values() if rt.status == "pending"]
        if not active:
            break

        decisions = _request_decisions_parallel(
            active,
            virtual_t=virtual_t,
            clock_mode="pdq",
            events=events,
            events_lock=events_lock,
            match_start=match_start,
            viewer=viewer,
            log=log,
            log_vlm=log_vlm,
            live_vlm_only=live_vlm_only,
        )
        if stop.event.is_set():
            break

        game_dt = _execute_decision_group(
            runtimes=runtimes,
            decisions=list(decisions.values()),
            virtual_t=virtual_t,
            clock_mode="pdq",
            exp=exp,
            game=game,
            stop=stop,
            events=events,
            events_lock=events_lock,
            match_start=match_start,
            viewer=viewer,
            log=log,
            live_vlm_only=live_vlm_only,
        )
        virtual_t = round(virtual_t + game_dt, 6)


def _run_lcrt_scheduler(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> None:
    """Latency-Controlled Real-Time using interruptible action chunks."""
    if not _all_runtimes_support_timed_actions(runtimes):
        log.warning(
            "LCRT timed action support is unavailable for at least one adapter; "
            "falling back to blocking LCRT execution."
        )
        _run_lcrt_scheduler_blocking(
            runtimes=runtimes,
            exp=exp,
            game=game,
            stop=stop,
            events=events,
            events_lock=events_lock,
            match_start=match_start,
            viewer=viewer,
            log=log,
            log_vlm=log_vlm,
            live_vlm_only=live_vlm_only,
        )
        return

    _run_lcrt_scheduler_strict(
        runtimes=runtimes,
        exp=exp,
        game=game,
        stop=stop,
        events=events,
        events_lock=events_lock,
        match_start=match_start,
        viewer=viewer,
        log=log,
        log_vlm=log_vlm,
        live_vlm_only=live_vlm_only,
    )


def _all_runtimes_support_timed_actions(
    runtimes: dict[int, _PausedPlayerRuntime],
) -> bool:
    return all(
        callable(getattr(runtime.adapter, "start_timed_action", None))
        for runtime in runtimes.values()
    )


def _run_lcrt_scheduler_strict(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> None:
    """Strict LCRT: start each ready action on the shared virtual timeline."""
    virtual_t = 0.0
    serial = 0
    pending: dict[int, _DecisionResult] = {}
    heap: list[tuple[float, int, int]] = []
    active: dict[int, _ActiveTimedAction] = {}

    def push_decision(decision: _DecisionResult) -> None:
        nonlocal serial
        pid = decision.player_index
        pending[pid] = decision
        heapq.heappush(heap, (decision.ready_at_s, serial, pid))
        serial += 1

    initial = _request_decisions_parallel(
        list(runtimes.values()),
        virtual_t=virtual_t,
        clock_mode="lcrt",
        events=events,
        events_lock=events_lock,
        match_start=match_start,
        viewer=viewer,
        log=log,
        log_vlm=log_vlm,
        live_vlm_only=live_vlm_only,
    )
    for decision in initial.values():
        push_decision(decision)

    try:
        while (pending or active) and not stop.event.is_set():
            new_decisions = _process_due_lcrt_action_events(
                active=active,
                runtimes=runtimes,
                virtual_t=virtual_t,
                exp=exp,
                game=game,
                stop=stop,
                events=events,
                events_lock=events_lock,
                match_start=match_start,
                viewer=viewer,
                log=log,
                log_vlm=log_vlm,
                live_vlm_only=live_vlm_only,
            )
            for decision in new_decisions:
                push_decision(decision)
            if stop.event.is_set():
                break

            if _has_ready_lcrt_decision(heap, pending, virtual_t):
                _poll_all_runtimes(
                    runtimes=runtimes,
                    virtual_t=virtual_t,
                    stop=stop,
                    game=game,
                    log=log,
                )
                if stop.event.is_set():
                    break
                _start_ready_lcrt_actions(
                    active=active,
                    pending=pending,
                    heap=heap,
                    runtimes=runtimes,
                    virtual_t=virtual_t,
                    events=events,
                    events_lock=events_lock,
                    match_start=match_start,
                    log=log,
                    stop=stop,
                )
                continue

            next_t = _next_lcrt_event_time(active, heap, pending)
            if next_t is None:
                break
            if next_t <= virtual_t + 1e-6:
                virtual_t = round(max(virtual_t, next_t), 6)
                continue

            _advance_all_game_time(runtimes.values(), next_t - virtual_t)
            virtual_t = round(next_t, 6)
    finally:
        for active_action in list(active.values()):
            action_state = active_action.action_state
            if hasattr(action_state, "cancel"):
                try:
                    action_state.cancel()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "player %s timed action cancel raised: %s",
                        active_action.decision.player_index, exc,
                    )


def _clean_lcrt_heap(
    heap: list[tuple[float, int, int]],
    pending: dict[int, _DecisionResult],
) -> None:
    while heap:
        ready_at, _seq, pid = heap[0]
        decision = pending.get(pid)
        if decision is not None and decision.ready_at_s == ready_at:
            return
        heapq.heappop(heap)


def _has_ready_lcrt_decision(
    heap: list[tuple[float, int, int]],
    pending: dict[int, _DecisionResult],
    virtual_t: float,
) -> bool:
    _clean_lcrt_heap(heap, pending)
    return bool(heap and heap[0][0] <= virtual_t + 1e-6)


def _next_lcrt_event_time(
    active: dict[int, _ActiveTimedAction],
    heap: list[tuple[float, int, int]],
    pending: dict[int, _DecisionResult],
) -> float | None:
    _clean_lcrt_heap(heap, pending)
    times: list[float] = []
    if heap:
        times.append(heap[0][0])
    times.extend(action.next_event_at_s for action in active.values())
    if not times:
        return None
    return min(times)


def _start_ready_lcrt_actions(
    *,
    active: dict[int, _ActiveTimedAction],
    pending: dict[int, _DecisionResult],
    heap: list[tuple[float, int, int]],
    runtimes: dict[int, _PausedPlayerRuntime],
    virtual_t: float,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    log: logging.Logger,
    stop: _StopController,
) -> None:
    while _has_ready_lcrt_decision(heap, pending, virtual_t):
        ready_at, _seq, pid = heapq.heappop(heap)
        decision = pending.pop(pid, None)
        if decision is None or decision.ready_at_s != ready_at:
            continue
        runtime = runtimes.get(pid)
        if runtime is None or runtime.status != "pending":
            continue
        if pid in active:
            pending[pid] = decision
            heapq.heappush(heap, (decision.ready_at_s, _seq, pid))
            return

        _append_event(
            events, events_lock, match_start,
            {
                "event": "action_start",
                "player_index": pid,
                "step": decision.step,
                "clock_mode": "lcrt",
                "virtual_t_s": round(virtual_t, 4),
                "ready_at_s": round(decision.ready_at_s, 4),
                "action": compact_action(decision.action),
            },
        )
        _push_decision_video_step(runtime, decision)
        try:
            action_state = runtime.adapter.start_timed_action(
                runtime.env.client,
                decision.action,
            )
        except Exception as exc:  # noqa: BLE001
            runtime.status = "error"
            runtime.error = f"{type(exc).__name__}: {exc}"
            _update_player_state(
                runtime.state,
                status=runtime.status,
                error=runtime.error,
                step=runtime.step,
                score=runtime.score,
                terminal_info=runtime.terminal_info,
            )
            stop.request("action_error", pid)
            raise

        delay_s = (
            0.0
            if getattr(action_state, "done", False)
            else float(action_state.next_delay_s())
        )
        active[pid] = _ActiveTimedAction(
            decision=decision,
            action_state=action_state,
            started_at_s=virtual_t,
            next_event_at_s=round(virtual_t + max(0.0, delay_s), 6),
        )
        log.info(
            "player=%s action start clock=lcrt step=%d vtime=%.2f ready_at=%.2f",
            pid, decision.step, virtual_t, decision.ready_at_s,
        )


def _process_due_lcrt_action_events(
    *,
    active: dict[int, _ActiveTimedAction],
    runtimes: dict[int, _PausedPlayerRuntime],
    virtual_t: float,
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> list[_DecisionResult]:
    new_decisions: list[_DecisionResult] = []
    while not stop.event.is_set():
        due = sorted(
            (
                active_action
                for active_action in active.values()
                if active_action.next_event_at_s <= virtual_t + 1e-6
            ),
            key=lambda action: (
                action.next_event_at_s,
                action.decision.player_index,
            ),
        )
        if not due:
            break

        for active_action in due:
            pid = active_action.decision.player_index
            if active.get(pid) is not active_action:
                continue
            if active_action.phase == "observe":
                active.pop(pid, None)
                new_decision = _finish_lcrt_timed_action(
                    active_action=active_action,
                    runtime=runtimes[pid],
                    virtual_t=virtual_t,
                    exp=exp,
                    game=game,
                    stop=stop,
                    events=events,
                    events_lock=events_lock,
                    match_start=match_start,
                    viewer=viewer,
                    log=log,
                    log_vlm=log_vlm,
                    live_vlm_only=live_vlm_only,
                )
                if new_decision is not None:
                    new_decisions.append(new_decision)
                if stop.event.is_set():
                    break
                continue

            try:
                active_action.action_state.advance_event()
            except Exception as exc:  # noqa: BLE001
                runtime = runtimes[pid]
                runtime.status = "error"
                runtime.error = f"{type(exc).__name__}: {exc}"
                _update_player_state(
                    runtime.state,
                    status=runtime.status,
                    error=runtime.error,
                    step=runtime.step,
                    score=runtime.score,
                    terminal_info=runtime.terminal_info,
                )
                stop.request("action_error", pid)
                raise

            if getattr(active_action.action_state, "done", False):
                obs_delay = float(
                    getattr(runtimes[pid].env, "obs_delay", 0.0) or 0.0
                )
                if obs_delay > 0:
                    active_action.phase = "observe"
                    active_action.next_event_at_s = round(virtual_t + obs_delay, 6)
                    continue

                active.pop(pid, None)
                new_decision = _finish_lcrt_timed_action(
                    active_action=active_action,
                    runtime=runtimes[pid],
                    virtual_t=virtual_t,
                    exp=exp,
                    game=game,
                    stop=stop,
                    events=events,
                    events_lock=events_lock,
                    match_start=match_start,
                    viewer=viewer,
                    log=log,
                    log_vlm=log_vlm,
                    live_vlm_only=live_vlm_only,
                )
                if new_decision is not None:
                    new_decisions.append(new_decision)
                if stop.event.is_set():
                    break
            else:
                delay_s = max(0.0, float(active_action.action_state.next_delay_s()))
                active_action.next_event_at_s = round(virtual_t + delay_s, 6)

    return new_decisions


def _finish_lcrt_timed_action(
    *,
    active_action: _ActiveTimedAction,
    runtime: _PausedPlayerRuntime,
    virtual_t: float,
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> _DecisionResult | None:
    decision = active_action.decision
    action_game_time_s = max(0.0, virtual_t - active_action.started_at_s)
    obs, done, info = _finish_paused_action_step(
        runtime=runtime,
        decision=decision,
        virtual_t=virtual_t,
        action_game_time_s=action_game_time_s,
        clock_mode="lcrt",
        exp=exp,
    )
    runtime.obs = obs
    runtime.terminal_info = info if done else runtime.terminal_info
    if info.get("score") is not None:
        runtime.score = info.get("score")

    runtime.state.recorder.record(decision.step, decision.obs, decision.action, done, info)
    if info.get("done_reason") == "game_over":
        runtime.state.recorder.record_terminal_observation(obs)

    if info.get("action_executed", True) is not False:
        runtime.step += 1
    _update_player_state(
        runtime.state,
        step=runtime.step,
        score=runtime.score,
        terminal_info=runtime.terminal_info,
    )
    _append_event(
        events, events_lock, match_start,
        {
            "event": "step",
            "player_index": decision.player_index,
            "step": decision.step,
            "clock_mode": "lcrt",
            "virtual_t_s": round(virtual_t, 4),
            "lcrt_decision_delay_s": round(decision.lcrt_decision_delay_s, 4),
            "action_game_time_s": round(action_game_time_s, 4),
            "action": compact_action(decision.action),
            "score": info.get("score"),
            "done": done,
            "done_reason": info.get("done_reason"),
        },
    )
    if viewer is not None:
        score_text = (
            f" score={info.get('score')}"
            if info.get("score") is not None
            else ""
        )
        viewer.update_player(
            decision.player_index,
            None,
            status=(
                f"{_viewer_player_label(decision.player_index)} step={runtime.step}"
                f"{score_text} vtime={virtual_t:.2f}s"
            ),
        )
    log.info(
        "player=%s step=%d clock=lcrt vtime=%.2f done=%s reason=%s",
        decision.player_index, decision.step, virtual_t, done, info.get("done_reason"),
    )

    if done:
        runtime.status = "ok"
        _update_player_state(
            runtime.state,
            status=runtime.status,
            terminal_info=runtime.terminal_info,
            score=runtime.score,
            step=runtime.step,
        )
        stop.request(info.get("done_reason") or "player_done", decision.player_index)
        return None

    if runtime.status != "pending" or stop.event.is_set():
        return None

    return _request_decision(
        runtime,
        virtual_t=virtual_t,
        clock_mode="lcrt",
        events=events,
        events_lock=events_lock,
        match_start=match_start,
        viewer=viewer,
        log=log,
        log_vlm=log_vlm,
        live_vlm_only=live_vlm_only,
    )


def _run_lcrt_scheduler_blocking(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> None:
    """Latency-Controlled Real-Time using per-call dynamic decision delays."""
    virtual_t = 0.0
    serial = 0
    pending: dict[int, _DecisionResult] = {}
    heap: list[tuple[float, int, int]] = []

    initial = _request_decisions_parallel(
        list(runtimes.values()),
        virtual_t=virtual_t,
        clock_mode="lcrt",
        events=events,
        events_lock=events_lock,
        match_start=match_start,
        viewer=viewer,
        log=log,
        log_vlm=log_vlm,
        live_vlm_only=live_vlm_only,
    )
    for pid, decision in initial.items():
        pending[pid] = decision
        heapq.heappush(heap, (decision.ready_at_s, serial, pid))
        serial += 1

    while pending and not stop.event.is_set():
        ready_at, _seq, pid = heapq.heappop(heap)
        decision = pending.get(pid)
        if decision is None or decision.ready_at_s != ready_at:
            continue

        if ready_at > virtual_t:
            gap = ready_at - virtual_t
            _advance_all_game_time(runtimes.values(), gap)
            virtual_t = ready_at
            _poll_all_runtimes(
                runtimes=runtimes,
                virtual_t=virtual_t,
                stop=stop,
                game=game,
                log=log,
            )
            if stop.event.is_set():
                break

        group = [decision]
        del pending[pid]
        while heap and heap[0][0] <= virtual_t + 1e-6:
            next_ready, _next_seq, next_pid = heapq.heappop(heap)
            next_decision = pending.get(next_pid)
            if next_decision is None or next_decision.ready_at_s != next_ready:
                continue
            group.append(next_decision)
            del pending[next_pid]

        game_dt = _execute_decision_group(
            runtimes=runtimes,
            decisions=group,
            virtual_t=virtual_t,
            clock_mode="lcrt",
            exp=exp,
            game=game,
            stop=stop,
            events=events,
            events_lock=events_lock,
            match_start=match_start,
            viewer=viewer,
            log=log,
            live_vlm_only=live_vlm_only,
        )
        virtual_t = round(virtual_t + game_dt, 6)
        _poll_all_runtimes(
            runtimes=runtimes,
            virtual_t=virtual_t,
            stop=stop,
            game=game,
            log=log,
        )
        if stop.event.is_set():
            break

        for decision in group:
            runtime = runtimes[decision.player_index]
            if runtime.status != "pending":
                continue
            new_decision = _request_decision(
                runtime,
                virtual_t=virtual_t,
                clock_mode="lcrt",
                events=events,
                events_lock=events_lock,
                match_start=match_start,
                viewer=viewer,
                log=log,
                log_vlm=log_vlm,
                live_vlm_only=live_vlm_only,
            )
            pending[runtime.player.player_index] = new_decision
            heapq.heappush(heap, (new_decision.ready_at_s, serial, runtime.player.player_index))
            serial += 1


def _request_decisions_parallel(
    runtimes: list[_PausedPlayerRuntime],
    *,
    virtual_t: float,
    clock_mode: str,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> dict[int, _DecisionResult]:
    if not runtimes:
        return {}
    results: dict[int, _DecisionResult] = {}
    with ThreadPoolExecutor(max_workers=len(runtimes)) as pool:
        futures = {
            pool.submit(
                _request_decision,
                runtime,
                virtual_t=virtual_t,
                clock_mode=clock_mode,
                events=events,
                events_lock=events_lock,
                match_start=match_start,
                viewer=viewer,
                log=log,
                log_vlm=log_vlm,
                live_vlm_only=live_vlm_only,
            ): runtime.player.player_index
            for runtime in runtimes
        }
        for future in as_completed(futures):
            decision = future.result()
            results[decision.player_index] = decision
    return results


def _request_decision(
    runtime: _PausedPlayerRuntime,
    *,
    virtual_t: float,
    clock_mode: str,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
) -> _DecisionResult:
    pid = runtime.player.player_index
    decision_obs = runtime.obs
    decision_step = runtime.step
    if runtime.video_recorder is not None and runtime.video_recorder.with_text_panel:
        runtime.video_recorder.push_thinking(decision_step)
    t_act = time.perf_counter()
    try:
        action = runtime.agent.act(decision_obs, runtime.task, runtime.schema)
    except EmptyModelResponseError as exc:
        act_latency = time.perf_counter() - t_act
        runtime.status = "skipped"
        runtime.error = f"{type(exc).__name__}: {exc}"
        runtime.terminal_info = {
            "done_reason": "model_empty_response",
            "skip_reason": "model_empty_response",
            "error": runtime.error,
            "step": runtime.step,
        }
        _add_player_output_fields(runtime.terminal_info, pid)
        _update_player_state(
            runtime.state,
            status=runtime.status,
            error=runtime.error,
            terminal_info=runtime.terminal_info,
            step=runtime.step,
            score=runtime.score,
        )
        _append_event(
            events, events_lock, match_start,
            {
                "event": "skipped",
                "player_index": pid,
                "step": decision_step,
                "reason": "model_empty_response",
                "latency_s": round(act_latency, 4),
            },
        )
        log.warning("player=%s skipping match: %s", pid, exc)
        raise
    act_latency = time.perf_counter() - t_act
    raw_response = getattr(runtime.agent, "last_vlm_response", None) or None
    reason_text, action_text = _split_vlm_response(raw_response or "")
    lcrt_decision_delay = _estimate_decision_latency(
        runtime=runtime,
        action=action,
        raw_response=raw_response,
        act_latency_s=act_latency,
        clock_mode=clock_mode,
    )
    decision = _DecisionResult(
        player_index=pid,
        step=decision_step,
        obs=decision_obs,
        action=action,
        raw_response=raw_response,
        reason_text=reason_text,
        action_text=action_text,
        act_latency_s=act_latency,
        lcrt_decision_delay_s=lcrt_decision_delay,
        ready_at_s=round(virtual_t + lcrt_decision_delay, 6),
    )
    _append_event(
        events, events_lock, match_start,
        {
            "event": "decision_ready",
            "player_index": pid,
            "step": decision_step,
            "clock_mode": clock_mode,
            "virtual_t_s": round(virtual_t, 4),
            "lcrt_decision_delay_s": round(lcrt_decision_delay, 4),
            "ready_at_s": round(decision.ready_at_s, 4),
        },
    )
    if viewer is not None:
        viewer.update_player(
            pid,
            decision_obs.get("image") if live_vlm_only else None,
            step=decision_step,
            reason=reason_text,
            action=action_text,
            status=(
                f"{_viewer_player_label(pid)} decision step={decision_step} "
                f"delay={lcrt_decision_delay:.2f}s"
            ),
        )
    log.info(
        "player=%s decision step=%d wall_decision_time=%.2fs lcrt_decision_delay=%.2fs ready_at=%.2f",
        pid, decision_step, act_latency, lcrt_decision_delay, decision.ready_at_s,
    )
    if log_vlm and raw_response:
        print(
            f"\n--- player {pid} VLM step={decision_step} ---\n"
            f"{raw_response}\n",
            flush=True,
        )
    return decision


def _estimate_decision_latency(
    *,
    runtime: _PausedPlayerRuntime,
    action: dict,
    raw_response: str | None,
    act_latency_s: float,
    clock_mode: str,
) -> float:
    """Return per-call LCRT delay. This hook is intentionally replaceable."""
    if clock_mode == "pdq":
        return 0.0
    for source in (runtime.agent, getattr(runtime.agent, "backend", None)):
        if source is None:
            continue
        for attr in ("last_decision_latency_s", "decision_latency_s"):
            value = getattr(source, attr, None)
            if value is None:
                continue
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return max(0.0, float(act_latency_s))


def _execute_decision_group(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    decisions: list[_DecisionResult],
    virtual_t: float,
    clock_mode: str,
    exp: TwoPlayerExperiment,
    game: GameSpec,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    live_vlm_only: bool,
) -> float:
    if not decisions:
        return 0.0

    for decision in decisions:
        _append_event(
            events, events_lock, match_start,
            {
                "event": "action_start",
                "player_index": decision.player_index,
                "step": decision.step,
                "clock_mode": clock_mode,
                "virtual_t_s": round(virtual_t, 4),
                "ready_at_s": round(decision.ready_at_s, 4),
                "action": compact_action(decision.action),
            },
        )
        runtime = runtimes.get(decision.player_index)
        if runtime is not None:
            _push_decision_video_step(runtime, decision)

    _resume_all(runtimes.values())
    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=len(decisions)) as pool:
            futures = [
                pool.submit(
                    runtimes[d.player_index].adapter.execute,
                    runtimes[d.player_index].env.client,
                    d.action,
                )
                for d in decisions
            ]
            for future in as_completed(futures):
                future.result()

        obs_delay = max(
            float(getattr(runtimes[d.player_index].env, "obs_delay", 0.0) or 0.0)
            for d in decisions
        )
        if obs_delay > 0:
            time.sleep(obs_delay)
    finally:
        game_dt = time.perf_counter() - t0
        _pause_all(runtimes.values())

    for decision in decisions:
        runtime = runtimes[decision.player_index]
        obs, done, info = _finish_paused_action_step(
            runtime=runtime,
            decision=decision,
            virtual_t=virtual_t,
            action_game_time_s=game_dt,
            clock_mode=clock_mode,
            exp=exp,
        )
        runtime.obs = obs
        runtime.terminal_info = info if done else runtime.terminal_info
        if info.get("score") is not None:
            runtime.score = info.get("score")

        runtime.state.recorder.record(decision.step, decision.obs, decision.action, done, info)
        if info.get("done_reason") == "game_over":
            runtime.state.recorder.record_terminal_observation(obs)

        action_executed = info.get("action_executed", True) is not False
        if action_executed:
            runtime.step += 1
        _update_player_state(
            runtime.state,
            step=runtime.step,
            score=runtime.score,
            terminal_info=runtime.terminal_info,
        )
        _append_event(
            events, events_lock, match_start,
            {
                "event": "step",
                "player_index": decision.player_index,
                "step": decision.step,
                "clock_mode": clock_mode,
                "virtual_t_s": round(virtual_t + game_dt, 4),
                "lcrt_decision_delay_s": round(decision.lcrt_decision_delay_s, 4),
                "action_game_time_s": round(game_dt, 4),
                "action": compact_action(decision.action),
                "score": info.get("score"),
                "done": done,
                "done_reason": info.get("done_reason"),
            },
        )
        if viewer is not None:
            score_text = (
                f" score={info.get('score')}"
                if info.get("score") is not None
                else ""
            )
            # The decision text is appended when the model response arrives.
            # After execution, only refresh the status bar; otherwise PDQ
            # shows each logical step twice in the live viewer.
            viewer.update_player(
                decision.player_index,
                None,
                status=(
                    f"{_viewer_player_label(decision.player_index)} step={runtime.step}"
                    f"{score_text} vtime={virtual_t + game_dt:.2f}s"
                ),
            )
        log.info(
            "player=%s step=%d clock=%s vtime=%.2f done=%s reason=%s",
            decision.player_index, decision.step, clock_mode,
            virtual_t + game_dt, done, info.get("done_reason"),
        )
        if done:
            runtime.status = "ok"
            _update_player_state(
                runtime.state,
                status=runtime.status,
                terminal_info=runtime.terminal_info,
                score=runtime.score,
                step=runtime.step,
            )
            stop.request(info.get("done_reason") or "player_done", decision.player_index)

    return max(0.0, game_dt)


def _finish_paused_action_step(
    *,
    runtime: _PausedPlayerRuntime,
    decision: _DecisionResult,
    virtual_t: float,
    action_game_time_s: float,
    clock_mode: str,
    exp: TwoPlayerExperiment,
) -> tuple[dict, bool, dict]:
    env = runtime.env
    client = env.client
    env.step_count += 1
    elapsed = time.time() - env.start_time
    obs = env._make_observation()

    try:
        client.get_score()
    except Exception:
        pass
    env._update_max_score_seen(client.score)

    try:
        if not client.game_over:
            client.check_game_over(timeout=0.5)
    except Exception:
        pass

    game_over = client.game_over
    agent_done = decision.action.get("done", False) if isinstance(decision.action, dict) else False
    step_limit = exp.env.max_steps > 0 and (runtime.step + 1) >= exp.env.max_steps
    done = bool(agent_done or step_limit or game_over)

    if game_over:
        done_reason = "game_over"
    elif agent_done:
        done_reason = "agent"
    elif step_limit:
        done_reason = "max_steps"
    else:
        done_reason = None

    if done:
        try:
            client.get_score()
        except Exception:
            pass
        env._update_max_score_seen(client.score)

    info = {
        "step": runtime.step + 1,
        "elapsed": elapsed,
        "action": decision.action,
        "done_reason": done_reason,
        "score": client.score,
        "survival_time": client.survival_time,
        "max_score_seen": env.max_score_seen,
        "action_executed": True,
        "terminal_timing": "after_action" if done else None,
        "clock_mode": clock_mode,
        "virtual_time_s": round(virtual_t, 6),
        "ready_at_s": decision.ready_at_s,
        "lcrt_decision_delay_s": round(decision.lcrt_decision_delay_s, 6),
        "act_latency_s": round(decision.act_latency_s, 6),
        "action_game_time_s": round(action_game_time_s, 6),
    }
    _add_player_output_fields(info, runtime.player.player_index)
    if decision.raw_response:
        info["vlm_response"] = decision.raw_response

    fp_stats = _framepack_last_stats(runtime.agent)
    if fp_stats is not None:
        info["frame_pack"] = {
            "kernel": fp_stats.kernel,
            "n_input": fp_stats.n_input_frames,
            "n_output": fp_stats.n_output_frames,
            "token_ratio": fp_stats.approx_token_ratio,
            "resolutions": fp_stats.resolutions,
        }
    return obs, done, info


def _advance_all_game_time(runtimes: list[_PausedPlayerRuntime] | Any, seconds: float) -> None:
    if seconds <= 0:
        return
    _resume_all(runtimes)
    try:
        time.sleep(seconds)
    finally:
        _pause_all(runtimes)


def _poll_all_runtimes(
    *,
    runtimes: dict[int, _PausedPlayerRuntime],
    virtual_t: float,
    stop: _StopController,
    game: GameSpec,
    log: logging.Logger,
) -> None:
    for runtime in runtimes.values():
        if runtime.status != "pending":
            continue
        client = getattr(runtime.env, "client", None)
        if client is None:
            continue
        try:
            client.get_score()
            runtime.score = client.score
            runtime.env._update_max_score_seen(client.score)
        except Exception as exc:  # noqa: BLE001
            log.debug("player %s score poll failed: %s", runtime.player.player_index, exc)
        try:
            client.check_game_over(timeout=0.2)
        except Exception as exc:  # noqa: BLE001
            log.debug("player %s game_over poll failed: %s", runtime.player.player_index, exc)
        if client.game_over:
            runtime.terminal_info = {
                "done_reason": "game_over",
                "score": client.score,
                "survival_time": client.survival_time,
                "max_score_seen": runtime.env.max_score_seen,
                "clock_mode": "lcrt",
                "virtual_time_s": round(virtual_t, 6),
                "terminal_timing": "between_actions",
            }
            _add_player_output_fields(
                runtime.terminal_info, runtime.player.player_index,
            )
            runtime.status = "ok"
            _update_player_state(
                runtime.state,
                status=runtime.status,
                terminal_info=runtime.terminal_info,
                score=runtime.score,
                step=runtime.step,
            )
            stop.request("game_over", runtime.player.player_index)


def _pause_all(runtimes) -> None:
    for runtime in runtimes:
        runtime.env.pause()


def _resume_all(runtimes) -> None:
    for runtime in runtimes:
        runtime.env.resume()


def _attach_api_debug(agent, player_dir: str, log: logging.Logger, pid: int) -> None:
    backend = getattr(agent, "backend", None)
    if backend is None:
        return
    try:
        from omni_game_arena.utils.api_debug import ApiDebugLogger

        backend.debug_logger = ApiDebugLogger(os.path.join(player_dir, "api_debug"))
        log.info("player %s api_debug enabled -> %s/api_debug/", pid, player_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s api_debug setup failed: %s", pid, exc)


def _run_player_loop(
    *,
    exp: TwoPlayerExperiment,
    game: GameSpec,
    player: PlayerSpec,
    state: _PlayerRuntimeState,
    stop: _StopController,
    events: list[dict],
    events_lock: threading.Lock,
    player_results: dict[int, dict],
    player_results_lock: threading.Lock,
    match_start: float,
    viewer,
    log: logging.Logger,
    log_vlm: bool,
    live_vlm_only: bool,
    live_fps: int,
    record_video: bool,
    video_fps: int,
    video_with_thinking: bool,
    video_thinking_layout: str,
) -> None:
    pid = player.player_index
    recorder = state.recorder
    env = None
    agent = None
    adapter = None
    video_recorder = None
    terminal_info: dict | None = None
    status = "pending"
    error = None
    score = None
    step = 0
    t_start = time.time()
    _update_player_state(state, t_start=t_start)

    try:
        agent, adapter = build_agent_and_adapter(
            player.agent, exp.params, game, player_index=pid,
        )
        env = make_solo_env(
            _env_for_player(exp.env, player),
            exp.params,
            adapter,
            game,
        )
        _update_player_state(state, env=env)
        obs = env.reset()
        reset_agent = getattr(agent, "reset", None)
        if callable(reset_agent):
            reset_agent()
        elif hasattr(agent, "reset_history"):
            agent.reset_history()

        if record_video:
            video_recorder = _start_player_video(
                state=state,
                env=env,
                fps=video_fps,
                with_thinking=video_with_thinking,
                thinking_layout=video_thinking_layout,
                log=log,
                pid=pid,
            )

        _append_event(
            events, events_lock, match_start,
            {"event": "reset", "player_index": pid, "step": 0},
        )
        if viewer is not None:
            if live_vlm_only:
                viewer.update_player(
                    pid, obs.get("image"), status=f"{_viewer_player_label(pid)} reset",
                )
            else:
                viewer.update_player(pid, status=f"{_viewer_player_label(pid)} reset")
                if env.client is not None:
                    viewer.start_streaming_player(pid, env.client, fps=live_fps)

        schema = adapter.action_schema
        task = player.task or exp.env.task
        log.info(
            "player %s start | model=%s port=%s max_steps=%s",
            pid, player.agent.model, player.port, exp.env.max_steps,
        )

        while not stop.event.is_set():
            decision_step = step
            decision_obs = obs
            if video_recorder is not None and video_recorder.with_text_panel:
                video_recorder.push_thinking(decision_step)
            t_act = time.time()
            action = agent.act(decision_obs, task, schema)
            act_latency = time.time() - t_act
            vlm_response = getattr(agent, "last_vlm_response", None) or None

            if stop.event.is_set():
                _append_event(
                    events, events_lock, match_start,
                    {
                        "event": "stopped_after_act",
                        "player_index": pid,
                        "step": step,
                        "latency_s": round(act_latency, 4),
                    },
                )
                break

            reason_text, action_text = _split_vlm_response(vlm_response or "")
            if video_recorder is not None and video_recorder.with_text_panel:
                video_recorder.push_step(decision_step, reason_text, action_text)
            obs, _reward, done, info = env.step(action)

            info = dict(info or {})
            _add_player_output_fields(info, pid)
            info["act_latency_s"] = round(act_latency, 4)
            if vlm_response:
                info["vlm_response"] = vlm_response

            fp_stats = _framepack_last_stats(agent)
            if fp_stats is not None:
                info["frame_pack"] = {
                    "kernel": fp_stats.kernel,
                    "n_input": fp_stats.n_input_frames,
                    "n_output": fp_stats.n_output_frames,
                    "token_ratio": fp_stats.approx_token_ratio,
                    "resolutions": fp_stats.resolutions,
                }

            recorder.record(decision_step, decision_obs, action, done, info)
            if info.get("done_reason") == "game_over":
                recorder.record_terminal_observation(obs)
            action_executed = info.get("action_executed", True) is not False
            if action_executed:
                step += 1
            if info.get("score") is not None:
                score = info.get("score")
            _update_player_state(
                state,
                step=step,
                score=score,
                terminal_info=info if done else terminal_info,
            )
            _append_event(
                events, events_lock, match_start,
                {
                    "event": "step",
                    "player_index": pid,
                    "step": decision_step,
                    "latency_s": round(act_latency, 4),
                    "action": compact_action(action),
                    "score": info.get("score"),
                    "done": done,
                    "done_reason": info.get("done_reason"),
                },
            )

            if viewer is not None:
                score_text = (
                    f" score={info.get('score')}"
                    if info.get("score") is not None
                    else ""
                )
                viewer.update_player(
                    pid,
                    decision_obs.get("image") if live_vlm_only else None,
                    step=step,
                    reason=reason_text,
                    action=action_text,
                    status=(
                        f"{_viewer_player_label(pid)} step={step}{score_text} "
                        f"lat={act_latency:.2f}s"
                    ),
                )

            log.info(
                "player=%s step=%d latency=%.2fs done=%s reason=%s",
                pid, decision_step, act_latency, done, info.get("done_reason"),
            )
            if log_vlm and vlm_response:
                # --log only controls console verbosity; it must not change
                # what files are saved.
                print(
                    f"\n--- player {pid} VLM step={decision_step} ---\n"
                    f"{vlm_response}\n",
                    flush=True,
                )

            if done:
                terminal_info = info
                status = "ok"
                _update_player_state(
                    state,
                    status=status,
                    terminal_info=terminal_info,
                    score=score,
                    step=step,
                )
                stop.request(info.get("done_reason") or "player_done", pid)
                break

        if status == "pending":
            status = "stopped" if stop.event.is_set() else "ok"
            _update_player_state(state, status=status, step=step)

    except EmptyModelResponseError as exc:
        status = "skipped"
        error = f"{type(exc).__name__}: {exc}"
        terminal_info = {
            "done_reason": "model_empty_response",
            "skip_reason": "model_empty_response",
            "error": error,
            "step": step,
        }
        _add_player_output_fields(terminal_info, pid)
        _update_player_state(
            state,
            status=status,
            error=error,
            terminal_info=terminal_info,
            step=step,
        )
        stop.request("model_empty_response", pid)
        _append_event(
            events, events_lock, match_start,
            {
                "event": "skipped",
                "player_index": pid,
                "step": step,
                "reason": "model_empty_response",
            },
        )
        log.warning("player %s skipping match: %s", pid, exc)

    except Exception as exc:  # noqa: BLE001
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        terminal_info = {
            "done_reason": "error",
            "error": error,
            "step": step,
        }
        _add_player_output_fields(terminal_info, pid)
        _update_player_state(
            state,
            status=status,
            error=error,
            terminal_info=terminal_info,
            step=step,
        )
        stop.request("player_error", pid)
        log.error("player %s failed: %s\n%s", pid, exc, traceback.format_exc())

    finally:
        if viewer is not None:
            try:
                viewer.stop_streaming_player(pid)
            except Exception as exc:  # noqa: BLE001
                log.warning("player %s viewer stream stop failed: %s", pid, exc)

        _stop_player_video(video_recorder, state, log, pid)

        if env is not None and getattr(env, "client", None) is not None:
            try:
                if env.client.score is None:
                    env.client.get_score()
                score = env.client.score
                _update_player_state(state, score=score)
            except Exception as exc:  # noqa: BLE001
                log.warning("player %s score query failed: %s", pid, exc)

        if env is not None:
            try:
                env.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("player %s env.close() raised: %s", pid, exc)

        _update_player_state(
            state,
            status=status,
            error=error,
            terminal_info=terminal_info,
            score=score,
            step=step,
        )
        _publish_player_result(
            state,
            player_results,
            player_results_lock,
            game,
            log,
            final=True,
        )


def _update_player_state(state: _PlayerRuntimeState, **updates: Any) -> None:
    with state.lock:
        for key, value in updates.items():
            setattr(state, key, value)


def _stopping_terminal_info(
    state: _PlayerRuntimeState,
    stop: _StopController,
) -> dict:
    with state.lock:
        step = state.step
        score = state.score
        pid = state.player.player_index
    info = {
        "done_reason": "stopping_timeout",
        "match_stop_reason": stop.reason or "completed",
        "match_stop_player_index": _optional_player_display_id(stop.player_index),
        "match_stop_player_id": _optional_player_display_id(stop.player_index),
        "match_stop_player_label": _optional_player_result_key(stop.player_index),
        "match_stop_ue_player_index": stop.player_index,
        "step": step,
        "partial": True,
    }
    _add_player_output_fields(info, pid)
    if score is not None:
        info["score"] = score
    return info


def _refresh_player_scores(
    player_states: dict[int, _PlayerRuntimeState],
    player_indices: list[int],
    log: logging.Logger,
) -> None:
    """Best-effort score query for players whose loop did not reach finally."""
    for pid in player_indices:
        state = player_states.get(pid)
        if state is None:
            continue

        with state.lock:
            env = state.env
            player = state.player

        score = _query_score_from_env(env, pid, log)
        if score is None:
            score = _query_score_from_port(player, log)
        if score is None:
            continue

        _update_player_state(state, score=score)
        log.info("player %s post-stop score query: %.4f", pid, score)


def _query_score_from_env(env: Any | None, pid: int, log: logging.Logger) -> float | None:
    client = getattr(env, "client", None) if env is not None else None
    if client is None or not getattr(client, "connected", False):
        return None

    try:
        client.get_score()
        if client.score is None:
            return None
        return float(client.score)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s env score query failed: %s", pid, exc)
        return None


def _query_score_from_port(player: PlayerSpec, log: logging.Logger) -> float | None:
    pid = player.player_index
    client = UE5Client(
        host=player.host,
        port=player.port,
        request_timeout=1.0,
        welcome_timeout=0.2,
    )
    if not client.connect():
        client.disconnect()
        log.warning(
            "player %s post-stop score query could not connect to %s:%s",
            pid, player.host, player.port,
        )
        return None

    try:
        client.get_score()
        if client.score is None:
            return None
        return float(client.score)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s port score query failed: %s", pid, exc)
        return None
    finally:
        client.disconnect()


def _publish_player_result(
    state: _PlayerRuntimeState,
    player_results: dict[int, dict],
    player_results_lock: threading.Lock,
    game: GameSpec,
    log: logging.Logger,
    *,
    status_override: str | None = None,
    terminal_info_override: dict | None = None,
    final: bool = False,
) -> dict:
    with state.lock:
        if state.finalized and not final:
            pid = state.player.player_index
            with player_results_lock:
                existing = player_results.get(pid)
            if existing is not None:
                return existing

        player = state.player
        pid = player.player_index
        status = status_override or state.status
        error = state.error
        terminal_info = (
            dict(terminal_info_override)
            if terminal_info_override is not None
            else (dict(state.terminal_info) if state.terminal_info is not None else None)
        )
        score = state.score
        video = state.video
        step = state.step
        wall = time.time() - state.t_start
        player_dir = state.player_dir

    records = state.recorder.snapshot_records()
    try:
        state.recorder.save_summary(records)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s recorder.save_summary() raised: %s", pid, exc)

    if not final and error is None:
        error = "player thread still running; wrote partial result before shutdown"

    if score is not None:
        if terminal_info is None:
            terminal_info = {
                "done_reason": status,
                "score": score,
            }
        else:
            if terminal_info.get("score") is None:
                terminal_info["score"] = score

    terminal_info = _terminal_info_output_dict(terminal_info, pid)
    metrics = compute_episode_metrics(records, terminal_info, wall, game)
    player_result = {
        "player_index": _player_display_id(pid),
        "player_id": _player_display_id(pid),
        "player_label": _player_result_key(pid),
        "ue_player_index": pid,
        "status": status,
        "error": error,
        "run_dir": player_dir,
        "model": player.agent.model,
        "host": player.host,
        "port": player.port,
        "steps": step,
        "score": score,
        "terminal_info": terminal_info,
        "metrics": metrics,
        "partial": not final,
    }
    if video is not None:
        player_result["video"] = video
    result_file = _player_result_file_dict(
        player=player,
        player_result=player_result,
        game=game,
    )
    _write_player_result_artifacts(
        player_dir=player_dir,
        result=result_file,
        records=records,
        log=log,
        pid=pid,
    )
    with player_results_lock:
        existing = player_results.get(pid)
        if not final and existing and not existing.get("partial", False):
            return existing
        player_results[pid] = player_result

    if final:
        _update_player_state(state, finalized=True)

    return player_result


def _player_result_file_dict(
    *,
    player: PlayerSpec,
    player_result: dict,
    game: GameSpec,
) -> dict:
    result = dict(player_result)
    result["game"] = game.name
    result["mode"] = game.mode
    result["agent"] = asdict(player.agent)
    result["player"] = _player_spec_output_dict(player)
    return result


def _write_player_result_artifacts(
    *,
    player_dir: str,
    result: dict,
    records: list[dict],
    log: logging.Logger,
    pid: int,
) -> None:
    try:
        with open(os.path.join(player_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s result.json write raised: %s", pid, exc)
        return

    if not records:
        return

    try:
        trace_dir = write_reflection_trace_from_summary(
            player_dir,
            result,
            records,
        )
        log.info("player %s reflection_trace -> %s", pid, trace_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("player %s reflection_trace generation raised: %s", pid, exc)


def _rewrite_player_result_artifacts(
    result: dict,
    game: GameSpec,
    log: logging.Logger,
) -> None:
    records_by_player = _load_player_records_for_rewrite(result, log)
    mode = result.get("mode")
    if mode == "coop":
        _normalize_coop_record_scores(records_by_player, game)
    elif mode == "pvp":
        _normalize_pvp_record_scores(records_by_player)

    for player_result in (result.get("player_results") or {}).values():
        player_dir = player_result.get("run_dir")
        if not player_dir:
            continue
        try:
            player_label = player_result.get("player_label")
            records = records_by_player.get(player_label)
            if records is None:
                continue
            summary_path = os.path.join(player_dir, "summary.json")
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            player_file_result = _load_player_result_file(player_dir, player_result)
            player_file_result.update({
                "outcome": player_result.get("outcome"),
                "winner": result.get("winner"),
                "winner_player_index": result.get("winner_player_index"),
                "winner_player_id": result.get("winner_player_id"),
                "winner_ue_player_index": result.get("winner_ue_player_index"),
            })
            for key in ("score", "terminal_info", "metrics"):
                if key in player_result:
                    player_file_result[key] = player_result.get(key)
            for key in ("raw_player_score", "scores"):
                if key in player_result:
                    player_file_result[key] = player_result.get(key)
                else:
                    player_file_result.pop(key, None)
            for key in (
                "own_score",
                "opponent_score",
                "teammate_score",
                "teammate_label",
                "teammate_scores",
                "team_score",
            ):
                player_file_result.pop(key, None)
            with open(
                os.path.join(player_dir, "result.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(player_file_result, f, indent=2, ensure_ascii=False)
            write_reflection_trace_from_summary(
                player_dir,
                player_file_result,
                records,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "player %s outcome artifact rewrite raised: %s",
                player_result.get("player_label"),
                exc,
            )


def _load_player_result_file(player_dir: str, fallback: dict) -> dict:
    result_path = os.path.join(player_dir, "result.json")
    try:
        with open(result_path, encoding="utf-8") as f:
            result = json.load(f)
            if isinstance(result, dict):
                return result
    except FileNotFoundError:
        pass
    return dict(fallback)


def _load_player_records_for_rewrite(
    result: dict,
    log: logging.Logger,
) -> dict[str, list[dict]]:
    records_by_player: dict[str, list[dict]] = {}
    for player_result in (result.get("player_results") or {}).values():
        player_label = player_result.get("player_label")
        player_dir = player_result.get("run_dir")
        if not player_label or not player_dir:
            continue
        try:
            summary_path = os.path.join(player_dir, "summary.json")
            with open(summary_path, encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list):
                records_by_player[player_label] = records
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "player %s summary load for artifact rewrite raised: %s",
                player_label,
                exc,
            )
    return records_by_player


def _normalize_coop_record_scores(
    records_by_player: dict[str, list[dict]],
    game: GameSpec,
) -> None:
    if not records_by_player:
        return

    aggregation = getattr(game, "coop_score_aggregation", "shared")
    latest_raw_scores = {player_label: 0.0 for player_label in records_by_player}
    events: list[tuple[float, str, int, float]] = []
    for player_label, records in records_by_player.items():
        for idx, record in enumerate(records):
            info = record.get("info")
            if not isinstance(info, dict):
                continue
            raw_score = _as_float(info.get("raw_player_score"))
            if raw_score is None:
                raw_score = _as_float(info.get("score"))
            if raw_score is None:
                continue
            events.append((_record_time(record), player_label, idx, raw_score))

    for group in _score_event_groups(events):
        for _t, player_label, _idx, raw_score in group:
            latest_raw_scores[player_label] = raw_score
        if aggregation == "sum":
            team_score = round(sum(latest_raw_scores.values()), 4)
        else:
            team_score = round(max(latest_raw_scores.values()), 4)

        for _t, player_label, idx, raw_score in group:
            teammate_score = _sum_other_scores(latest_raw_scores, player_label)
            info = records_by_player[player_label][idx].setdefault("info", {})
            if isinstance(info, dict):
                info["raw_player_score"] = raw_score
                info["own_score"] = raw_score
                if teammate_score is not None:
                    info["teammate_score"] = teammate_score
                info["team_score"] = team_score
                info["score"] = team_score
                max_score_seen = _as_float(info.get("max_score_seen"))
                if max_score_seen is None or team_score > max_score_seen:
                    info["max_score_seen"] = team_score


def _normalize_pvp_record_scores(
    records_by_player: dict[str, list[dict]],
) -> None:
    if not records_by_player:
        return

    latest_scores = {player_label: 0.0 for player_label in records_by_player}
    events: list[tuple[float, str, int, float]] = []
    for player_label, records in records_by_player.items():
        for idx, record in enumerate(records):
            info = record.get("info")
            if not isinstance(info, dict):
                continue
            score = _as_float(info.get("score"))
            if score is None:
                continue
            events.append((_record_time(record), player_label, idx, score))

    for group in _score_event_groups(events):
        for _t, player_label, _idx, score in group:
            latest_scores[player_label] = score

        for _t, player_label, idx, score in group:
            opponent_score = _sum_other_scores(latest_scores, player_label)
            info = records_by_player[player_label][idx].setdefault("info", {})
            if isinstance(info, dict):
                info["own_score"] = score
                if opponent_score is not None:
                    info["opponent_score"] = opponent_score


def _score_event_groups(
    events: list[tuple[float, str, int, float]],
) -> list[list[tuple[float, str, int, float]]]:
    groups: list[list[tuple[float, str, int, float]]] = []
    for event in sorted(events):
        if not groups or event[0] != groups[-1][0][0]:
            groups.append([event])
        else:
            groups[-1].append(event)
    return groups


def _record_time(record: dict) -> float:
    info = record.get("info")
    if isinstance(info, dict):
        virtual_time = _as_float(info.get("virtual_time_s"))
        if virtual_time is not None:
            return virtual_time
    timestamp = _as_float(record.get("observation_timestamp"))
    return timestamp if timestamp is not None else 0.0


def _env_for_player(env: EnvSpec, player: PlayerSpec) -> EnvSpec:
    return EnvSpec(
        host=player.host,
        port=player.port,
        task=player.task or env.task,
        max_steps=env.max_steps,
        screenshot_quality=env.screenshot_quality,
        # Map switching is handled once at match start by player 0's port.
        # Per-player SoloEnv instances should not each issue an open-map
        # command into the same PvP/Coop match.
        map="",
        obs_delay=env.obs_delay,
    )


def _open_match_map(exp: TwoPlayerExperiment, log: logging.Logger) -> None:
    """Open the match map once through player 0's RemoteInput port."""
    if not exp.env.map:
        log.info("No match map configured; using currently loaded UE5 scene")
        return

    opener = min(exp.players, key=lambda p: p.player_index)
    log.info(
        "Opening match map %s via player %s at %s:%s",
        exp.env.map, opener.player_index, opener.host, opener.port,
    )
    client = UE5Client(
        host=opener.host,
        port=opener.port,
        screenshot_quality=exp.env.screenshot_quality,
    )
    if not client.connect():
        raise RuntimeError(
            f"Failed to connect to UE5 at {opener.host}:{opener.port} "
            f"to open map {exp.env.map}"
        )
    try:
        client.open_map(exp.env.map)
        time.sleep(3.0)
    finally:
        client.disconnect()


def _wait_for_player_threads(
    threads: list[threading.Thread],
    stop: _StopController,
    log: logging.Logger,
    stop_grace_s: float = 1.0,
) -> None:
    """Wait while match is live, but never block forever after stop."""
    stop_seen_at: float | None = None
    while any(t.is_alive() for t in threads):
        if stop.event.is_set():
            if stop_seen_at is None:
                stop_seen_at = time.time()
            elif time.time() - stop_seen_at >= stop_grace_s:
                alive = ", ".join(t.name for t in threads if t.is_alive())
                log.warning(
                    "Stop signal grace period elapsed; continuing while "
                    "thread(s) finish in background: %s",
                    alive,
                )
                return
        time.sleep(0.1)


def _append_event(
    events: list[dict],
    lock: threading.Lock,
    match_start: float,
    event: dict,
) -> None:
    event = dict(event)
    event["t_s"] = round(time.time() - match_start, 4)
    with lock:
        events.append(event)


def _apply_score_summary(result: dict, game: GameSpec) -> None:
    result.pop("team_score", None)
    player_results = result.get("player_results") or {}
    scores: dict[int, float] = {}
    for _raw_pid, player_result in player_results.items():
        score = player_result.get("score")
        if game.mode == "coop" and player_result.get("raw_player_score") is not None:
            score = player_result.get("raw_player_score")
        if score is None:
            continue
        try:
            pid = int(player_result["ue_player_index"])
            scores[pid] = float(score)
        except (KeyError, TypeError, ValueError):
            continue

    if game.mode == "pvp" and len(scores) == 2:
        pids = sorted(scores)
        if scores[pids[0]] > scores[pids[1]]:
            winner = pids[0]
        elif scores[pids[1]] > scores[pids[0]]:
            winner = pids[1]
        else:
            result["winner"] = "draw"
            return
        result["winner"] = _player_result_key(winner)
        result["winner_player_index"] = _player_display_id(winner)
        result["winner_player_id"] = _player_display_id(winner)
        result["winner_ue_player_index"] = winner
    elif game.mode == "coop" and scores:
        aggregation = getattr(game, "coop_score_aggregation", "shared")
        if aggregation == "sum":
            team_score = round(sum(scores.values()), 4)
        else:
            # Some coop scenes expose the already-shared team score through
            # every player port, so duplicate reads must not be summed.
            team_score = round(max(scores.values()), 4)
        result["coop_total_score"] = team_score


def _apply_player_score_context(result: dict, game: GameSpec) -> None:
    player_results = result.get("player_results") or {}
    if not isinstance(player_results, dict):
        return

    team_score = _as_float(result.get("coop_total_score"))
    raw_scores_by_key: dict[str, float] = {}
    if game.mode in {"coop", "pvp"}:
        for key, player_result in player_results.items():
            if not isinstance(player_result, dict):
                continue
            raw_score = _as_float(player_result.get("raw_player_score"))
            if raw_score is None:
                raw_score = _as_float(player_result.get("score"))
            if raw_score is not None:
                raw_scores_by_key[str(key)] = raw_score

    for key, player_result in player_results.items():
        if not isinstance(player_result, dict):
            continue

        raw_player_score = _as_float(player_result.get("raw_player_score"))
        for stale_key in (
            "own_score",
            "opponent_score",
            "teammate_score",
            "teammate_label",
            "teammate_scores",
            "team_score",
            "raw_player_score",
        ):
            player_result.pop(stale_key, None)

        own_score = raw_player_score
        if own_score is None:
            own_score = _as_float(player_result.get("score"))

        if game.mode == "coop":
            scores: dict[str, Any] = {}
            if team_score is not None:
                preserve_raw = (
                    getattr(game, "coop_score_aggregation", "shared") == "sum"
                    or (own_score is not None and own_score != team_score)
                )
                teammate_score = _sum_other_scores(raw_scores_by_key, str(key))
                _apply_coop_team_score(
                    player_result,
                    team_score,
                    own_score,
                    teammate_score,
                    preserve_raw=preserve_raw,
                )
                if own_score is not None:
                    scores["own"] = own_score
                if teammate_score is not None:
                    scores["teammate"] = teammate_score
                scores["team"] = team_score
            if scores:
                player_result["scores"] = scores
            continue

        if game.mode == "pvp":
            opponent_score = _sum_other_scores(raw_scores_by_key, str(key))
            if own_score is not None:
                player_result["own_score"] = own_score
            if opponent_score is not None:
                player_result["opponent_score"] = opponent_score
            _apply_score_fields(
                player_result,
                own_score=own_score,
                opponent_score=opponent_score,
            )
            scores: dict[str, Any] = {}
            if own_score is not None:
                scores["own"] = own_score
            if opponent_score is not None:
                scores["opponent"] = opponent_score
            if scores:
                player_result["scores"] = scores
            continue

        scores: dict[str, Any] = {}
        if own_score is not None:
            scores["own"] = own_score

        if scores:
            player_result["scores"] = scores


def _apply_coop_team_score(
    player_result: dict,
    team_score: float,
    raw_score: float | None,
    teammate_score: float | None,
    *,
    preserve_raw: bool,
) -> None:
    if preserve_raw and raw_score is not None:
        player_result["raw_player_score"] = raw_score
    else:
        player_result.pop("raw_player_score", None)

    player_result["score"] = team_score
    if raw_score is not None:
        player_result["own_score"] = raw_score
    if teammate_score is not None:
        player_result["teammate_score"] = teammate_score
    player_result["team_score"] = team_score

    terminal_info = player_result.get("terminal_info")
    if isinstance(terminal_info, dict):
        if preserve_raw and raw_score is not None:
            terminal_info["raw_player_score"] = raw_score
        else:
            terminal_info.pop("raw_player_score", None)
        if raw_score is not None:
            terminal_info["own_score"] = raw_score
        if teammate_score is not None:
            terminal_info["teammate_score"] = teammate_score
        terminal_info["team_score"] = team_score
        terminal_info["score"] = team_score
        max_score_seen = _as_float(terminal_info.get("max_score_seen"))
        if max_score_seen is None or team_score > max_score_seen:
            terminal_info["max_score_seen"] = team_score

    metrics = player_result.get("metrics")
    if isinstance(metrics, dict):
        game_metrics = metrics.get("game")
        if not isinstance(game_metrics, dict):
            game_metrics = {}
            metrics["game"] = game_metrics
        if preserve_raw and raw_score is not None:
            game_metrics["raw_player_score"] = raw_score
        else:
            game_metrics.pop("raw_player_score", None)
        if raw_score is not None:
            game_metrics["own_score"] = raw_score
        if teammate_score is not None:
            game_metrics["teammate_score"] = teammate_score
        game_metrics["team_score"] = team_score
        game_metrics["score"] = team_score


def _apply_score_fields(
    player_result: dict,
    *,
    own_score: float | None = None,
    opponent_score: float | None = None,
) -> None:
    terminal_info = player_result.get("terminal_info")
    if isinstance(terminal_info, dict):
        if own_score is not None:
            terminal_info["own_score"] = own_score
        if opponent_score is not None:
            terminal_info["opponent_score"] = opponent_score

    metrics = player_result.get("metrics")
    if isinstance(metrics, dict):
        game_metrics = metrics.get("game")
        if not isinstance(game_metrics, dict):
            game_metrics = {}
            metrics["game"] = game_metrics
        if own_score is not None:
            game_metrics["own_score"] = own_score
        if opponent_score is not None:
            game_metrics["opponent_score"] = opponent_score


def _sum_other_scores(scores_by_key: dict[str, float], own_key: str) -> float | None:
    teammate_scores = [
        score for key, score in scores_by_key.items()
        if key != own_key
    ]
    if not teammate_scores:
        return None
    return round(sum(teammate_scores), 4)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_player_outcomes(result: dict, game: GameSpec) -> None:
    if game.mode != "pvp":
        return
    player_results = result.get("player_results") or {}
    winner = result.get("winner")
    if not winner:
        return
    for key, player_result in player_results.items():
        if winner == "draw":
            outcome = "draw"
        elif key == winner or player_result.get("player_label") == winner:
            outcome = "win"
        else:
            outcome = "loss"
        player_result["outcome"] = outcome
        player_result["winner"] = winner


def _match_status(player_results: dict) -> str:
    statuses = [p.get("status") for p in player_results.values()]
    if any(s == "error" for s in statuses):
        return "error"
    if any(s == "skipped" for s in statuses):
        return "skipped"
    return "ok"


def _missing_player_result(pid: int) -> dict:
    return {
        "player_index": _player_display_id(pid),
        "player_id": _player_display_id(pid),
        "player_label": _player_result_key(pid),
        "ue_player_index": pid,
        "status": "missing",
        "error": "player thread did not publish a result",
        "score": None,
        "steps": 0,
    }


def _players_slug(players: list[PlayerSpec]) -> str:
    return "_vs_".join(
        _player_slug_part(p) for p in players
    )


def _finalize_summary(results: list[dict], game: GameSpec) -> dict:
    logger = _logger_for(game)
    summary = {
        "game": game.name,
        "mode": game.mode,
        "n_matches": len(results),
        "n_ok": sum(1 for r in results if r.get("status") == "ok"),
        "n_error": sum(1 for r in results if r.get("status") == "error"),
        "n_skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "n_interrupted": sum(1 for r in results if r.get("status") == "interrupted"),
        "results": results,
    }
    logger.info(
        "Two-player benchmark finished: %d total, %d ok, %d skipped, %d error, %d interrupted",
        summary["n_matches"],
        summary["n_ok"],
        summary["n_skipped"],
        summary["n_error"],
        summary["n_interrupted"],
    )
    return summary


def _print_match_result(result: dict) -> None:
    parts = []
    for pid, player in sorted(result["player_results"].items()):
        parts.append(
            f"{pid}: score={player.get('score')} steps={player.get('steps')}"
        )
    winner = result.get("winner")
    if winner is not None:
        parts.append(f"winner={winner}")
    if result.get("coop_total_score") is not None:
        parts.append(f"coop_total_score={result['coop_total_score']}")
    print("[match end] " + " | ".join(parts), flush=True)
