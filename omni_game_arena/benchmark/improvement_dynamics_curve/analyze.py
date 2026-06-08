"""Analyze IDC run outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_curve(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir) / "idc_curve.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing idc_curve.json: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def print_curve(run_dir: str | Path) -> None:
    curve = load_curve(run_dir)
    print(f"\n{run_dir}")
    print("Round | mean_score | n")
    print("------+------------+---")
    for p in curve.get("points", []):
        print(
            f"{p.get('round_idx', 0):>5} | "
            f"{_fmt(p.get('mean_score')):>10} | "
            f"{p.get('n')}"
        )


def plot_curves(run_dirs: list[str | Path], out_path: str | Path) -> None:
    try:
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        print("matplotlib not installed; skipping plot")
        return

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for run_dir in run_dirs:
        curve = load_curve(run_dir)
        points = curve.get("points", [])
        xs = [p.get("round_idx") for p in points]
        ys = [p.get("mean_score") for p in points]
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if not pairs:
            continue
        label = _label_for_run(run_dir)
        ax.plot([p[0] for p in pairs], [p[1] for p in pairs], marker="o", label=label)

    ax.set_xlabel("IDC round")
    ax.set_ylabel("Mean score")
    ax.set_title("Improvement Dynamics Curve")
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"[plot saved] {out_path}")


def _label_for_run(run_dir: str | Path) -> str:
    run_dir = Path(run_dir)
    cfg_path = run_dir / "idc_config.json"
    if cfg_path.exists():
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        model = (cfg.get("agent") or {}).get("model")
        game = cfg.get("game")
        if model and game:
            return f"{game}/{model}"
    return run_dir.name


def _fmt(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return "n/a"
