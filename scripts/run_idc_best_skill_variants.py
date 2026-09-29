"""Run held-out variants with skills from an explicitly selected IDC run.

Use --idc-run or --idc-root to select learned skills. Alternatively, supply
your own <game>/<model>.txt files with --skill-root; those runs save under
runs/variant_eval/<game>/<model>/<variant>/<best_skill|no_skill>/.
The no-skill arm does not require a skill file or an IDC run.
PvP evaluates P1 against the five skill-free IDC opponents, one game each by
default. Each opponent has a separate fixed_opponents/<model>/ result directory.

With --idc-root or --idc-run, this runner instead reads an
existing IDC run, finds the skill that produced the best measured base-map
round, injects that skill into the normal benchmark prompt, and saves the
held-out variant episodes under the source IDC run directory:

    runs/idc/<game>/<model>/<timestamp>/
      unseen_variants/<variant>/best_skill/<model>/...

With --flat-output, the benchmark episodes are written directly as:

      unseen_variants/<variant>/best_skill/<timestamp>/

Example:
    python scripts/run_idc_best_skill_variants.py --game last_stand --idc-run path/to/idc-run

By default, variant configs are loaded from:

    configs/vlm/cold_start/<solo|coop|pvp>/<game>/variant_lfm_{variant}.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MODELS = [
    "claude-opus-4-6",
    "claude-opus-4-7",
    "gpt-5.5",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "qwen3.5-397b-a17b",
]


@dataclass(frozen=True)
class GameRunSpec:
    name: str
    mode: str
    result_file: str
    variants: tuple[str, ...]


GAME_SPECS = {
    "midline_clash": GameRunSpec(
        name="midline_clash", mode="pvp", result_file="match_result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "cue_chase": GameRunSpec(name="cue_chase", mode="solo", result_file="result.json", variants=("Var1", "Var2", "Var3", "Var4")),
    "scene_escape": GameRunSpec(name="scene_escape", mode="solo", result_file="result.json", variants=("Var1", "Var2", "Var3", "Var4")),
    "sky_duel": GameRunSpec(
        name="sky_duel", mode="pvp", result_file="match_result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "crystal_guard": GameRunSpec(
        name="crystal_guard", mode="pvp", result_file="match_result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "monster_shoot": GameRunSpec(
        name="monster_shoot", mode="solo", result_file="result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "obstacle_run_3d": GameRunSpec(
        name="obstacle_run_3d",
        mode="solo",
        result_file="result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "handoff_run": GameRunSpec(
        name="handoff_run",
        mode="coop",
        result_file="match_result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "last_stand": GameRunSpec(
        name="last_stand",
        mode="solo",
        result_file="result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "shared_floor": GameRunSpec(
        name="shared_floor",
        mode="coop",
        result_file="match_result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "obstacle_run_2d": GameRunSpec(
        name="obstacle_run_2d",
        mode="solo",
        result_file="result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
    "solo_craft": GameRunSpec(
        name="solo_craft",
        mode="solo",
        result_file="result.json",
        variants=("Var1", "Var2", "Var3", "Var4"),
    ),
}


def resolve_variants(spec: GameRunSpec, variants: list[str] | None) -> list[str]:
    """Resolve Original or Var1–Var4 without historical variant mappings."""
    known = {name.lower(): name for name in (*spec.variants, "origin")}
    resolved = []
    for value in variants if variants is not None else spec.variants:
        name = known.get(value.lower())
        if name is None:
            message = (
                f"Unknown variant {value!r} for {spec.name}. Available: "
                f"{', '.join((*spec.variants, 'origin'))}."
            )
            raise ValueError(message)
        if name not in resolved:
            resolved.append(name)
    return resolved


def fixed_pvp_opponents(game: str) -> tuple[str, ...]:
    """Read the same five-opponent roster and order used by this game's IDC."""
    path = REPO_ROOT / "configs" / "vlm" / "idc" / f"{game}.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = tuple((cfg.get("idc") or {}).get("pvp_opponents") or ())
    if len(names) != 5 or any(not isinstance(name, str) or not name for name in names):
        raise ValueError(f"{path}: expected five fixed P2 model names")
    if len(set(name.lower() for name in names)) != 5:
        raise ValueError(f"{path}: fixed P2 models must be distinct")
    return names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate IDC-learned or user-supplied skills on held-out map variants."
    )
    parser.add_argument("--game", default="last_stand")
    parser.add_argument("--skill-root", default=None,
                        help="Explicit directory of your own <game>/<model>.txt files.")
    parser.add_argument("--output-root", default=None,
                        help="Provided-skill result root; defaults to runs/variant_eval.")
    parser.add_argument("--idc-root", default=None)
    parser.add_argument(
        "--idc-run",
        default=None,
        help=(
            "Exact IDC run directory to evaluate. When set, this runner "
            "does not auto-pick the latest run under --idc-root."
        ),
    )
    parser.add_argument(
        "--config-pattern",
        default=None,
        help=(
            "Variant config pattern. Defaults to "
            "configs/vlm/cold_start/<solo|coop|pvp>/<game>/"
            "variant_lfm_{variant}.yaml based on --game."
        ),
    )
    parser.add_argument("--models", nargs="+", default=None)
    parser.add_argument(
        "--variants", nargs="+", default=None,
        help="Defaults to Var1 Var2 Var3 Var4 for every game. Case-insensitive; origin is optional.",
    )
    parser.add_argument("--episodes", type=int, default=None,
                        help="Solo/coop successful episodes per variant (default 5).")
    parser.add_argument("--pvp-episodes-per-opponent", type=int, default=None,
                        help="PvP successful games per fixed opponent per variant (default 1; five total).")
    parser.add_argument(
        "--skill-round",
        type=int,
        default=None,
        help=(
            "Use idc_run/round_NN/skill_out.md instead of auto-selecting the "
            "highest measured IDC point. Example: --skill-round 5 uses "
            "round_05/skill_out.md."
        ),
    )
    parser.add_argument("--host", default=os.environ.get("IP", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "12345")))
    parser.add_argument("--port-p2", type=int, default=None, help="Default: --port + 1.")
    parser.add_argument("--output-subdir", default="unseen_variants")
    parser.add_argument("--arm-name", default="best_skill")
    parser.add_argument(
        "--no-skill",
        action="store_true",
        help=(
            "Run the matched no-skill arm. No skill is loaded, and "
            "--arm-name defaults to no_skill when left unchanged."
        ),
    )
    parser.add_argument(
        "--flat-output",
        action="store_true",
        help=(
            "Ask run_benchmark.py to write each episode directly under the "
            "variant arm dir, e.g. <variant>/best_skill/<timestamp>/."
        ),
    )
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--no-api-debug", action="store_true")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument(
        "--extra-run-arg",
        action="append",
        default=[],
        help="Extra argument passed through to scripts/run_benchmark.py.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sum(bool(source) for source in (args.skill_root, args.idc_root, args.idc_run)) > 1:
        print("[error] Choose one skill source: --skill-root, --idc-root, or --idc-run", flush=True)
        return 2
    if not args.no_skill and not any((args.skill_root, args.idc_root, args.idc_run)):
        print("[error] Select learned skills with --idc-run or --idc-root, or supply your own --skill-root", flush=True)
        return 2
    provided_skills = not (args.idc_root or args.idc_run)
    if provided_skills and args.skill_round is not None:
        print("[error] --skill-round requires an explicit IDC source", flush=True)
        return 2
    if not provided_skills and args.output_root:
        print("[error] --output-root is for provided skills; IDC uses --output-subdir", flush=True)
        return 2
    if args.episodes is not None and args.episodes < 1:
        print("[error] --episodes must be positive", flush=True)
        return 2
    if args.no_skill and args.skill_round is not None:
        print("[error] --no-skill cannot be combined with --skill-round", flush=True)
        return 2
    if args.no_skill and args.arm_name == "best_skill":
        args.arm_name = "no_skill"
    if not args.no_skill and args.arm_name == "no_skill":
        print(
            "[error] --arm-name no_skill requires --no-skill; refusing to "
            "label a skill-injected run as no-skill",
            flush=True,
        )
        return 2
    spec = GAME_SPECS.get(args.game)
    if spec is None:
        print(
            f"Unsupported game for best-skill variants: {args.game}. "
            f"Known: {', '.join(sorted(GAME_SPECS))}",
            flush=True,
        )
        return 2
    if spec.mode == "pvp" and args.episodes is not None:
        print("[error] PvP uses --pvp-episodes-per-opponent, not --episodes", flush=True)
        return 2
    if spec.mode != "pvp" and args.pvp_episodes_per_opponent is not None:
        print("[error] --pvp-episodes-per-opponent is only for PvP", flush=True)
        return 2
    target_episodes = args.pvp_episodes_per_opponent if spec.mode == "pvp" else args.episodes
    target_episodes = target_episodes if target_episodes is not None else (1 if spec.mode == "pvp" else 5)
    if target_episodes < 1:
        print("[error] --pvp-episodes-per-opponent must be positive", flush=True)
        return 2
    try:
        opponents = fixed_pvp_opponents(spec.name) if spec.mode == "pvp" else (None,)
    except (ValueError, OSError) as exc:
        print(f"[error] {exc}", flush=True)
        return 2
    config_pattern = args.config_pattern or default_config_pattern(spec)
    try:
        args.variants = resolve_variants(spec, args.variants)
    except ValueError as exc:
        print(f"[error] {exc}", flush=True)
        return 2
    if args.port_p2 is None:
        args.port_p2 = args.port + 1
    if not 1 <= args.port <= 65534 or not 1 <= args.port_p2 <= 65535:
        print("[error] Invalid RemoteInput port pair", flush=True)
        return 2
    if spec.mode in ("coop", "pvp") and args.port_p2 == args.port:
        print("[error] Player 1 and player 2 must use different ports", flush=True)
        return 2
    print(f"[variants] {', '.join(args.variants)}", flush=True)
    if spec.mode == "pvp":
        print(f"[pvp] P1 evaluated; skill-free P2 roster: {', '.join(opponents)}", flush=True)
        print(f"[pvp] {target_episodes} per opponent; {5 * target_episodes} games per variant", flush=True)
    failures: list[str] = []
    try:
        run_specs = _resolve_run_specs(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", flush=True)
        return 1

    for model, fixed_idc_run in run_specs:
        try:
            if provided_skills:
                idc_run = None
                skill = None if args.no_skill else find_provided_skill(
                    args.skill_root, args.game, model)
                model_output = (REPO_ROOT / (args.output_root or "runs/variant_eval") / args.game / model).resolve()
            else:
                idc_run = fixed_idc_run or find_latest_idc_run(args.idc_root, args.game, model)
                model_output = idc_run / args.output_subdir
                if args.no_skill:
                    skill = None
                elif args.skill_round is None:
                    skill = find_best_measured_skill(idc_run)
                else:
                    skill = find_skill_by_round(idc_run, args.skill_round)
        except Exception as exc:  # noqa: BLE001
            msg = f"{args.game}/{model}: {exc}"
            if args.allow_missing:
                print(f"[skip] {msg}", flush=True)
                continue
            failures.append(msg)
            print(f"[error] {msg}", flush=True)
            continue

        manifest_rows = []
        source_line = f"idc_run    : {idc_run}" if idc_run else f"skill_root : {args.skill_root or 'none'}"
        if skill is None:
            print(
                f"\n===== {args.game} / {model} =====\n"
                f"{source_line}\n"
                "selection  : no_skill\n"
                "skill_path : none",
                flush=True,
            )
        else:
            print(
                f"\n===== {args.game} / {model} =====\n"
                f"{source_line}\n"
                f"selection  : {skill.selection}\n"
                f"best_round : {skill.best_round}\n"
                f"best_score : {format_score(skill.best_score)}\n"
                f"skill_path : {skill.path}",
                flush=True,
            )

        for variant in args.variants:
            config_path = Path(
                config_pattern.format(game=args.game, variant=variant)
            )
            if not config_path.is_absolute():
                config_path = REPO_ROOT / config_path
            if not config_path.exists():
                failures.append(f"Missing variant config: {config_path}")
                print(f"[error] Missing variant config: {config_path}", flush=True)
                continue

            for opponent in opponents:
                output_root = model_output / variant / args.arm_name
                if opponent is not None:
                    # Isolate the new protocol from all previous self-play results,
                    # including the matchup where P1 and P2 have the same model.
                    output_root = output_root / "fixed_opponents" / opponent
                try:
                    row = run_until_complete(
                        spec=spec,
                        config_path=config_path,
                        output_root=output_root,
                        model=model,
                        skill_path=skill.path if skill is not None else None,
                        target_episodes=target_episodes,
                        host=args.host,
                        port=args.port,
                        port_p2=args.port_p2,
                        dry_run=args.dry_run,
                        live=not args.no_live,
                        log=not args.no_log,
                        api_debug=not args.no_api_debug,
                        video=not args.no_video,
                        flat_output=args.flat_output,
                        extra_args=args.extra_run_arg,
                        opponent_model=opponent,
                    )
                except (RuntimeError, subprocess.CalledProcessError) as exc:
                    failures.append(f"{args.game}/{model}/{variant}/{opponent or 'solo-coop'}: {exc}")
                    print(f"[error] {failures[-1]}", flush=True)
                    continue
                manifest_rows.append(
                    {
                        "variant": variant,
                        "opponent_model": opponent,
                        "config": str(config_path),
                        "output_root": str(output_root),
                        "cell_dir": str(row["cell_dir"]),
                        "flat_output": args.flat_output,
                        "existing_ok": row["existing_ok"],
                        "target_episodes": target_episodes,
                    }
                )

        if manifest_rows and not args.dry_run:
            manifest_name = f"{args.arm_name}_fixed_opponents_manifest.json" if spec.mode == "pvp" else f"{args.arm_name}_manifest.json"
            write_manifest(
                model_output / manifest_name,
                {
                    "game": args.game,
                    "model": model,
                    "idc_run": str(idc_run) if idc_run else None,
                    "skill_source": "provided_file" if provided_skills else "idc",
                    "selection": skill.selection if skill is not None else "no_skill",
                    "best_round": skill.best_round if skill is not None else None,
                    "best_score": skill.best_score if skill is not None else None,
                    "skill_path": str(skill.path) if skill is not None else None,
                    "skill_sha256": hashlib.sha256(skill.path.read_bytes()).hexdigest() if skill is not None else None,
                    "pvp_protocol": {
                        "evaluated_player": "player_1",
                        "opponent_player": "player_2",
                        "opponents": list(opponents),
                        "opponent_skill": "",
                        "episodes_per_opponent": target_episodes,
                        "episodes_per_variant": target_episodes * len(opponents),
                        "metric": "score",
                    } if spec.mode == "pvp" else None,
                    "variants": manifest_rows,
                },
            )

    if failures:
        print("\nFailures:", flush=True)
        for item in failures:
            print(f"  - {item}", flush=True)
        return 1
    return 0


def _resolve_run_specs(args: argparse.Namespace) -> list[tuple[str, Path | None]]:
    if not args.idc_run:
        return [(model, None) for model in (args.models or DEFAULT_MODELS)]

    idc_run = resolve_idc_run(args.idc_run)
    saved_path = idc_run / "idc_config.json"
    saved = load_json(saved_path) if saved_path.exists() else {}
    if saved.get("game") and saved["game"] != args.game:
        raise ValueError(f"IDC run is for {saved['game']}, not {args.game}")
    if args.models:
        if len(args.models) != 1:
            raise ValueError("--idc-run targets one run; pass at most one model")
        model = args.models[0]
        saved_model = (saved.get("agent") or {}).get("model") or saved.get("model")
        if saved_model and model != saved_model:
            raise ValueError(f"IDC run is for model {saved_model}, not {model}")
    else:
        model = infer_model_from_idc_run(idc_run)
    return [(model, idc_run)]


def default_config_pattern(spec: GameRunSpec) -> str:
    group = spec.mode if spec.mode in ("coop", "pvp") else "solo"
    return f"configs/vlm/cold_start/{group}/{{game}}/variant_lfm_{{variant}}.yaml"


class BestSkill:
    def __init__(
        self,
        *,
        path: Path,
        best_round: int | None,
        best_score: float | None,
        selection: str,
    ) -> None:
        self.path = path
        self.best_round = best_round
        self.best_score = best_score
        self.selection = selection


def find_provided_skill(skill_root: str | Path, game: str, model: str) -> BestSkill:
    path = (REPO_ROOT / skill_root / game / f"{model}.txt").resolve()
    if not path.is_file():
        raise FileNotFoundError(f"missing provided skill: {path}")
    if not path.read_text(encoding="utf-8").strip():
        raise ValueError(f"provided skill file is empty: {path}")
    return BestSkill(path=path, best_round=None, best_score=None, selection="provided_skill_file")


def resolve_idc_run(path: str | Path) -> Path:
    idc_run = Path(path)
    if not idc_run.is_absolute():
        idc_run = REPO_ROOT / idc_run
    idc_run = idc_run.resolve()
    if not idc_run.is_dir():
        raise FileNotFoundError(f"missing IDC run dir: {idc_run}")
    if not (idc_run / "idc_curve.json").exists():
        raise FileNotFoundError(f"missing idc_curve.json under IDC run: {idc_run}")
    return idc_run


def infer_model_from_idc_run(idc_run: Path) -> str:
    cfg_path = idc_run / "idc_config.json"
    if cfg_path.exists():
        try:
            cfg = load_json(cfg_path)
            agent = cfg.get("agent") or {}
            model = agent.get("model") or cfg.get("model")
            if model:
                return str(model)
        except Exception:  # noqa: BLE001
            pass
    return idc_run.parent.name


def find_latest_idc_run(idc_root: str | Path, game: str, model: str) -> Path:
    model_dir = REPO_ROOT / idc_root / game / model
    if not model_dir.exists():
        raise FileNotFoundError(f"missing IDC model dir: {model_dir}")

    candidates = [
        p for p in model_dir.iterdir()
        if p.is_dir() and (p / "idc_curve.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"no IDC run with idc_curve.json under {model_dir}")

    complete = [p for p in candidates if is_completed_idc_run(p)]
    if not complete:
        raise FileNotFoundError(f"no completed IDC run under {model_dir}")
    return sorted(complete, key=lambda p: p.name)[-1]


def is_completed_idc_run(path: Path) -> bool:
    """A run is complete once its state says so or its last round is scored."""
    state_path = path / "idc_state.json"
    if state_path.exists() and load_json(state_path).get("status") == "completed":
        return True
    cfg_path = path / "idc_config.json"
    cfg = load_json(cfg_path) if cfg_path.exists() else {}
    rounds = (cfg.get("idc") or {}).get("rounds", cfg.get("rounds", 10))
    return (path / f"round_{int(rounds):02d}" / "round_result.json").exists()


def find_best_measured_skill(idc_run: Path) -> BestSkill:
    curve = load_json(idc_run / "idc_curve.json")
    points = [
        p for p in curve.get("points", [])
        if isinstance(p.get("mean_score"), (int, float))
    ]
    if not points:
        raise ValueError(f"idc_curve has no scored points: {idc_run}")

    # A learned round tied with R0 is still a measured best skill.
    best = max(points, key=lambda p: (float(p["mean_score"]), int(p.get("round_idx", 0)) > 0))
    best_round = int(best.get("round_idx", 0))
    best_score = float(best["mean_score"])
    if best_round <= 0:
        raise ValueError(
            "best measured point is round_00 no-skill baseline; no learned "
            "best skill exists"
        )

    # Round r measures the skill emitted after round r-1.
    skill_path = idc_run / f"round_{best_round - 1:02d}" / "skill_out.md"
    if not skill_path.exists():
        raise FileNotFoundError(
            f"missing skill_out for best round {best_round}: {skill_path}"
        )
    if not skill_path.read_text(encoding="utf-8").strip():
        raise ValueError(f"best skill file is empty: {skill_path}")
    return BestSkill(
        path=skill_path,
        best_round=best_round,
        best_score=best_score,
        selection="auto_best_score",
    )


def find_skill_by_round(idc_run: Path, skill_round: int) -> BestSkill:
    if skill_round < 0:
        raise ValueError(f"--skill-round must be non-negative, got {skill_round}")

    skill_path = idc_run / f"round_{skill_round:02d}" / "skill_out.md"
    if not skill_path.exists():
        raise FileNotFoundError(
            f"missing forced skill_out for round_{skill_round:02d}: {skill_path}"
        )
    if not skill_path.read_text(encoding="utf-8").strip():
        raise ValueError(f"forced skill file is empty: {skill_path}")

    measured_round = skill_round + 1
    measured_score = find_measured_score(idc_run, measured_round)
    return BestSkill(
        path=skill_path,
        best_round=measured_round,
        best_score=measured_score,
        selection=f"forced_skill_round_{skill_round:02d}",
    )


def find_measured_score(idc_run: Path, measured_round: int) -> float | None:
    try:
        curve = load_json(idc_run / "idc_curve.json")
    except Exception:  # noqa: BLE001
        return None
    for point in curve.get("points", []):
        if int(point.get("round_idx", -1)) != measured_round:
            continue
        score = point.get("mean_score")
        if isinstance(score, (int, float)):
            return float(score)
    return None


def format_score(score: float | None) -> str:
    if score is None:
        return "n/a"
    return f"{score:.6g}"


def run_until_complete(
    *,
    spec: GameRunSpec,
    config_path: Path,
    output_root: Path,
    model: str,
    skill_path: Path | None,
    target_episodes: int,
    host: str,
    port: int,
    port_p2: int,
    dry_run: bool,
    live: bool,
    log: bool,
    api_debug: bool,
    video: bool,
    flat_output: bool,
    extra_args: list[str],
    opponent_model: str | None = None,
    stop_file: Path | None = None,
) -> dict[str, Any]:
    def check_stop() -> None:
        if stop_file is not None and stop_file.exists():
            raise RuntimeError(f"Batch stopped by {stop_file}; leave the marker in place until explicitly resumed")

    check_stop()
    cell_dir = resolve_cell_dir(
        spec=spec,
        config_path=config_path,
        output_root=output_root,
        model=model,
        skill_path=skill_path,
        host=host,
        port=port,
        port_p2=port_p2,
        flat_output=flat_output,
        extra_args=extra_args,
        opponent_model=opponent_model,
    )
    ok_count = count_ok_episodes(cell_dir, spec.result_file)
    matchup = f"{model} vs {opponent_model}" if opponent_model else model
    print(
        f"\n[variant] {config_path.stem} / {matchup}\n"
        f"cell_dir: {cell_dir}\n"
        f"ok episodes: {ok_count}/{target_episodes}",
        flush=True,
    )

    while ok_count < target_episodes:
        check_stop()
        missing = target_episodes - ok_count
        print(f"[run] need {missing} more episode(s)", flush=True)
        cmd = benchmark_cmd(
            spec=spec,
            config_path=config_path,
            output_root=output_root,
            model=model,
            skill_path=skill_path,
            host=host,
            port=port,
            port_p2=port_p2,
            flat_output=flat_output,
            extra_args=extra_args,
            opponent_model=opponent_model,
        )
        if live:
            cmd.append("--live")
        if log:
            cmd.append("--log")
        if api_debug:
            cmd.append("--api-debug")
        if video:
            cmd.extend(["--record-video", "--video-with-thinking"])
        cmd.extend(["--episodes", "1"])

        print("[cmd] " + " ".join(str(x) for x in cmd), flush=True)
        if dry_run:
            break
        # Viewer metadata travels only in the child environment, never in the
        # model prompt/config. Every new process reads the existing cell history.
        child_env = dict(os.environ)
        if live:
            child_env["OMNI_ARENA_VARIANT_PROGRESS"] = json.dumps({
                "game": spec.name,
                "mode": spec.mode,
                "variant": config_path.stem.removeprefix("variant_lfm_"),
                "model": model,
                "opponent_model": opponent_model,
                "with_skill": skill_path is not None,
                "target_episodes": target_episodes,
                "cell_dir": str(cell_dir),
                "result_file": spec.result_file,
            })
        else:
            child_env.pop("OMNI_ARENA_VARIANT_PROGRESS", None)
        check_stop()
        subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=child_env)
        next_count = count_ok_episodes(cell_dir, spec.result_file)
        if next_count <= ok_count:
            raise RuntimeError(
                f"Benchmark produced no new successful episode in {cell_dir}; "
                "stopping instead of retrying skipped/interrupted runs indefinitely."
            )
        ok_count = next_count

    return {"cell_dir": cell_dir, "existing_ok": ok_count}


def resolve_cell_dir(
    *,
    spec: GameRunSpec,
    config_path: Path,
    output_root: Path,
    model: str,
    skill_path: Path | None,
    host: str,
    port: int,
    port_p2: int,
    flat_output: bool,
    extra_args: list[str],
    opponent_model: str | None = None,
) -> Path:
    cmd = benchmark_cmd(
        spec=spec,
        config_path=config_path,
        output_root=output_root,
        model=model,
        skill_path=skill_path,
        host=host,
        port=port,
        port_p2=port_p2,
        flat_output=flat_output,
        extra_args=extra_args,
        opponent_model=opponent_model,
    )
    cmd.extend(["--episodes", "1", "--dry-run"])
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        match = re.match(r"\s+\[\s*\d+\]\s+(.+)$", line)
        if not match:
            continue
        raw = match.group(1).strip()
        raw = raw.split(" | ", 1)[0]
        raw = raw.replace("\\<timestamp>", "").replace("/<timestamp>", "")
        return (REPO_ROOT / raw).resolve()
    raise RuntimeError(
        "Could not parse dry-run output from run_benchmark.py:\n" + proc.stdout
    )


def benchmark_cmd(
    *,
    spec: GameRunSpec,
    config_path: Path,
    output_root: Path,
    model: str,
    skill_path: Path | None,
    host: str,
    port: int,
    port_p2: int,
    flat_output: bool,
    extra_args: list[str],
    opponent_model: str | None = None,
) -> list[str]:
    if spec.mode == "pvp" and opponent_model not in fixed_pvp_opponents(spec.name):
        raise ValueError("PvP requires an explicit opponent from the fixed IDC roster")
    if spec.mode != "pvp" and opponent_model is not None:
        raise ValueError("Only PvP accepts opponent_model")
    cmd = [
        sys.executable,
        "scripts/run_benchmark.py",
        "--config",
        str(config_path),
        "--output-root",
        str(output_root),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if spec.mode == "pvp":
        # Sampling/connection overrides remain available, but the fixed seats,
        # selected P1 skill, and skill-free P2 cannot be overridden accidentally.
        cmd.extend(extra_args)
    if skill_path is None:
        cmd.append("--no-prompt-skills")
    if spec.mode == "solo":
        cmd.extend(["--include", model])
        if skill_path is not None:
            cmd.extend(["--prompt-skill", str(skill_path)])
    else:
        players = [
            {
                "id": 1,
                "host": host,
                "port": port,
                "model": model,
                "prompt_skills": [str(skill_path)] if skill_path is not None else [],
            },
            {
                "id": 2,
                "host": host,
                "port": port_p2,
                "model": opponent_model if spec.mode == "pvp" else model,
                "prompt_skills": [str(skill_path)] if skill_path is not None and spec.mode != "pvp" else [],
            },
        ]
        if spec.mode == "pvp":
            cmd.extend(["--set", "prompt_skills=[]", "--set", "players_defaults.prompt_skills=[]"])
        cmd.extend(
            [
                "--set",
                "players=" + json.dumps(players, separators=(",", ":")),
            ]
        )
    cmd.extend(["--set", "params.with_skill_prompt=" + ("true" if skill_path is not None else "false")])
    if flat_output:
        cmd.append("--flat-output")
    if spec.mode != "pvp":
        cmd.extend(extra_args)
    return cmd


def count_ok_episodes(cell_dir: Path, result_file: str) -> int:
    if not cell_dir.exists():
        return 0
    count = 0
    for result_path in cell_dir.rglob(result_file):
        if not result_path.exists():
            continue
        try:
            result = load_json(result_path)
        except Exception:  # noqa: BLE001
            continue
        if result.get("status") == "ok":
            count += 1
    return count


def load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    tmp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
