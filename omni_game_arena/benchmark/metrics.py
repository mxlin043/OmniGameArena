"""Episode and cell metrics.

Generic signals live here (steps, wall-clock, action latency, FramePack
token telemetry). Game-specific signals (``finished``, ``done_reason``,
``score`` etc.) are delegated to ``GameSpec.extract_terminal_metrics`` /
``GameSpec.aggregate_episode_metrics`` and land under the ``game`` sub-dict.
"""

from __future__ import annotations

import statistics

from .games.base import GameSpec


def compute_episode_metrics(
    records: list[dict],
    terminal_info: dict | None,
    wall_time_s: float,
    game: GameSpec,
) -> dict:
    """Compute one episode's metrics (generic + per-game)."""
    steps = len(records)

    latencies: list[float] = []
    for i in range(1, len(records)):
        dt = (
            records[i].get("observation_timestamp", 0)
            - records[i - 1].get("observation_timestamp", 0)
        )
        if dt > 0:
            latencies.append(dt)

    # FramePack telemetry (absent on kernel="none")
    fp_entries = [
        r.get("info", {}).get("frame_pack")
        for r in records
        if r.get("info", {}).get("frame_pack")
    ]
    if fp_entries:
        ratios = [e.get("token_ratio", 1.0) for e in fp_entries]
        last = fp_entries[-1]
        frame_pack_summary = {
            "kernel": last.get("kernel"),
            "mean_token_ratio": round(sum(ratios) / len(ratios), 4),
            "max_n_input": max(e.get("n_input", 0) for e in fp_entries),
            "final_resolutions": last.get("resolutions", []),
            "n_steps_packed": len(fp_entries),
        }
    else:
        frame_pack_summary = None

    return {
        "steps": steps,
        "wall_time_s": round(wall_time_s, 3),
        "latency": _latency_stats(latencies),
        "frame_pack": frame_pack_summary,
        "game": game.extract_terminal_metrics(terminal_info or {}),
    }


def _latency_stats(latencies: list[float]) -> dict:
    if not latencies:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    sorted_lat = sorted(latencies)
    p95_idx = min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)
    return {
        "mean": round(statistics.mean(latencies), 4),
        "median": round(statistics.median(latencies), 4),
        "p95": round(sorted_lat[p95_idx], 4),
        "min": round(min(latencies), 4),
        "max": round(max(latencies), 4),
        "n": len(latencies),
    }


def aggregate_cell_metrics(
    episode_metrics: list[dict],
    game: GameSpec,
) -> dict:
    """Aggregate multiple episodes for one (agent, params) cell."""
    if not episode_metrics:
        return {"n_episodes": 0}

    steps = [m["steps"] for m in episode_metrics]
    walls = [m["wall_time_s"] for m in episode_metrics]
    lat_means = [
        m["latency"]["mean"]
        for m in episode_metrics
        if m["latency"]["n"] > 0
    ]

    fp_ratios = [
        m["frame_pack"]["mean_token_ratio"]
        for m in episode_metrics
        if m.get("frame_pack")
    ]
    fp_kernel = next(
        (m["frame_pack"].get("kernel") for m in episode_metrics if m.get("frame_pack")),
        None,
    )

    out = {
        "n_episodes": len(episode_metrics),
        "mean_steps": round(statistics.mean(steps), 2),
        "median_steps": statistics.median(steps),
        "mean_wall_time_s": round(statistics.mean(walls), 3),
        "mean_latency_s": round(statistics.mean(lat_means), 4) if lat_means else 0.0,
        "frame_pack_kernel": fp_kernel,
        "mean_token_ratio": round(statistics.mean(fp_ratios), 4) if fp_ratios else 1.0,
        "per_episode": episode_metrics,
    }
    out.update(game.aggregate_episode_metrics(episode_metrics))
    return out
