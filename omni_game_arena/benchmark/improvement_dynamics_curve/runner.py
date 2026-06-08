"""Main runner for Improvement Dynamics Curve (IDC)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import Experiment
from ..games import get_game
from .config import IDCConfig
from .episodes import run_episode_set
from .io import atomic_write_json, atomic_write_text, load_json, read_text_if_exists, resolve_idc_run_dir
from .metrics import aggregate_episode_results, compute_curve_metrics
from .official import stage_round0_from_official_pdq
from .reflectors.agentic import AgenticIDCReflector
from .reflectors.base import IDCReflectionInput

logger = logging.getLogger(__name__)


def run_idc(cfg: IDCConfig) -> dict[str, Any]:
    game = get_game(cfg.game_name)
    if not cfg.env_spec.task:
        cfg.env_spec.task = game.default_task

    run_dir = resolve_idc_run_dir(
        output_root=cfg.output_root,
        game_name=cfg.game_name,
        model=cfg.agent_profile.model,
        run_dir=cfg.run_dir,
    )
    cfg.run_dir = str(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = run_dir / "idc_config.json"
    if not config_path.exists():
        atomic_write_json(config_path, cfg.to_json_dict())

    viewer = _maybe_start_viewer(cfg)
    _set_live_status(viewer, "IDC | initializing reflector")
    reflector = _build_reflector(cfg)
    exp_template = Experiment(
        game=cfg.game_name,
        env=cfg.env_spec,
        agent=cfg.agent_profile,
        params=cfg.params,
        episode_idx=0,
        run_id="idc/template",
    )

    try:
        state = _load_or_init_state(run_dir, cfg)
        _update_live_progress(
            viewer,
            run_dir,
            cfg,
            state,
            "initializing IDC run",
        )

        _set_live_status(viewer, "IDC | staging round 00 official PDQ traces")
        round0 = stage_round0_from_official_pdq(
            pdq_root=cfg.official_pdq_root,
            game_name=cfg.game_name,
            model=cfg.agent_profile.model,
            run_dir=run_dir,
            success_threshold=cfg.success_threshold,
            mode=getattr(game, "mode", "solo") or "solo",
        )
        _mark_round_state(state, 0, episodes="complete", score="complete")
        _save_state(run_dir, state, "round_00_scored")
        _update_live_progress(
            viewer,
            run_dir,
            cfg,
            state,
            "round 00 official PDQ traces staged",
        )

        _ensure_reflection(
            run_dir=run_dir,
            round_idx=0,
            cfg=cfg,
            reflector=reflector,
            previous_skill="",
            round_result=round0,
            state=state,
            viewer=viewer,
        )

        for round_idx in range(1, cfg.rounds + 1):
            prev_skill = read_text_if_exists(
                run_dir / f"round_{round_idx - 1:02d}" / "skill_out.md"
            )
            round_dir = run_dir / f"round_{round_idx:02d}"
            round_dir.mkdir(parents=True, exist_ok=True)

            round_result_path = round_dir / "round_result.json"
            if round_result_path.exists():
                round_result = load_json(round_result_path)
                _mark_round_state(state, round_idx, episodes="complete", score="complete")
                _save_state(run_dir, state, f"round_{round_idx:02d}_scored")
                _update_live_progress(
                    viewer,
                    run_dir,
                    cfg,
                    state,
                    f"round {round_idx:02d} episodes already complete",
                )
            else:
                # This round's episodes are not yet recorded -> we are going
                # to run them. Write skill_in.md only when it is missing:
                #   - fresh round         -> create the record (inherited skill);
                #   - resume mid-episodes -> it already exists, leave it as-is.
                # A fully-complete round never reaches this branch, so a
                # regression-guard-replaced skill_in is never clobbered on
                # resume (this was the round_05 corruption bug).
                skill_in_path = round_dir / "skill_in.md"
                if not skill_in_path.exists():
                    atomic_write_text(skill_in_path, prev_skill)
                _mark_round_state(
                    state,
                    round_idx,
                    episodes=f"0/{cfg.episodes_per_round}",
                    score="pending",
                )
                _save_state(run_dir, state, f"round_{round_idx:02d}_running_episodes")
                _update_live_progress(
                    viewer,
                    run_dir,
                    cfg,
                    state,
                    f"round {round_idx:02d} running episodes",
                )
                episodes = run_episode_set(
                    round_dir=round_dir,
                    round_idx=round_idx,
                    skill_text=prev_skill,
                    game=game,
                    exp_template=exp_template,
                    n_episodes=cfg.episodes_per_round,
                    clock_mode="pdq",
                    live_viewer=viewer,
                    log_vlm=cfg.log_vlm,
                    api_debug=cfg.api_debug,
                    progress_callback=lambda event, round_idx=round_idx: (
                        _handle_episode_progress(
                            viewer=viewer,
                            run_dir=run_dir,
                            cfg=cfg,
                            state=state,
                            event=event,
                        )
                    ),
                )
                aggregate = aggregate_episode_results(
                    episodes,
                    success_threshold=cfg.success_threshold,
                )
                round_result = {
                    "round_idx": round_idx,
                    "source": "idc_episode_run",
                    "skill_in": f"round_{round_idx:02d}/skill_in.md",
                    "episodes": episodes,
                    **aggregate,
                }
                atomic_write_json(round_result_path, round_result)
                _mark_round_state(state, round_idx, episodes="complete", score="complete")
                _save_state(run_dir, state, f"round_{round_idx:02d}_scored")
                _update_live_progress(
                    viewer,
                    run_dir,
                    cfg,
                    state,
                    f"round {round_idx:02d} scored",
                )

            if round_idx < cfg.rounds:
                _ensure_reflection(
                    run_dir=run_dir,
                    round_idx=round_idx,
                    cfg=cfg,
                    reflector=reflector,
                    previous_skill=prev_skill,
                    round_result=round_result,
                    state=state,
                    viewer=viewer,
                )

            _write_curve_and_metrics(run_dir, cfg)

        _write_curve_and_metrics(run_dir, cfg)
        _save_state(run_dir, state, "completed")
        _set_live_status(viewer, "IDC | completed")
        _update_live_progress(viewer, run_dir, cfg, state, "completed")
        return {
            "run_dir": str(run_dir),
            "curve": load_json(run_dir / "idc_curve.json"),
            "metrics": load_json(run_dir / "idc_metrics.json"),
        }
    finally:
        if viewer is not None:
            viewer.stop()


def _ensure_reflection(
    *,
    run_dir: Path,
    round_idx: int,
    cfg: IDCConfig,
    reflector: AgenticIDCReflector,
    previous_skill: str,
    round_result: dict[str, Any],
    state: dict[str, Any],
    viewer=None,
) -> str:
    round_dir = run_dir / f"round_{round_idx:02d}"
    skill_out_path = round_dir / "skill_out.md"
    if skill_out_path.exists():
        _mark_round_state(state, round_idx, reflection="complete")
        _save_state(run_dir, state, f"round_{round_idx:02d}_reflected")
        _update_live_progress(
            viewer,
            run_dir,
            cfg,
            state,
            f"round {round_idx:02d} reflection already complete",
        )
        return read_text_if_exists(skill_out_path)

    _mark_round_state(state, round_idx, reflection="running")
    _save_state(run_dir, state, f"round_{round_idx:02d}_reflecting")
    _set_live_status(viewer, f"IDC | round {round_idx:02d} reflecting", busy=True)
    _update_live_progress(
        viewer,
        run_dir,
        cfg,
        state,
        f"round {round_idx:02d} calling reflector",
    )
    aggregate, best_skill = _reflection_context(
        run_dir=run_dir,
        round_idx=round_idx,
        round_result=round_result,
    )
    notebook_so_far = read_text_if_exists(run_dir / "notebook.md")
    inp = IDCReflectionInput(
        game_name=cfg.game_name,
        round_idx=round_idx,
        previous_skill=previous_skill,
        episodes=[],
        aggregate=aggregate,
        best_skill=best_skill,
        round_dir=round_dir,
        notebook_so_far=notebook_so_far,
    )
    # Reflection API log is ALWAYS on (not gated by cfg.api_debug, which
    # controls the much larger player-side dump). Every chat / chat_with_tools
    # call inside the agentic reflection loop becomes one JSON file under
    # round_dir/reflection_api_log/, with images extracted to
    # reflection_api_log/images/ keyed by SHA-256.
    _attach_reflection_api_log(reflector, round_dir)
    # The reflector occasionally ends its agentic loop without ever calling
    # submit_skill, producing no skill. Retry once; if it still submits
    # nothing, carry over the previous round's skill - but loudly (warn),
    # so a no-op reflection round is visible instead of silent.
    max_attempts = 2
    result = None
    skill = ""
    for attempt in range(1, max_attempts + 1):
        # Force-submit nudge ONLY on a retry, never the first attempt.
        inp.force_submit = attempt > 1
        result = reflector.reflect(inp)
        skill = (result.skill_text or "").strip()
        if skill:
            break
        if attempt < max_attempts:
            logger.warning(
                "IDC round %d: reflector submitted no skill "
                "(attempt %d/%d) - re-running reflection.",
                round_idx, attempt, max_attempts,
            )
    if not skill:
        logger.warning(
            "IDC round %d: reflection produced no skill after %d attempt(s); "
            "carrying over the previous round's skill unchanged.",
            round_idx, max_attempts,
        )
        skill = previous_skill
    atomic_write_text(round_dir / "reflection_prompt.md", result.prompt_text)
    atomic_write_text(round_dir / "reflection_response.md", result.response_text)
    atomic_write_text(skill_out_path, skill)
    if result.api_response is not None:
        atomic_write_json(round_dir / "reflection_api_response.json", result.api_response)
    # Persist the full transcript + per-tool-call trace for audit.
    if result.messages is not None:
        atomic_write_json(
            round_dir / "analysis_messages.json",
            [_serialise_message_for_log(m) for m in result.messages],
        )
    if result.trace is not None:
        _write_jsonl(round_dir / "analysis_trace.jsonl", result.trace)
    # Notebook: only update the canonical run-level file when the
    # reflector actually emitted a new notebook this round. None means
    # "no update" means keep whatever earlier rounds wrote.
    if result.notebook_out is not None:
        atomic_write_text(run_dir / "notebook.md", result.notebook_out)
        logger.info(
            "IDC notebook updated by round %d (%d chars)",
            round_idx, len(result.notebook_out),
        )
    _mark_round_state(state, round_idx, reflection="complete")
    _save_state(run_dir, state, f"round_{round_idx:02d}_reflected")
    _set_live_status(viewer, f"IDC | round {round_idx:02d} reflection complete")
    _update_live_progress(
        viewer,
        run_dir,
        cfg,
        state,
        f"round {round_idx:02d} reflection complete",
    )
    return skill


def _reflection_context(
    *,
    run_dir: Path,
    round_idx: int,
    round_result: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    points = []
    for idx in range(0, round_idx + 1):
        result = round_result if idx == round_idx else None
        path = run_dir / f"round_{idx:02d}" / "round_result.json"
        if result is None and path.exists():
            result = load_json(path)
        if result is None:
            continue
        points.append({
            "round_idx": idx,
            "mean_score": result.get("mean_score"),
            "n": result.get("n"),
            "scores": result.get("scores"),
        })

    best_point = _best_curve_point(points)
    previous_point = points[-2] if len(points) >= 2 else None
    aggregate = {
        "mean_score": round_result.get("mean_score"),
        "scores": round_result.get("scores"),
        "n": round_result.get("n"),
        "curve_so_far": points,
        "best_so_far": best_point,
        "previous_round": previous_point,
    }
    if previous_point is not None:
        current = _as_float(round_result.get("mean_score"))
        previous = _as_float(previous_point.get("mean_score"))
        if current is not None and previous is not None:
            aggregate["delta_vs_previous_round"] = round(current - previous, 6)

    best_skill = _best_measured_skill(run_dir, best_point)
    return aggregate, best_skill


def _best_curve_point(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [p for p in points if _as_float(p.get("mean_score")) is not None]
    if not scored:
        return None
    return max(scored, key=lambda p: _as_float(p.get("mean_score")) or float("-inf"))


def _best_measured_skill(run_dir: Path, best_point: dict[str, Any] | None) -> str:
    if not best_point:
        return ""
    best_round = int(best_point.get("round_idx") or 0)
    if best_round <= 0:
        return ""
    return read_text_if_exists(run_dir / f"round_{best_round - 1:02d}" / "skill_out.md")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_curve_and_metrics(run_dir: Path, cfg: IDCConfig) -> None:
    points = []
    for round_idx in range(0, cfg.rounds + 1):
        path = run_dir / f"round_{round_idx:02d}" / "round_result.json"
        if not path.exists():
            continue
        result = load_json(path)
        points.append({
            "round_idx": round_idx,
            "mean_score": result.get("mean_score"),
            "n": result.get("n"),
            "scores": result.get("scores"),
        })
    curve = {
        "x_label": "round_idx",
        "y_labels": ["mean_score"],
        "points": points,
    }
    atomic_write_json(run_dir / "idc_curve.json", curve)
    atomic_write_json(run_dir / "idc_metrics.json", compute_curve_metrics(points))


def _serialise_message_for_log(msg: dict) -> dict:
    """Strip raw image bytes from a transcript message for human-readable
    dumping. Images become a small placeholder block; everything else is
    kept verbatim.
    """
    role = msg.get("role")
    content = msg.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if not isinstance(content, list):
        return {"role": role, "content": content}

    new_content = []
    for blk in content:
        if not isinstance(blk, dict):
            new_content.append(blk)
            continue
        t = blk.get("type")
        if t == "image":
            new_content.append({
                "type": "image",
                "_placeholder": "<image bytes elided>",
            })
        elif t == "tool_result":
            inner = blk.get("content")
            if isinstance(inner, list):
                inner_clean = []
                for ib in inner:
                    if isinstance(ib, dict) and ib.get("type") == "image":
                        inner_clean.append({
                            "type": "image",
                            "_placeholder": "<image bytes elided>",
                        })
                    else:
                        inner_clean.append(ib)
                new_content.append({**blk, "content": inner_clean})
            else:
                new_content.append(blk)
        else:
            new_content.append(blk)
    return {"role": role, "content": new_content}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """Write a list of dicts as JSONL. Atomic at the file level."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str))
            f.write("\n")
    os.replace(tmp, path)


