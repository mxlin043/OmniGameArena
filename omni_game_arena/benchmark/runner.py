"""Experiment runner.

Responsibilities
----------------
1. For each ``Experiment`` (agent x params x episode):
   - spin up SoloEnv (isolated per episode so state is clean)
   - build a fresh agent/adapter via the factory
   - run the main gym-like loop
   - save step trajectory via ``omni_game_arena.eval.recorder.StepRecorder``
   - compute episode metrics (generic + game-specific via ``GameSpec``)

2. Aggregate per-cell (agent x params) results across episodes.
3. Write rolling + final results files for downstream analysis.

Isolation guarantees
--------------------
- Each experiment builds its own Env + Agent + Adapter; no mutable state
  leaks across cells.
- Exceptions inside one experiment do NOT abort the whole benchmark;
  they are logged, recorded as ``status="error"`` and the runner
  continues with the next cell.
- Keyboard interrupts (Ctrl+C) are honored: the current experiment is
  wrapped up cleanly and a partial summary is still written.
"""

from __future__ import annotations

from omni_game_arena.utils.public_config import public_config

import json
import logging
import os
import re
import time
import traceback
from contextlib import nullcontext
from typing import Any
from omni_game_arena.clock import normalize_clock_mode, decision_latency

# Matches Lumine-style action tag. Used to split a VLM response into a
# reasoning prefix and the chunked action payload for the live viewer.
# Tolerate model-side typos in the action tags: Claude sometimes emits
# `<|action_start|}` for `<|action_start|>`, and Qwen3.5-397B doubles the
# leading pipe, `<||action_end|>`, on roughly 15% of turns. An unmatched
# tag is not merely cosmetic -- the payload then gets split on ';' with the
# literal tag still attached, so `<|action_start|>S` and `<||action_end|>`
# are handed downstream as if they were key presses, costing two of the six
# action slots. Must stay in sync with the matching regexes in
# `omni_game_arena.prompts.methods.lumine` and
# `omni_game_arena.eval.reflection_trace` - all three must fail/succeed the
# same way to keep viewer, parser, and trace consistent.
_ACTION_TAG_RE = re.compile(
    r'<\|+action_start\|*[>}](.*?)<\|+action_end\|*[>}]', re.DOTALL,
)


def _split_vlm_response(text: str) -> tuple[str, str]:
    """Return ``(reason, action)`` extracted from a raw VLM response.

    ``reason`` is everything before the action tag (trimmed). ``action``
    is the content between ``<|action_start|>`` and ``<|action_end|>``,
    whitespace-collapsed for compact display. If no action tag is
    present the whole response is treated as the reason.
    """
    if not text:
        return "", ""
    m = _ACTION_TAG_RE.search(text)
    if m:
        reason = text[:m.start()].strip()
        action = " ".join(m.group(1).split())
    else:
        reason = text.strip()
        action = ""
    return reason, action

from omni_game_arena.env.client_ue5 import UE5Client
from omni_game_arena.env.solo import SoloEnv
from omni_game_arena.eval.recorder import StepRecorder
from omni_game_arena.eval.reflection_trace import write_reflection_trace_from_summary
from omni_game_arena.eval.video_recorder import VideoRecorder
from omni_game_arena.models import EmptyModelResponseError

from omni_game_arena.utils.viewer import LiveViewer
from omni_game_arena.utils.variant_progress import VariantProgress

from .config import ParamsPoint, EnvSpec, Experiment, params_record
from .factory import build_agent_and_adapter
from .frame_pack_wrapper import last_stats as _framepack_last_stats
from .games.base import GameSpec
from .logging_utils import (
    ExperimentLogContext,
    benchmark_logger_name,
    reserve_timestamped_run_dir,
)
from .metrics import aggregate_cell_metrics, compute_episode_metrics


