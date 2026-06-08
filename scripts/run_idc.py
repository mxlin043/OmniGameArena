"""Run an Improvement Dynamics Curve (IDC) experiment.

Fresh run:
    python scripts/run_idc.py --config configs/vlm/idc/last_stand.yaml --model claude-opus-4-6

Resume:
    python scripts/run_idc.py --resume runs/idc/last_stand/claude-opus-4-6/20260523_180000
"""

from __future__ import annotations

import argparse
import copy
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omni_game_arena.benchmark.improvement_dynamics_curve import run_idc
from omni_game_arena.benchmark.improvement_dynamics_curve.config import (
    config_from_dict,
    load_idc_config,
)
from omni_game_arena.benchmark.improvement_dynamics_curve.io import load_json


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run IDC evaluation")
    p.add_argument("--config", default=None, help="IDC YAML config")
    p.add_argument("--resume", default=None, help="Existing IDC run directory")
    p.add_argument("--model", default=None, help="Override player model")
    p.add_argument("--reflector-model", default=None, help="Override reflector model")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--rounds", type=int, default=None)
    p.add_argument("--episodes-per-round", type=int, default=None)
    p.add_argument("--output-root", default=None)
    p.add_argument("--pdq-root", default=None)
    p.add_argument(
        "--live",
        action="store_true",
        help="Open LiveViewer with gameplay, step log, and IDC progress panel",
    )
    p.add_argument("--log-vlm", action="store_true")
    p.add_argument(
        "--api-debug",
        action="store_true",
        help=(
            "Dump player API calls under each episode api_debug/ and "
            "reflection API calls under each round api_debug_reflector/."
        ),
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.resume:
        cfg_path = os.path.join(args.resume, "idc_config.json")
        cfg_dict = load_json(cfg_path)
        cfg_dict.setdefault("output", {})["run_dir"] = args.resume
        cfg = config_from_dict(_json_config_to_yaml_shape(cfg_dict))
    else:
        if not args.config:
            raise SystemExit("Either --config or --resume is required.")
        cfg = load_idc_config(args.config)

    if args.model:
        cfg.agent_profile.model = args.model
    if args.reflector_model is not None:
        cfg.reflector_model = args.reflector_model
    if args.host:
        cfg.env_spec.host = args.host
    if args.port is not None:
        cfg.env_spec.port = args.port
    if args.rounds is not None:
        cfg.rounds = args.rounds
    if args.episodes_per_round is not None:
        cfg.episodes_per_round = args.episodes_per_round
    if args.output_root:
        cfg.output_root = args.output_root
    if args.pdq_root:
        cfg.official_pdq_root = args.pdq_root
    if args.live:
        cfg.live = True
    if args.log_vlm:
        cfg.log_vlm = True
    if args.api_debug:
        cfg.api_debug = True

    result = run_idc(cfg)
    print(f"\n[idc done] {result['run_dir']}")
    for point in result["curve"].get("points", []):
        print(
            "  round {round_idx:02d}: mean_score={mean_score} "
            "n={n}".format(**point)
        )
    return 0


def _json_config_to_yaml_shape(cfg: dict) -> dict:
    """Convert saved idc_config.json back to config_from_dict shape."""
    out = copy.deepcopy(cfg)
    agent = out.get("agent") or {}
    out["model"] = agent.get("model")
    out["agent"] = {
        "kind": agent.get("kind", "vlm"),
        "method": agent.get("method", "lumine"),
        "extra": agent.get("extra") or {},
        "prompt_skills": agent.get("prompt_skills") or [],
    }
    # New saved configs use "params"; older ones used "ablation".
    out["params"] = out.pop("params", None) or out.pop("ablation", {})
    idc = out.get("idc") or {}
    output = out.get("output") or {}
    output["run_dir"] = idc.get("run_dir") or output.get("run_dir") or ""
    out["output"] = output
    return out


if __name__ == "__main__":
    raise SystemExit(main())