def _build_reflector(cfg: IDCConfig):
    model = cfg.reflector_model or cfg.agent_profile.model
    logger.info(
        "IDC reflector: agentic (model=%s, max_iter=%d, "
        "validator=%s, validate_cap=%d)",
        model, cfg.max_reflection_iterations,
        cfg.validator_model or f"(same as reflector: {model})",
        cfg.max_validate_skill_calls,
    )
    return AgenticIDCReflector(
        game_name=cfg.game_name,
        model=model,
        temperature=cfg.reflector_temperature,
        resize_size=cfg.reflector_resize_size,
        max_iterations=cfg.max_reflection_iterations,
        validator_model=cfg.validator_model or None,
        validator_temperature=cfg.validator_temperature,
        max_validate_skill_calls=cfg.max_validate_skill_calls,
    )


def _attach_reflection_api_log(reflector, round_dir: Path) -> None:
    """Always-on per-call dump of the reflection backend.

    Reuses :class:`omni_game_arena.utils.api_debug.ApiDebugLogger`, which
    already handles role/content serialization + image extraction
    (Anthropic ``image`` and OpenAI ``image_url`` blocks both become
    ``image_ref`` pointers to ``images/<sha256>.jpg`` on disk).

    The reflector backend may live across multiple rounds (one
    AgenticIDCReflector instance is built per IDC run, not per round), so
    we swap its ``debug_logger`` each round to redirect dumps into the
    current round's directory. The logger object owns ``call_idx``, so a
    fresh one per round restarts the call counter at 1, making each
    round's dump self-contained.
    """
    backend = getattr(reflector, "backend", None)
    if backend is None:
        return
    try:
        from omni_game_arena.utils.api_debug import ApiDebugLogger

        out_dir = round_dir / "reflection_api_log"
        backend.debug_logger = ApiDebugLogger(out_dir)
        logger.info("IDC reflection_api_log -> %s", out_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("IDC reflection_api_log setup failed: %s", exc)


def _handle_episode_progress(
    *,
    viewer,
    run_dir: Path,
    cfg: IDCConfig,
    state: dict[str, Any],
    event: dict[str, Any],
) -> None:
    event_name = str(event.get("event") or "episode_update")
    round_idx = int(event.get("round_idx") or 0)
    episode_id = str(event.get("episode_id") or "ep_??")
    completed = int(event.get("completed") or 0)
    n_episodes = int(event.get("n_episodes") or cfg.episodes_per_round)

    if event_name == "episode_start":
        episode_state = f"{completed}/{n_episodes} running {episode_id}"
        message = f"round {round_idx:02d} {episode_id} running"
    elif event_name == "episode_complete":
        score = _format_float(event.get("score"))
        episode_state = f"{completed}/{n_episodes}"
        message = f"round {round_idx:02d} {episode_id} complete score={score}"
    elif event_name == "episode_skipped":
        score = _format_float(event.get("score"))
        episode_state = f"{completed}/{n_episodes}"
        message = f"round {round_idx:02d} {episode_id} reused score={score}"
    elif event_name == "episode_overwrite_incomplete":
        episode_state = f"{completed}/{n_episodes} cleaning {episode_id}"
        message = f"round {round_idx:02d} overwriting incomplete {episode_id}"
    elif event_name == "episode_failed":
        episode_state = f"{completed}/{n_episodes} failed {episode_id}"
        message = f"round {round_idx:02d} {episode_id} failed"
    else:
        episode_state = f"{completed}/{n_episodes}"
        message = f"round {round_idx:02d} {episode_id} update"

    if event_name in ("episode_complete", "episode_skipped"):
        _record_round_score(state, round_idx, episode_id, event.get("score"))
    _mark_round_state(state, round_idx, episodes=episode_state)
    _save_state(run_dir, state, f"round_{round_idx:02d}_{event_name}")
    _set_live_status(viewer, f"IDC | {message}")
    _update_live_progress(viewer, run_dir, cfg, state, message)


def _update_live_progress(
    viewer,
    run_dir: Path,
    cfg: IDCConfig,
    state: dict[str, Any],
    message: str,
) -> None:
    if viewer is None or not hasattr(viewer, "set_progress"):
        return
    viewer.set_progress(_format_live_progress(run_dir, cfg, state, message))


def _format_live_progress(
    run_dir: Path,
    cfg: IDCConfig,
    state: dict[str, Any],
    message: str,
) -> str:
    run_tail = str(run_dir)
    if len(run_tail) > 54:
        run_tail = "..." + run_tail[-51:]

    lines = [
        "IDC Progress",
        f"Game   : {cfg.game_name}",
        f"Model  : {cfg.agent_profile.model}",
        f"Rounds : {cfg.rounds}",
        f"E/Round: {cfg.episodes_per_round}",
        f"Run    : {run_tail}",
        "",
        f"Status : {state.get('status', 'unknown')}",
        f"Now    : {message}",
        "",
        "Round status",
    ]

    current_round = state.get("current_round")
    rounds_state = state.get("rounds") or {}
    for round_idx in range(0, cfg.rounds + 1):
        item = rounds_state.get(str(round_idx), {})
        marker = ">" if current_round == round_idx else " "
        label = "R00 official" if round_idx == 0 else f"R{round_idx:02d}"
        episode_state = item.get("episodes", "pending")
        score_state = item.get("score", "pending")
        reflection_state = item.get(
            "reflection",
            "n/a" if round_idx == cfg.rounds else "pending",
        )
        summary = _round_result_summary(
            run_dir, round_idx, state, current_round == round_idx
        )
        lines.append(
            f"{marker} {label}: ep={episode_state} "
            f"score={score_state} ref={reflection_state}"
        )
        if summary:
            lines.append(f"    {summary}")
    return "\n".join(lines)


def _round_result_summary(
    run_dir: Path,
    round_idx: int,
    state: dict[str, Any],
    is_current: bool,
) -> str:
    path = run_dir / f"round_{round_idx:02d}" / "round_result.json"
    if path.exists():
        result = load_json(path)
        scores = result.get("scores") or []
        mean = _format_float(result.get("mean_score"))
        n = result.get("n", len(scores))
        body = f"mean={mean} n={n}"
        if scores:
            body += f"  [{_format_score_list(scores)}]"
        return body
    # No round_result.json yet -> in-progress (or not started). Use the
    # per-episode scores accumulated in state for a running mean.
    item = (state.get("rounds") or {}).get(str(round_idx), {})
    ep_scores = item.get("episode_scores") or {}
    vals = [v for v in ep_scores.values() if isinstance(v, (int, float))]
    if vals:
        mean = sum(vals) / len(vals)
        return (
            f"mean={_format_float(mean)} n={len(vals)} (running)  "
            f"[{_format_score_list(vals)}]"
        )
    if is_current:
        return "mean=n/a n=0 (running)"
    return ""


def _format_score_list(scores: Any) -> str:
    out = []
    for s in scores:
        try:
            out.append(f"{float(s):.2f}")
        except (TypeError, ValueError):
            out.append(str(s))
    return " ".join(out)


def _format_float(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _set_live_status(viewer, status: str, busy: bool = False) -> None:
    if viewer is None:
        return
    try:
        viewer.set_status(status, busy=busy)
    except TypeError:
        viewer.set_status(status)


def _maybe_start_viewer(cfg: IDCConfig):
    if not cfg.live:
        return None
    # Coop games (e.g. shared_floor) drive two simultaneous players via
    # two_player.py, which expects update_player / start_streaming_player
    # methods. The single-player LiveViewer does not have them; it would
    # AttributeError mid-episode. Build the right class per game.mode.
    game = get_game(cfg.game_name)
    is_coop = (getattr(game, "mode", "solo") or "solo") == "coop"

    if is_coop:
        from omni_game_arena.utils.two_player_viewer import TwoPlayerLiveViewer

        viewer = TwoPlayerLiveViewer(
            width=720,
            height=420,
            title=f"IDC - {cfg.game_name} / {cfg.agent_profile.model}",
            show_progress_panel=True,
            progress_title="IDC Progress",
            progress_initial="IDC | initializing",
            progress_log_prefix="[IDC]",
        )
        viewer.start()
        return viewer

    from omni_game_arena.utils.viewer import LiveViewer

    viewer = LiveViewer(
        width=1024,
        height=768,
        title=f"IDC - {cfg.game_name} / {cfg.agent_profile.model}",
        show_log_panel=True,
        show_progress_panel=True,
        progress_panel_width=420,
        progress_title="IDC Progress",
        progress_initial="IDC | initializing",
        progress_log_prefix="[IDC]",
    )
    viewer.start()
    return viewer


def _load_or_init_state(run_dir: Path, cfg: IDCConfig) -> dict[str, Any]:
    path = run_dir / "idc_state.json"
    if path.exists():
        return load_json(path)
    return {
        "status": "initialized",
        "game": cfg.game_name,
        "model": cfg.agent_profile.model,
        "rounds": {},
    }


def _mark_round_state(
    state: dict[str, Any],
    round_idx: int,
    **fields: str,
) -> None:
    rounds = state.setdefault("rounds", {})
    item = rounds.setdefault(str(round_idx), {})
    item.update(fields)
    state["current_round"] = round_idx


def _record_round_score(
    state: dict[str, Any],
    round_idx: int,
    episode_id: str,
    score: Any,
) -> None:
    """Remember a per-episode score for the (in-progress) round so the live
    panel can show a running mean before round_result.json is written. Keyed
    by episode_id so a resumed re-run overwrites instead of double-counting."""
    if score is None:
        return
    try:
        value = round(float(score), 4)
    except (TypeError, ValueError):
        return
    rounds = state.setdefault("rounds", {})
    item = rounds.setdefault(str(round_idx), {})
    item.setdefault("episode_scores", {})[str(episode_id)] = value


def _save_state(run_dir: Path, state: dict[str, Any], status: str) -> None:
    state["status"] = status
    atomic_write_json(run_dir / "idc_state.json", state)
    logger.info("IDC status: %s", status)
