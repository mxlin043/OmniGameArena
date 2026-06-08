"""Run held-out variants with each model's best measured IDC skill.

This runner is intentionally outside the IDC reflection loop. It reads an
existing IDC run, finds the skill that produced the best measured base-map
round, injects that skill into the normal benchmark prompt, and saves the
held-out variant episodes under the source IDC run directory:

    runs/idc/<game>/<model>/<timestamp>/
      unseen_variants/<variant>/best_skill/<model>/...

With --flat-output, the benchmark episodes are written directly as:

      unseen_variants/<variant>/best_skill/<timestamp>/

Example:
    python scripts/run_idc_best_skill_variants.py --game last_stand

By default, variant configs are loaded from:

    configs/vlm/cold_start/<solo|coop>/<game>/variant_pdq_{variant}.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = [
    "claude-opus-4-6",
    "claude-opus-4-7",
    "gpt-5.5",
    "gemini-3.1-pro-preview",
]
DEFAULT_VARIANTS = ["var1", "var2", "var3"]


@dataclass(frozen=True)
class GameRunSpec:
    name: str
    mode: str
    result_file: str


GAME_SPECS = {
    "last_stand": GameRunSpec(
        name="last_stand",
        mode="solo",
        result_file="result.json",
    ),
    "shared_floor": GameRunSpec(
        name="shared_floor",
        mode="coop",
        result_file="match_result.json",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate IDC best skills on held-out map variants."
    )
    parser.add_argument("--game", default="last_stand")
    parser.add_argument("--idc-root", default="runs/idc")
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
            "configs/vlm/cold_start/<solo|coop>/<game>/"
            "variant_pdq_{variant}.yaml based on --game."
        ),
    )
    parser.add_argument("--models", nargs="+", default=None)
    parser.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS)
    parser.add_argument("--episodes", type=int, default=5)
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
    parser.add_argument("--port-p2", type=int, default=12346)
    parser.add_argument("--output-subdir", default="unseen_variants")
    parser.add_argument("--arm-name", default="best_skill")
    parser.add_argument(
        "--flat-output",
        action="store_true",
        help=(
            "Ask run_benchmark.py to write each episode directly under the "
            "variant arm dir, e.g. unseen_variants/var1/best_skill/<timestamp>/."
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
    spec = GAME_SPECS.get(args.game)
    if spec is None:
        print(
            f"Unsupported game for best-skill variants: {args.game}. "
            f"Known: {', '.join(sorted(GAME_SPECS))}",
            flush=True,
        )
        return 2
    config_pattern = args.config_pattern or default_config_pattern(spec)
    failures: list[str] = []
    try:
        run_specs = _resolve_run_specs(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", flush=True)
        return 1

    for model, fixed_idc_run in run_specs:
        try:
            idc_run = fixed_idc_run or find_latest_idc_run(
                args.idc_root,
                args.game,
                model,
            )
            if args.skill_round is None:
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
        print(
            f"\n===== {args.game} / {model} =====\n"
            f"idc_run    : {idc_run}\n"
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

            output_root = idc_run / args.output_subdir / variant / args.arm_name
            row = run_until_complete(
                spec=spec,
                config_path=config_path,
                output_root=output_root,
                model=model,
                skill_path=skill.path,
                target_episodes=args.episodes,
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
            )
            manifest_rows.append(
                {
                    "variant": variant,
                    "config": str(config_path),
                    "output_root": str(output_root),
                    "cell_dir": str(row["cell_dir"]),
                    "flat_output": args.flat_output,
                    "existing_ok": row["existing_ok"],
                    "target_episodes": args.episodes,
                }
            )

        if manifest_rows and not args.dry_run:
            write_manifest(
                idc_run / args.output_subdir / f"{args.arm_name}_manifest.json",
                {
                    "game": args.game,
                    "model": model,
                    "idc_run": str(idc_run),
                    "selection": skill.selection,
                    "best_round": skill.best_round,
                    "best_score": skill.best_score,
                    "skill_path": str(skill.path),
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
    if args.models:
        if len(args.models) != 1:
            raise ValueError("--idc-run targets one run; pass at most one model")
        model = args.models[0]
    else:
        model = infer_model_from_idc_run(idc_run)
    return [(model, idc_run)]


def default_config_pattern(spec: GameRunSpec) -> str:
    group = "coop" if spec.mode == "coop" else "solo"
    return f"configs/vlm/cold_start/{group}/{{game}}/variant_pdq_{{variant}}.yaml"


class BestSkill:
    def __init__(
        self,
        *,
        path: Path,
        best_round: int,
        best_score: float | None,
        selection: str,
    ) -> None:
        self.path = path
        self.best_round = best_round
        self.best_score = best_score
        self.selection = selection


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

    complete = [
        p for p in candidates
        if (p / "round_10" / "round_result.json").exists()
    ]
    return sorted(complete or candidates, key=lambda p: p.name)[-1]


def find_best_measured_skill(idc_run: Path) -> BestSkill:
    curve = load_json(idc_run / "idc_curve.json")
    points = [
        p for p in curve.get("points", [])
        if isinstance(p.get("mean_score"), (int, float))
    ]
    if not points:
        raise ValueError(f"idc_curve has no scored points: {idc_run}")

    best = max(points, key=lambda p: float(p["mean_score"]))
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
    skill_path: Path,
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
) -> dict[str, Any]:
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
    )
    ok_count = count_ok_episodes(cell_dir, spec.result_file)
    print(
        f"\n[variant] {config_path.stem} / {model}\n"
        f"cell_dir: {cell_dir}\n"
        f"ok episodes: {ok_count}/{target_episodes}",
        flush=True,
    )

    while ok_count < target_episodes:
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
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)
        ok_count = count_ok_episodes(cell_dir, spec.result_file)

    return {"cell_dir": cell_dir, "existing_ok": ok_count}


def resolve_cell_dir(
    *,
    spec: GameRunSpec,
    config_path: Path,
    output_root: Path,
    model: str,
    skill_path: Path,
    host: str,
    port: int,
    port_p2: int,
    flat_output: bool,
    extra_args: list[str],
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
    )
    cmd.extend(["--episodes", "1", "--dry-run"])
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
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
    skill_path: Path,
    host: str,
    port: int,
    port_p2: int,
    flat_output: bool,
    extra_args: list[str],
) -> list[str]:
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
    if spec.mode == "solo":
        cmd.extend(["--include", model, "--prompt-skill", str(skill_path)])
    else:
        players = [
            {
                "id": 1,
                "host": host,
                "port": port,
                "model": model,
                "prompt_skills": [str(skill_path)],
            },
            {
                "id": 2,
                "host": host,
                "port": port_p2,
                "model": model,
                "prompt_skills": [str(skill_path)],
            },
        ]
        cmd.extend(
            [
                "--set",
                "players=" + json.dumps(players, separators=(",", ":")),
            ]
        )
    if flat_output:
        cmd.append("--flat-output")
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