def _print_final_score(env, exp) -> None:
    """Print the final score to stdout so it shows in the cmd window.

    Runs in ``run_one_experiment``'s ``finally`` block, covering both
    normal end-of-episode and early KeyboardInterrupt. Queries UE5 via
    ``get_score`` when no score is cached yet (the interrupt case).
    Never raises: score display must not mask a real exception.
    """
    try:
        client = getattr(env, "client", None) if env is not None else None
        if client is None:
            print(f"[episode end] {exp.run_id} score=<no client>", flush=True)
            return
        if client.score is None:
            try:
                client.get_score()
            except Exception:  # noqa: BLE001
                pass
        score = client.score
        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else str(score)
        print(f"[episode end] {exp.run_id} score={score_str}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def _logger_for(game: GameSpec) -> logging.Logger:
    return logging.getLogger(f"{benchmark_logger_name(game.name)}.runner")


class BenchmarkSoloEnv(SoloEnv):
    """SoloEnv with safer reset/reconnect behavior for long benchmarks.

    Per-episode "reset" here = switch to ``self.map`` via the UE5 console
    (``open /Game/OmniGameArena/Map/<name>``). When ``self.map`` is empty, no switch
    happens - useful for sandbox debugging where the user already loaded
    the scene by hand.
    """

    def __init__(self, *, map: str = "", map_wait: float = 3.0, **kwargs):
        super().__init__(**kwargs)
        self.map = map
        self.map_wait = max(0.0, float(map_wait))

    def reset(self):
        logger = logging.getLogger(__name__)
        if self.client:
            self.client.disconnect()

        self.client = UE5Client(
            host=self.host,
            port=self.port,
            screenshot_quality=self.screenshot_quality,
        )
        self.client.strict_response_matching = getattr(self, 'strict_response_matching', False)
        if not self.client.connect():
            raise RuntimeError(
                f"Failed to connect to UE5 at {self.host}:{self.port}"
            )

        if self.map:
            self.client.open_map(self.map)
            if self.map_wait > 0:
                time.sleep(self.map_wait)
            if not self.client.connected:
                logger.warning(
                    "UE5 connection closed during map switch; reconnecting before initial observation"
                )
                if not self.client.reconnect():
                    raise RuntimeError(
                        f"Failed to reconnect to UE5 at {self.host}:{self.port} after map switch"
                    )
                time.sleep(0.5)
            logger.info("Map switched to %s", self.map)

        self.resume()
        self.step_count = 0
        self.start_time = time.time()

        logger.info("Environment reset. Task: %s", self.task)
        try:
            return self._make_observation()
        except Exception as e:
            if not (self.map and self.client is not None):
                raise
            # The viewport can still be blank well after map_wait, and the
            # old single 0.5 s retry was shorter than the wait that had
            # already proved insufficient, so it reliably failed too and
            # took the whole run down. Back off instead.
            last = e
            for delay in (1.0, 2.0, 4.0, 8.0):
                logger.warning(
                    "Initial observation failed after map switch (%s); "
                    "reconnecting and retrying in %.1fs",
                    last, delay,
                )
                if not self.client.reconnect():
                    raise RuntimeError(
                        f"Failed to reconnect to UE5 at {self.host}:{self.port} for initial observation"
                    ) from last
                time.sleep(delay)
                try:
                    return self._make_observation()
                except Exception as exc:  # noqa: BLE001 - retry until budget runs out
                    last = exc
            raise last


# -- Single experiment --------------------------------------------------

def run_one_experiment(
    exp: Experiment,
    output_root: str,
    game: GameSpec,
    viewer: LiveViewer | None = None,
    log_vlm: bool = False,
    api_debug: bool = False,
    clock_mode: str = "realtime",
    record_video: bool = False,
    video_fps: int = 30,
    video_with_thinking: bool = False,
    video_thinking_layout: str = "side",
    flat_output: bool = False,
    checkpoint=None,
) -> dict:
    """Run a single experiment cell (one episode).

    Layout: ``output_root/<agent>/<YYYYMMDD_HHMMSS>/``.
    Agent kind/method stay in result metadata, not in the storage path.
    Each episode gets its own second-level timestamped directory; collisions
    wait for the next available second.
    """
    clock_mode = normalize_clock_mode(clock_mode)
    if flat_output:
        parent_dir = output_root
    else:
        parent_dir = os.path.join(output_root, exp.agent.model)
    run_dir, ep_timestamp = reserve_timestamped_run_dir(parent_dir)
    if checkpoint is not None:
        checkpoint.start_attempt(run_dir)
    # Print save path so the user sees where this episode's artifacts land,
    # without having to dig through the log file.
    print(f"[save] {run_dir}", flush=True)
    serialized_params = params_record(exp.params, [exp.agent])
    if exp.agent.kind.lower() == "random":
        # Avoid implying that VLM-only knobs (history, temperature, visual
        # input) affect the no-model baseline. These are the only shared
        # runtime parameters consumed by its adapter/environment.
        serialized_params = {
            key: serialized_params[key]
            for key in ("hold_duration", "obs_delay", "chunk_steps")
        }

    result: dict[str, Any] = {
        "run_id": exp.run_id,
        "run_dir": run_dir,
        "timestamp": ep_timestamp,
        "clock_mode": clock_mode,
        "game": exp.game,
        "agent": public_config(exp.agent),
        "parameters": serialized_params,
        "episode_idx": exp.episode_idx,
        "status": "pending",
        "metrics": None,
    }

    with ExperimentLogContext(
        run_dir,
        name="experiment",
        game_name=game.name,
        write_file=api_debug,
    ) as exp_log:
        exp_log.info(
            "Config: model=%s kind=%s | params=%s",
            exp.agent.model, exp.agent.kind,
            serialized_params,
        )

        env, agent, adapter, recorder, video_recorder = None, None, None, None, None
        terminal_info: dict | None = None
        t_start = time.time()

        try:
            agent, adapter = build_agent_and_adapter(exp.agent, exp.params, game)
            policy_metadata = getattr(agent, "policy_metadata", None)
            if isinstance(policy_metadata, dict):
                result["policy_parameters"] = policy_metadata
            _configure_lcm_timing(agent, clock_mode == "lcm", exp_log)
            if api_debug:
                backend = getattr(agent, "backend", None)
                if backend is None:
                    exp_log.info(
                        "api_debug requested but agent kind=%s has no model API",
                        exp.agent.kind,
                    )
                else:
                    # Attach per-call debug sink. Dumps every (request, response)
                    # pair under run_dir/api_debug/. See utils.api_debug.
                    from omni_game_arena.utils.api_debug import ApiDebugLogger
                    backend.debug_logger = ApiDebugLogger(
                        os.path.join(run_dir, "api_debug")
                    )
                    exp_log.info("api_debug enabled -> %s/api_debug/", run_dir)
            env = make_solo_env(exp.env, exp.params, adapter, game)
            if checkpoint is not None:
                env.strict_response_matching = True
            recorder = StepRecorder(output_dir=run_dir)
            if record_video and not (checkpoint is not None and checkpoint.resume
                                     and checkpoint.data['phase'] == 'terminal'):
                video_recorder = VideoRecorder(
                    os.path.join(run_dir, "episode.mp4"),
                    fps=video_fps,
                    with_text_panel=video_with_thinking,
                    text_layout=video_thinking_layout,
                    # Only openp2p / nitrogen get the bounded (lighter) text
                    # panel render; every other agent keeps the original path.
                    light_text_panel=_is_policy_agent(exp),
                )

            terminal_info = _run_episode(
                env, agent, adapter, exp, recorder, exp_log,
                viewer=viewer, log_vlm=log_vlm, clock_mode=clock_mode,
                video_recorder=video_recorder,
                checkpoint=checkpoint,
            )
            result["status"] = "ok"

        except KeyboardInterrupt:
            exp_log.warning("Interrupted by user")
            result["status"] = "interrupted"

        except EmptyModelResponseError as e:
            exp_log.warning("Skipping episode: %s", e)
            result["status"] = "skipped"
            result["skip_reason"] = "model_empty_response"
            result["error"] = f"{type(e).__name__}: {e}"
            terminal_info = {
                "done_reason": "model_empty_response",
                "skip_reason": "model_empty_response",
                "error": result["error"],
            }

        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            exp_log.error("Experiment failed: %s\n%s", e, tb)
            result["status"] = "error"
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            wall = time.time() - t_start

            # Print final score to the cmd window - on normal done
            # solo.step() already called get_score(), but on an early
            # KeyboardInterrupt we query here so the user still sees
            # progress. Must run before env.close() (which closes the
            # TCP client). Best-effort: never let this mask a real error.
            _print_final_score(env, exp)

            if video_recorder is not None:
                try:
                    video_recorder.stop_streaming()
                    result["video"] = {
                        "path": video_recorder.output_path,
                        "fps": video_recorder.fps,
                        "frames": video_recorder.frame_count,
                        "with_thinking": video_recorder.with_text_panel,
                        "thinking_layout": video_recorder.text_layout,
                        "error": video_recorder.error,
                    }
                    exp_log.info(
                        "Video recording -> %s (%d frames)",
                        video_recorder.output_path,
                        video_recorder.frame_count,
                    )
                except Exception as e:  # noqa: BLE001
                    exp_log.warning("video_recorder.stop_streaming() raised: %s", e)

            if env is not None:
                try:
                    if checkpoint is not None:
                        env.close(keep_paused=True)
                    else:
                        env.close()
                except Exception as e:  # noqa: BLE001
                    exp_log.warning("env.close() raised: %s", e)

            if recorder is not None:
                try:
                    recorder.save_summary()
                    exp_log.info("Step recording -> %s", recorder.output_dir)
                except Exception as e:  # noqa: BLE001
                    exp_log.warning("recorder.save_summary() raised: %s", e)

            records = recorder.records if recorder is not None else []
            if terminal_info:
                result["termination"] = {
                    key: terminal_info[key]
                    for key in ("done_reason", "skip_reason")
                    if terminal_info.get(key) is not None
                }
            ep_metrics = compute_episode_metrics(records, terminal_info, wall, game)
            result["metrics"] = ep_metrics
            if checkpoint is not None:
                result['checkpoint'] = checkpoint.metadata()
                if 'video' in result:
                    result['video'].update(start_step=checkpoint.start_step,
                                           end_step_exclusive=(checkpoint.data or {}).get('step', checkpoint.start_step))
                    result['video_segment'] = dict(result['video'])
                result['video_segments'] = checkpoint.video_segments(result.get('video'))
                if 'video' not in result and result['video_segments']:
                    result['video'] = result['video_segments'][-1]
                result['video_coverage'] = 'segments' if checkpoint.resume else 'single_attempt'
                result['attempt_wall_time_s'] = wall
                if checkpoint.resume:
                    result['metrics']['wall_time_s'] = round(wall + checkpoint.prior_elapsed, 4)
                if record_video and result['status'] == 'ok':
                    from .checkpoint import finalize_video
                    try:
                        result['video'], result['video_validation'] = finalize_video(result['video_segments'], run_dir)
                        result['video_coverage'] = 'complete_verified'
                    except Exception as exc:
                        result['status'] = 'error'
                        result['error'] = f'VideoValidationError: {exc}'
                        exp_log.error(result['error'])
            exp_log.info(
                "Episode metrics: %s", json.dumps(ep_metrics, ensure_ascii=False)
            )

            with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            if records:
                try:
                    trace_dir = write_reflection_trace_from_summary(
                        run_dir, result, records, clock_mode=clock_mode,
                    )
                    exp_log.info("Reflection trace -> %s", trace_dir)
                except Exception as e:  # noqa: BLE001
                    exp_log.warning("reflection_trace generation raised: %s", e)

    return result


def make_solo_env(
    env_spec: EnvSpec,
    params: ParamsPoint,
    adapter,
    game: GameSpec,
) -> SoloEnv:
    """Build benchmark-local SoloEnv with the resolved obs_delay.

    Resolution order (highest priority first):
      params.obs_delay > env_spec.obs_delay > game.obs_delay
    Uses ``is not None`` (not ``or``) so explicit 0.0 in YAML overrides a
    non-zero game default - ``or`` would treat 0.0 as falsy and fall through.
    """
    if params.obs_delay is not None:
        obs_delay = params.obs_delay
    elif env_spec.obs_delay is not None:
        obs_delay = env_spec.obs_delay
    else:
        obs_delay = game.obs_delay
    return BenchmarkSoloEnv(
        adapter=adapter,
        host=env_spec.host,
        port=env_spec.port,
        task=env_spec.task,
        max_steps=env_spec.max_steps,
        screenshot_quality=env_spec.screenshot_quality,
        map=env_spec.map,
        map_wait=env_spec.map_wait,
        obs_delay=obs_delay,
    )


def _configure_lcm_timing(agent, enabled: bool, log) -> None:
    backend = getattr(agent, "backend", None)
    if backend is None:
        return
    configure = getattr(backend, "enable_lcm_timing", None)
    if callable(configure):
        configure(enabled)
        if enabled:
            log.info(
                "LCM timing enabled for backend=%s model=%s",
                type(backend).__name__, getattr(backend, "model", None),
            )


def _estimate_decision_latency(agent, act_latency_s: float, clock_mode: str) -> tuple[float, dict]:
    # act_latency_s remains a diagnostic; it must never drive the LCM clock.
    return decision_latency(agent, clock_mode)

def _is_policy_agent(exp) -> bool:
    kind = (getattr(exp.agent, "kind", "") or "").strip().lower()
    return kind in {"openp2p", "nitrogen"}


def _connect_dedicated_live_client(env, exp, log):
    if not _is_policy_agent(exp):
        return None
    try:
        client = UE5Client(
            host=env.host,
            port=env.port,
            screenshot_quality=env.screenshot_quality,
        )
        if client.connect():
            log.info("Dedicated policy live viewer client connected")
            return client
        log.warning("Dedicated policy live viewer client failed; falling back to env client")
    except Exception as exc:  # noqa: BLE001
        log.warning("Dedicated policy live viewer client failed: %s", exc)
    return None


def _run_episode(
    env,
    agent,
    adapter,
    exp,
    recorder,
    log,
    viewer=None,
    log_vlm=False,
    clock_mode: str = "realtime",
    video_recorder: VideoRecorder | None = None,
    checkpoint=None,
) -> dict:
    """Main gym loop for a single episode. Returns terminal info dict."""
    clock_mode = _normalize_clock_mode(clock_mode)
    if checkpoint is not None and checkpoint.resume:
        obs, step = checkpoint.restore(env, agent, recorder)
        if checkpoint.data['phase'] == 'terminal':
            return checkpoint.data['terminal_info']
    else:
        obs = env.reset()
        if clock_mode in {"lfm", "lcm"}:
            env.pause()
        reset_agent = getattr(agent, "reset", None)
        if callable(reset_agent):
            reset_agent()
        elif hasattr(agent, "reset_history"):
            agent.reset_history()
        step = 0
        if checkpoint is not None:
            checkpoint.capture('ready', env, agent, obs, step, recorder)

    task = exp.env.task
    max_steps = exp.env.max_steps
    schema = adapter.action_schema
    endpoint = f"ip={env.host} port={env.port}"

    terminal_info: dict = {}
    log.info(
        "Episode start | task=%s | max_steps=%d | clock_mode=%s",
        task, max_steps, clock_mode,
    )

    # Start streaming screenshots straight from UE5 (decoupled from the
    # agent's think-act cadence, so the window stays smooth even when
    # the VLM takes several seconds per step).
    live_client = None
    if viewer is not None and env.client is not None:
        live_client = _connect_dedicated_live_client(env, exp, log)
        viewer.start_streaming(
            live_client or env.client,
            fps=30,
            drop_stale_frames=_is_policy_agent(exp),
        )
        viewer.set_status(f"{endpoint} | step={step}")
        viewer.clear_log(header=f"=== {exp.agent.model} | {exp.run_id.rsplit('/', 1)[0]} ===")

    if video_recorder is not None and env.client is not None:
        if checkpoint is not None and checkpoint.resume:
            for record in recorder.records[-64:]:
                reason, action_text = _split_vlm_response(record.get('info', {}).get('vlm_response', ''))
                video_recorder.push_step(record['step'], reason, action_text)
        video_recorder.start_streaming(env)

    try:
        while True:
            decision_step = step
            decision_obs = obs
            t_act = time.time()
            if checkpoint is not None and checkpoint.data['phase'] == 'action_ready':
                action = checkpoint.data['action']
                act_latency = checkpoint.data['act_latency']
                log.info('Reusing saved model response at step %d; no API request', step)
            else:
                action = agent.act(decision_obs, task, schema)
                act_latency = time.time() - t_act
                if checkpoint is not None:
                    checkpoint.capture('action_ready', env, agent, obs, step, recorder,
                                       action=action, latency=act_latency)

            # Capture the agent's raw VLM response (if any) so it ends up
            # in the step record - needed for downstream error analysis.
            vlm_response = getattr(agent, "last_vlm_response", None) or None

            # Push the planned reason + action to the side panel BEFORE
            # executing, so the viewer shows the agent's intent in sync with
            # (slightly before) the UE5 motion it causes. Label with the
            # current ``step`` - i.e. the index of the observation the VLM
            # just reasoned over (``step_0000`` for the very first action,
            # matching the screenshot filename on disk).
            reason_text, action_text = _split_vlm_response(vlm_response or "")
            if viewer is not None:
                viewer.push_step(decision_step, reason_text, action_text)

            lcm_decision_delay_s, latency_meta = _estimate_decision_latency(
                agent, act_latency, clock_mode,
            )
            show_lcm_thinking = (
                video_recorder is not None
                and video_recorder.with_text_panel
                and clock_mode == "lcm"
                and lcm_decision_delay_s > 0
            )
            if video_recorder is not None and video_recorder.with_text_panel:
                if show_lcm_thinking:
                    video_recorder.push_thinking(decision_step)
                else:
                    video_recorder.push_step(decision_step, reason_text, action_text)

            if lcm_decision_delay_s > 0:
                env.advance_game_time(lcm_decision_delay_s, pause_after=True)

            if show_lcm_thinking:
                video_recorder.push_step(decision_step, reason_text, action_text)

            if checkpoint is not None:
                if video_recorder is not None and decision_step == checkpoint.start_step:
                    video_recorder.write_frame(decision_obs['image'])
                checkpoint.mark_executing()
            obs, reward, done, info = env.step(
                action,
                pause_before_observe=(clock_mode in {"lfm", "lcm"}),
            )

            info = dict(info or {})
            info["clock_mode"] = clock_mode
            info["lcm_decision_delay_s"] = round(lcm_decision_delay_s, 4)
            info["act_latency_s"] = round(act_latency, 4)
            info.update(latency_meta)
            if vlm_response:
                info["vlm_response"] = vlm_response

            action_metadata = getattr(agent, "last_action_metadata", None)
            if isinstance(action_metadata, dict) and action_metadata:
                info["random_policy"] = dict(action_metadata)

            fp_stats = _framepack_last_stats(agent)
            if fp_stats is not None:
                info["frame_pack"] = {
                    "kernel": fp_stats.kernel,
                    "n_input": fp_stats.n_input_frames,
                    "n_output": fp_stats.n_output_frames,
                    "token_ratio": fp_stats.approx_token_ratio,
                    "resolutions": fp_stats.resolutions,
                }

            if recorder is not None:
                recorder.record(decision_step, decision_obs, action, done, info)
                if info.get("done_reason") == "game_over":
                    recorder.record_terminal_observation(obs)

            action_executed = info.get("action_executed", True) is not False
            if action_executed:
                step += 1
            if checkpoint is not None:
                checkpoint.capture('terminal' if done else 'ready', env, agent, obs, step,
                                   recorder, terminal_info=info if done else None)
                if video_recorder is not None:
                    video_recorder.write_frame(obs['image'])

            if viewer is not None:
                # Keep the bottom status bar compact; detailed run id / done
                # reason remain in logs and result files.
                score = info.get("score")
                score_str = f" score={score}" if score is not None else ""
                latency_label = "delay" if clock_mode == "lcm" else "wall"
                latency_value = lcm_decision_delay_s if clock_mode == "lcm" else act_latency
                viewer.set_status(
                    f"{endpoint} | step={step}{score_str} | "
                    f"{latency_label}={latency_value:.2f}s"
                )

            log.info(
                "step=%d wall_decision_time=%.2fs lcm_decision_delay=%.2fs source=%s done=%s reason=%s",
                decision_step, act_latency, lcm_decision_delay_s,
                info.get("lcm_decision_delay_source"), done, info.get("done_reason"),
            )
            if log_vlm and vlm_response:
                # --log only controls console verbosity; it must not change
                # what files are saved.
                print(f"\n-- VLM step={decision_step} --\n{vlm_response}\n", flush=True)

            if done:
                terminal_info = info
                break

    finally:
        if viewer is not None:
            viewer.stop_streaming()
        if live_client is not None:
            live_client.disconnect()
        if video_recorder is not None:
            video_recorder.stop_streaming()

    return terminal_info


# -- Full sweep ---------------------------------------------------------

def _normalize_clock_mode(clock_mode: str) -> str:
    return normalize_clock_mode(clock_mode)

def run_benchmark(
    experiments: list[Experiment],
    output_root: str,
    game: GameSpec,
    live: bool = False,
    log_vlm: bool = False,
    api_debug: bool = False,
    clock_mode: str = "realtime",
    record_video: bool = False,
    video_fps: int = 30,
    video_with_thinking: bool = False,
    video_thinking_layout: str = "side",
    flat_output: bool = False,
    checkpoint_path: str | None = None,
    resume_checkpoint: bool = False,
) -> dict:
    """Run every experiment and return the aggregated summary dict.

    When ``live=True``, a small Tk window shows the current agent's
    observation in real time. The viewer is best-effort - if Tk can't
    start (headless box, missing display), the benchmark keeps going.

    When ``log_vlm=True``, each step's raw VLM response is printed to
    the experiment log in addition to being recorded in summary.json.
    """
    clock_mode = _normalize_clock_mode(clock_mode)
    if resume_checkpoint and not checkpoint_path:
        raise ValueError('--resume-checkpoint requires --checkpoint')
    if checkpoint_path and len(experiments) != 1:
        raise ValueError('A checkpoint belongs to exactly one experiment/episode')
    logger = _logger_for(game)
    os.makedirs(output_root, exist_ok=True)
    logger.info(
        "Starting benchmark: game=%s | %d experiment(s) | output_root=%s | clock_mode=%s",
        game.name, len(experiments), output_root, clock_mode,
    )

    viewer: LiveViewer | None = None
    variant_progress = VariantProgress.from_environment(game.name) if live else None
    if live:
        # ``width`` is the canvas (image) region only - the viewer adds
        # ~460 px on the right for the step log panel.
        viewer = LiveViewer(
            width=1024, height=768,
            scale_to_fit=False,
            title=f"Omni Game Arena Live - {game.name}",
            **(variant_progress.viewer_options() if variant_progress else {}),
        )
        viewer.start()
        logger.info("Live viewer enabled")

    all_results: list[dict] = []
    try:
        for i, exp in enumerate(experiments, start=1):
            logger.info("----- [%d/%d] %s -----", i, len(experiments), exp.run_id)
            if variant_progress:
                variant_progress.update(viewer)
            from .checkpoint import EpisodeCheckpoint, RunLock
            with RunLock(checkpoint_path + '.lock') if checkpoint_path else nullcontext():
                checkpoint = EpisodeCheckpoint(checkpoint_path, exp, clock_mode,
                                               resume_checkpoint) if checkpoint_path else None
                res = run_one_experiment(
                    exp, output_root, game, viewer=viewer, log_vlm=log_vlm,
                    api_debug=api_debug, clock_mode=clock_mode,
                    record_video=record_video, video_fps=video_fps,
                    video_with_thinking=video_with_thinking,
                    video_thinking_layout=video_thinking_layout,
                    flat_output=flat_output, checkpoint=checkpoint,
                )
            all_results.append(res)
            if variant_progress:
                variant_progress.update(viewer, finished=True)
            if res.get("status") == "interrupted":
                logger.warning(
                    "Benchmark interrupted by user at [%d/%d]", i, len(experiments)
                )
                break
    finally:
        summary = _finalize_summary(all_results, game)
        if viewer is not None:
            viewer.stop()

    return summary


def _finalize_summary(
    results: list[dict],
    game: GameSpec,
) -> dict:
    """Aggregate by (agent, params) cell for stdout display.

    No files written - per-experiment ``result.json`` files in each
    run dir are the source of truth; the analysis script that walks
    the tree is responsible for any cross-run aggregation.
    """
    logger = _logger_for(game)

    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        if r.get("status") == "skipped":
            continue
        parameter_dict = r.get("parameters") or r.get("params") or r.get("ablation") or {}
        agent_kind = (r.get("agent") or {}).get("kind", "vlm")
        params_id = (
            _params_short_id(parameter_dict)
            if agent_kind == "vlm"
            else agent_kind
        )
        key = (r["agent"]["model"], params_id)
        buckets.setdefault(key, []).append(r["metrics"])

    cells = []
    for (agent_name, ab_id), metrics_list in buckets.items():
        cells.append({
            "agent": agent_name,
            "params_id": ab_id,
            "aggregated": aggregate_cell_metrics(
                [m for m in metrics_list if m is not None],
                game,
            ),
        })

    summary = {
        "game": game.name,
        "n_experiments": len(results),
        "n_ok": sum(1 for r in results if r["status"] == "ok"),
        "n_error": sum(1 for r in results if r["status"] == "error"),
        "n_skipped": sum(1 for r in results if r["status"] == "skipped"),
        "n_interrupted": sum(1 for r in results if r["status"] == "interrupted"),
        "cells": cells,
        "results": results,
    }

    logger.info(
        "Benchmark finished: %d total, %d ok, %d skipped, %d error, %d interrupted",
        summary["n_experiments"], summary["n_ok"],
        summary["n_skipped"], summary["n_error"], summary["n_interrupted"],
    )
    return summary


def _params_short_id(params_dict: dict) -> str:
    """Rebuild short id from a serialized params dict (a missing
    ``temperature`` means the model never received one)."""
    ab = ParamsPoint(**params_dict)
    return ab.short_id(temperature_sent="temperature" in params_dict)
