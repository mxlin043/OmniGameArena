"""Build the compact per-episode input package used by reflection.

``reflection_trace`` is intentionally smaller than the raw benchmark log:
it keeps only the per-step visual/action/result signals that help a
reflector diagnose play quality.  Raw ``summary.json`` / ``result.json``
remain the debugging source of truth.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from .recorder import compact_action


TRACE_DIR_NAME = "reflection_trace"
FRAMES_DIR_NAME = "frames"
TERMINAL_FRAME_NAME = "terminal_observation.jpg"

_ACTION_TAG_RE = re.compile(
    r"<\|action_start\|[>}](.*?)<\|action_end\|[>}]", re.DOTALL,
)


def write_reflection_trace_from_files(run_dir: str | Path) -> Path:
    """Regenerate ``reflection_trace`` from a benchmark run directory."""
    run_dir = Path(run_dir)
    result = _load_json(run_dir / "result.json")
    summary = _load_json(run_dir / "summary.json")
    return write_reflection_trace_from_summary(run_dir, result, summary)


def write_reflection_trace_from_summary(
    run_dir: str | Path,
    result: dict[str, Any],
    summary: Iterable[dict[str, Any]],
    *,
    clock_mode: str | None = None,
) -> Path:
    """Write a compact trace from benchmark ``summary.json`` records."""
    run_dir = Path(run_dir)
    records = list(summary or [])
    if clock_mode is None:
        clock_mode = _infer_clock_mode(records)

    steps = []
    prev_score = 0.0
    prev_named_scores: dict[str, float] = {}
    for rec in records:
        info = rec.get("info") or {}
        named_scores = _has_named_scores(info)
        score = None if named_scores else _as_float(info.get("score"))
        score_delta = None
        if score is not None:
            score_delta = round(score - prev_score, 6)
            prev_score = score

        step_idx = int(rec.get("step", len(steps)))
        frame_name = f"step_{step_idx:04d}.jpg"
        step = _base_step(
            step=step_idx,
            frame=frame_name,
            action=rec.get("action"),
            vlm_response=info.get("vlm_response"),
            score=score,
            score_delta=score_delta,
            done=bool(rec.get("done", False)),
            position=info.get("character_position"),
        )
        _add_named_scores(step, info, prev_named_scores)
        _add_time_field(step, info, clock_mode)
        steps.append(step)

    _attach_terminal_frame(run_dir, steps)
    return _write_trace(
        run_dir=run_dir,
        manifest=_manifest(result, steps),
        steps=steps,
        frame_sources=_frame_sources(run_dir, steps),
    )


def write_reflection_trace_from_frames(
    run_dir: str | Path,
    result: dict[str, Any],
    frames: Iterable[dict[str, Any]],
    *,
    model: str | None = None,
    clock_mode: str = "realtime",
) -> Path:
    """Write a compact trace from learnability-style in-memory frames.

    Learnability frames store the post-action observation at step ``N``.
    The decision image for that action is therefore ``step_(N-1).jpg``.
    """
    run_dir = Path(run_dir)
    frame_list = list(frames or [])
    steps = []
    prev_score = 0.0
    prev_named_scores: dict[str, float] = {}

    for frame in frame_list:
        if frame.get("action") is None:
            continue
        raw_step = int(frame.get("step", len(steps) + 1))
        decision_step = max(0, raw_step - 1)
        frame_name = f"step_{decision_step:04d}.jpg"

        named_scores = _has_named_scores(frame)
        score = None if named_scores else _as_float(frame.get("score"))
        score_delta = None
        if score is not None:
            score_delta = round(score - prev_score, 6)
            prev_score = score

        step = _base_step(
            step=decision_step,
            frame=frame_name,
            action=compact_action(frame.get("action")),
            vlm_response=frame.get("vlm_response"),
            score=score,
            score_delta=score_delta,
            done=bool(frame.get("done", False)),
            position=frame.get("character_position"),
        )
        _add_named_scores(step, frame, prev_named_scores)
        _add_time_field(step, frame, clock_mode)
        steps.append(step)

    _attach_terminal_frame(run_dir, steps)
    result_for_manifest = dict(result or {})
    if model and not _extract_model(result_for_manifest):
        result_for_manifest["model"] = model

    return _write_trace(
        run_dir=run_dir,
        manifest=_manifest(result_for_manifest, steps),
        steps=steps,
        frame_sources=_frame_sources(run_dir, steps),
    )


def load_reflection_trace(run_dir: str | Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]] | None:
    """Load ``reflection_trace`` if it exists, otherwise return ``None``."""
    trace_dir = Path(run_dir) / TRACE_DIR_NAME
    manifest_path = trace_dir / "manifest.json"
    steps_path = trace_dir / "steps.jsonl"
    if not manifest_path.exists() or not steps_path.exists():
        return None
    manifest = _load_json(manifest_path)
    steps = []
    with steps_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                steps.append(json.loads(line))
    return trace_dir, manifest, steps


def _write_trace(
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    steps: list[dict[str, Any]],
    frame_sources: Iterable[tuple[Path, str]],
) -> Path:
    trace_dir = run_dir / TRACE_DIR_NAME
    frames_dir = trace_dir / FRAMES_DIR_NAME
    if trace_dir.exists():
        shutil.rmtree(trace_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    for src, rel_frame in frame_sources:
        if src.exists():
            dst = trace_dir / rel_frame
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    with (trace_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(_drop_none(manifest), f, indent=2, ensure_ascii=False)

    with (trace_dir / "steps.jsonl").open("w", encoding="utf-8") as f:
        for step in steps:
            f.write(json.dumps(_drop_none(step), ensure_ascii=False) + "\n")

    return trace_dir


def _attach_terminal_frame(run_dir: Path, steps: list[dict[str, Any]]) -> None:
    """Attach terminal post-action frame to the final done step when present."""
    terminal = run_dir / TERMINAL_FRAME_NAME
    if not terminal.exists():
        return
    for step in reversed(steps):
        if step.get("done"):
            step["terminal_frame"] = f"{FRAMES_DIR_NAME}/{TERMINAL_FRAME_NAME}"
            return


def _frame_sources(run_dir: Path, steps: list[dict[str, Any]]) -> list[tuple[Path, str]]:
    sources: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for step in steps:
        for key in ("frame", "terminal_frame"):
            rel_frame = step.get(key)
            if not rel_frame or rel_frame in seen:
                continue
            seen.add(rel_frame)
            sources.append((run_dir / rel_frame.split("/", 1)[-1], rel_frame))
    return sources


def _manifest(result: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = {
        "total_steps": _extract_total_steps(steps),
        "model": _extract_model(result),
        "outcome": result.get("outcome"),
        "winner": result.get("winner"),
        "player_label": result.get("player_label"),
    }
    if _is_multiplayer_result(result):
        scores = _extract_scores(result, include_final_fallback=False)
        if not scores and str(result.get("mode") or "").lower() == "coop":
            team_score = _extract_final_score(result)
            if team_score is not None:
                scores = {"team": team_score}
        manifest["scores"] = scores
    else:
        manifest["final_score"] = _extract_final_score(result)
    return manifest


def _base_step(
    *,
    step: int,
    frame: str,
    action: Any,
    vlm_response: str | None,
    score: float | None,
    score_delta: float | None,
    done: bool,
    position: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "frame": f"{FRAMES_DIR_NAME}/{frame}",
        "action": action,
        "agent_reasoning": _extract_reasoning(vlm_response),
        "score": score,
        "score_delta": score_delta,
        "done": done,
        # Optional: present only when UE5 returned character_position in
        # get_score. Earlier-recorded episodes (e.g. round 0 PDQ baselines
        # captured before this feature shipped) will not have this field.
        "position": position,
    }


def _add_named_scores(
    step: dict[str, Any],
    source: dict[str, Any],
    prev_scores: dict[str, float],
) -> None:
    for name in ("own", "opponent", "teammate", "team"):
        field = f"{name}_score"
        score = _as_float(source.get(field))
        if score is None:
            continue
        step[field] = score
        prev_score = prev_scores.get(name, 0.0)
        step[f"{field}_delta"] = round(score - prev_score, 6)
        prev_scores[name] = score


def _has_named_scores(source: dict[str, Any]) -> bool:
    return any(
        _as_float(source.get(f"{name}_score")) is not None
        for name in ("own", "opponent", "teammate", "team")
    )


def _add_time_field(step: dict[str, Any], source: dict[str, Any], clock_mode: str | None) -> None:
    mode = (clock_mode or "realtime").strip().lower()
    if mode == "pdq":
        return
    if mode == "lcrt":
        value = source.get("lcrt_decision_delay_s", source.get("decision_delay_s"))
        if value is not None:
            step["decision_delay_s"] = value
        return
    value = source.get("wall_decision_time_s", source.get("act_latency_s"))
    if value is not None:
        step["wall_decision_time_s"] = value


def _infer_clock_mode(records: list[dict[str, Any]]) -> str:
    for rec in records:
        info = rec.get("info") or {}
        if info.get("clock_mode"):
            return str(info["clock_mode"])
    return "realtime"


def _extract_reasoning(text: str | None) -> str:
    if not text:
        return ""
    match = _ACTION_TAG_RE.search(text)
    if match:
        text = text[: match.start()]
    return " ".join(text.split())


def _extract_total_steps(steps: list[dict[str, Any]]) -> int:
    """Return how many actions were executed in this trace."""
    return len([step for step in steps if step.get("action") is not None])


def _extract_final_score(result: dict[str, Any]) -> float | None:
    metrics = result.get("metrics") or {}
    game = metrics.get("game") or {}
    for value in (
        game.get("score"),
        game.get("final_score"),
        result.get("score"),
        result.get("final_score"),
    ):
        score = _as_float(value)
        if score is not None:
            return score
    return None


def _extract_named_score(result: dict[str, Any], key: str) -> float | None:
    scores = result.get("scores") or {}
    score_key = key.removesuffix("_score")
    metrics = result.get("metrics") or {}
    game = metrics.get("game") or {}
    terminal_info = result.get("terminal_info") or {}
    for value in (
        scores.get(score_key) if isinstance(scores, dict) else None,
        result.get(key),
        game.get(key),
        terminal_info.get(key),
    ):
        score = _as_float(value)
        if score is not None:
            return score
    return None


def _extract_scores(
    result: dict[str, Any],
    *,
    include_final_fallback: bool = True,
) -> dict[str, float] | None:
    scores: dict[str, float] = {}
    for key in ("own", "opponent", "teammate", "team"):
        score = _extract_named_score(result, f"{key}_score")
        if score is not None:
            scores[key] = score

    if include_final_fallback and "own" not in scores:
        final_score = _extract_final_score(result)
        if final_score is not None:
            scores["own"] = final_score

    return scores or None


def _is_multiplayer_result(result: dict[str, Any]) -> bool:
    mode = str(result.get("mode") or "").lower()
    if mode in {"coop", "pvp", "multi", "multiplayer"}:
        return True
    scores = result.get("scores")
    return isinstance(scores, dict) and any(
        key in scores for key in ("teammate", "teammates", "team", "opponent")
    )


def _extract_model(result: dict[str, Any]) -> str | None:
    agent = result.get("agent") or {}
    return agent.get("model") or result.get("model")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _drop_none(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if v is not None}
