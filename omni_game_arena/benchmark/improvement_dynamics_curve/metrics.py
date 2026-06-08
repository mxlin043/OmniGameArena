"""Metric helpers for Improvement Dynamics Curve runs.

Per-milestone `pass_rate_*` metrics used to live here, gated by
`DEFAULT_PROGRESS_THRESHOLDS = {early: 0.15, mid: 0.30, far: 0.40}`.
Those thresholds were calibrated for ObstacleRun3D's 0-1 score range
and produced all-1.0 noise on games with different scales (e.g.
solo_craft scores 9-14). No downstream code consumed the fields, and
they leaked into round_result.json / idc_context.json where the
reflector read them as misleading signal. Removed entirely. If a
per-game milestone scheme becomes useful later, route it through
GameSpec rather than a module-level default.
"""

from __future__ import annotations

from typing import Any


def score_from_result(result: dict[str, Any]) -> float | None:
    metrics = result.get("metrics") or {}
    game = metrics.get("game") or {}
    for value in (
        game.get("score"),
        game.get("final_score"),
        result.get("score"),
        result.get("final_score"),
    ):
        if isinstance(value, (int, float)):
            return float(value)
    return None


def aggregate_episode_results(
    episodes: list[dict[str, Any]],
    *,
    success_threshold: float = 0.999,  # retained for API compat; unused
) -> dict[str, Any]:
    """Aggregate per-episode scores into round-level stats.

    Returns only mean_score / scores / n / n_scored. The `success_rate`
    field (proportion of episodes with score >= success_threshold) was
    removed: per-game success has no shared meaning across games with
    different score scales (e.g. 0-1 vs 9-14), and the reflector picked
    it up as misleading signal. The success_threshold parameter is kept
    for backwards compatibility with callers but no longer used.
    """
    _ = success_threshold  # unused; kept for kwarg compat
    scores = [
        float(ep["score"])
        for ep in episodes
        if isinstance(ep.get("score"), (int, float))
    ]
    return {
        "n": len(episodes),
        "n_scored": len(scores),
        "mean_score": round(sum(scores) / len(scores), 6) if scores else None,
        "scores": scores,
    }


def compute_curve_metrics(points: list[dict[str, Any]]) -> dict[str, Any]:
    scored_points = [
        p for p in points if isinstance(p.get("mean_score"), (int, float))
    ]
    if not scored_points:
        return {}
    s0 = scored_points[0]["mean_score"]
    last = scored_points[-1]["mean_score"]
    gains = [p["mean_score"] - s0 for p in scored_points[1:]]
    regressions = 0
    for prev, cur in zip(scored_points, scored_points[1:]):
        if cur["mean_score"] < prev["mean_score"]:
            regressions += 1
    return {
        "final_gain": round(last - s0, 6),
        "auc_gain": round(sum(gains) / len(gains), 6) if gains else 0.0,
        "regression_rate": round(regressions / max(1, len(scored_points) - 1), 6),
        "s0": s0,
        "s_final": last,
    }
