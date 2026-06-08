"""Omni Game Arena benchmark entry point.

The scene is selected by the YAML's ``game:`` key; the ``extends`` chain
(list or string) is resolved before expansion, so small mode configs can
inherit shared game defaults such as ``vanilla.yaml``.

Quick start
-----------
    # Bash / zsh
    python scripts/run_benchmark.py \
        --config configs/vlm/cold_start/solo/obstacle_run_2d/vanilla_pdq.yaml \
        --host 127.0.0.1 --port 12345

    # PowerShell (backtick line continuation)
    python scripts/run_benchmark.py `
        --config configs/vlm/cold_start/solo/obstacle_run_2d/vanilla_pdq.yaml `
        --host 127.0.0.1 --port 12345

Output layout
-------------
    <output_root>/<game>/<agent_name>/<YYYYMMDD_HHMMSS>/
        result.json
        step_*.jpg + summary.json
        experiment.log  # only when --api-debug is enabled

With ``--flat-output``, episode directories are written directly under
``<output_root>/<YYYYMMDD_HHMMSS>/``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys

# Make `omni_game_arena` importable when the script is run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omni_game_arena.benchmark.config import (
    expand_experiments,
    expand_two_player_experiments,
    load_benchmark_config,
)
from omni_game_arena.benchmark.games import get_game, list_games
from omni_game_arena.benchmark.logging_utils import setup_root_logger, timestamp_slug
from omni_game_arena.benchmark.runner import run_benchmark
from omni_game_arena.benchmark.two_player import run_two_player_benchmark


def _override_env(cfg: dict, args: argparse.Namespace) -> dict:
    """Apply CLI overrides to the loaded config."""
    env = cfg.setdefault("env", {})
    if args.host is not None:
        env["host"] = args.host
    if args.port is not None:
        env["port"] = args.port
    if args.task is not None:
        env["task"] = args.task
    if args.max_steps is not None:
        env["max_steps"] = args.max_steps
    if args.no_map:
        env["map"] = ""

    if args.episodes is not None:
        cfg["episodes_per_cell"] = args.episodes
    if args.include:
        cfg["include_agents"] = args.include
    if args.exclude:
        cfg["exclude_agents"] = args.exclude
    return cfg


def _override_players(cfg: dict, models: list | None) -> dict:
    """Override per-player models in a two-player ``players:`` list, positionally.

    The i-th model in ``models`` replaces the model of the i-th ``players:``
    entry (config order = player 0, player 1, ...). Host / port / id are left
    untouched. No-op when ``models`` is empty or the config has no ``players:``
    (e.g. a solo config), so it is always safe to pass through.
    """
    if not models:
        return cfg
    players = cfg.get("players")
    if not isinstance(players, list) or not players:
        return cfg
    for i, model in enumerate(models):
        if i >= len(players):
            break
        entry = players[i]
        if isinstance(entry, str):
            players[i] = {"model": model}
        elif isinstance(entry, dict):
            if isinstance(entry.get("agent"), dict):
                entry["agent"]["model"] = model
            else:
                entry["model"] = model
    return cfg


def _override_prompt_skills(cfg: dict, args: argparse.Namespace) -> dict:
    """Apply prompt-skill CLI overrides."""
    if args.no_prompt_skills:
        cfg["prompt_skills"] = []
    if args.prompt_skill:
        existing = cfg.get("prompt_skills") or []
        if isinstance(existing, str):
            existing = [existing]
        cfg["prompt_skills"] = [*existing, *args.prompt_skill]
    return cfg


def _parse_override_value(raw: str):
    """Parse a CLI override value.

    Tries JSON first (covers int / float / bool / null / list / dict /
    quoted string), falls back to the raw string. So:
      16           -> int 16
      0.5          -> float 0.5
      true         -> True
      [4,8,16]     -> list (sweep values)
      "hello"      -> "hello"  (JSON-quoted)
      hello        -> "hello"  (bare string fallback)
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _apply_overrides(cfg: dict, overrides: list[str] | None) -> dict:
    """Apply ``--set KEY=VALUE`` overrides, where KEY is a dotted path.

    Examples:
        --set params.chunk_steps=16          # single-value override
        --set params.chunk_steps=[4,8,16]    # sweep values (list)
        --set env.max_steps=50
        --set episodes_per_cell=3
    """
    for spec in overrides or []:
        if "=" not in spec:
            raise SystemExit(
                f"Bad --set value {spec!r}. Expected KEY=VALUE, e.g. "
                f"params.chunk_steps=16"
            )
        key, raw_value = spec.split("=", 1)
        value = _parse_override_value(raw_value)

        parts = key.split(".")
        target = cfg
        for p in parts[:-1]:
            nxt = target.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                target[p] = nxt
            target = nxt
        target[parts[-1]] = value
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _suite_game_config(suite_cfg: dict, entry) -> dict:
    """Build a normal single-game config from a suite `games:` entry."""
    if isinstance(entry, str):
        game_name = entry
        entry_cfg = {}
    elif isinstance(entry, dict):
        entry_cfg = copy.deepcopy(entry)
        game_name = entry_cfg.pop("game", None) or entry_cfg.pop("name", None)
    else:
        raise ValueError(f"Suite game entries must be strings or dicts, got {entry!r}")

    if not game_name:
        raise ValueError(f"Suite game entry is missing `game:`/`name:`: {entry!r}")

    base = {
        key: copy.deepcopy(value)
        for key, value in suite_cfg.items()
        if key not in {"games", "game", "env_defaults"}
    }
    if suite_cfg.get("env_defaults"):
        base["env"] = _deep_merge(base.get("env") or {}, suite_cfg["env_defaults"])

    per_game_env = entry_cfg.pop("env", None)
    game_cfg = _deep_merge(base, entry_cfg)
    game_cfg["game"] = game_name
    if per_game_env:
        game_cfg["env"] = _deep_merge(game_cfg.get("env") or {}, per_game_env)
    return game_cfg


def _resolve_output_root(cfg: dict, args: argparse.Namespace, game) -> str:
    output_base = args.output_root or cfg.get("output_root") or "runs"
    output_root_is_final = bool(cfg.get("output_root_is_final", False))
    return (
        output_base
        if output_root_is_final
        or os.path.basename(os.path.normpath(output_base)) == game.name
        else os.path.join(output_base, game.name)
    )


def _print_solo_dry_run(
    experiments,
    output_root: str,
    args: argparse.Namespace,
    *,
    flat_output: bool | None = None,
) -> None:
    use_flat_output = args.flat_output if flat_output is None else flat_output
    for i, exp in enumerate(experiments, 1):
        if use_flat_output:
            path = os.path.join(output_root, "<timestamp>")
        else:
            path = os.path.join(
                output_root,
                exp.agent.model,
                "<timestamp>",
            )
        print(f"  [{i:3d}] {path}")


def _runtime_bool(cfg: dict, args: argparse.Namespace, name: str) -> bool:
    return bool(getattr(args, name, False)) or bool(cfg.get(name, False))


def _run_solo_suite(cfg: dict, args: argparse.Namespace) -> int:
    raw_games = cfg.get("games") or []
    if not raw_games:
        print("Suite config has an empty `games:` list.", file=sys.stderr)
        return 2

    suite_items: list[tuple[dict, object, list, str]] = []
    for entry in raw_games:
        try:
            game_cfg = _suite_game_config(cfg, entry)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        game_cfg = _override_env(game_cfg, args)
        game_cfg = _override_prompt_skills(game_cfg, args)
        game_cfg = _apply_overrides(game_cfg, args.overrides)

        game_name = game_cfg.get("game")
        try:
            game = get_game(game_name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if game.mode != "solo":
            print(
                f"Suite mode in run_benchmark.py currently supports solo games only; "
                f"{game.name!r} is {game.mode!r}.",
                file=sys.stderr,
            )
            return 2

        if game.map and not (game_cfg.get("env") or {}).get("map"):
            game_cfg.setdefault("env", {})["map"] = game.map

        try:
            experiments = expand_experiments(game_cfg, game.name, game.default_task)
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to expand suite game {game_name!r}: {exc}", file=sys.stderr)
            return 2
        if not experiments:
            print(
                f"No experiments expanded for suite game {game_name!r}.",
                file=sys.stderr,
            )
            return 2

        output_root = _resolve_output_root(game_cfg, args, game)
        suite_items.append((game_cfg, game, experiments, output_root))

    if args.dry_run:
        total = sum(len(item[2]) for item in suite_items)
        print(f"Would run solo suite: {len(suite_items)} game(s), {total} experiment(s):")
        for game_cfg, game, experiments, output_root in suite_items:
            print(f"\n[{game.name}]")
            _print_solo_dry_run(
                experiments,
                output_root,
                args,
                flat_output=_runtime_bool(game_cfg, args, "flat_output"),
            )
        print("\nExpanded config (first cell):")
        print(json.dumps(suite_items[0][2][0].to_dict(), indent=2, ensure_ascii=False))
        return 0

    total_errors = 0
    for game_cfg, game, experiments, output_root in suite_items:
        logger = setup_root_logger(output_root, game_name=game.name, verbose=args.verbose)
        logger.info("Loaded suite config: %s", args.config)
        logger.info("Game         : %s", game.name)
        logger.info("Output root  : %s", output_root)
        logger.info("Experiments  : %d", len(experiments))

        clock_mode = args.clock_mode or game_cfg.get("clock_mode") or "realtime"
        summary = run_benchmark(
            experiments,
            output_root,
            game,
            live=_runtime_bool(game_cfg, args, "live"),
            log_vlm=_runtime_bool(game_cfg, args, "log"),
            api_debug=_runtime_bool(game_cfg, args, "api_debug"),
            clock_mode=clock_mode,
            record_video=_runtime_bool(game_cfg, args, "record_video"),
            video_fps=int(game_cfg.get("video_fps", args.video_fps)),
            video_with_thinking=_runtime_bool(game_cfg, args, "video_with_thinking"),
            video_thinking_layout=game_cfg.get(
                "video_thinking_layout",
                args.video_thinking_layout,
            ),
            flat_output=_runtime_bool(game_cfg, args, "flat_output"),
        )

        logger.info(
            "Suite game summary: total=%d ok=%d skip=%d err=%d int=%d",
            summary["n_experiments"],
            summary["n_ok"],
            summary.get("n_skipped", 0),
            summary["n_error"],
            summary["n_interrupted"],
        )
        total_errors += summary["n_error"]
        if summary["n_interrupted"]:
            break

    return 0 if total_errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Omni Game Arena benchmark runner (game selected by YAML)",
        epilog=(
            "Registered games: "
            + ", ".join(list_games())
            + ". Bash/zsh line continuation uses '\\'; "
            "PowerShell line continuation uses the backtick '`'."
        ),
    )

    parser.add_argument("--config", type=str, required=True, help="Path to benchmark YAML config")
    parser.add_argument(
        "--output-root",
        dest="output_root",
        type=str,
        default=None,
        help=(
            "Override storage root dir. Highest priority; YAML may also set "
            "`output_root:`. The game name is appended automatically. "
            "Default: runs"
        ),
    )
    parser.add_argument("--host", type=str, default=None, help="Override env.host")
    parser.add_argument("--port", type=int, default=None, help="Override env.port")
    parser.add_argument("--task", type=str, default=None, help="Override env.task")
    parser.add_argument("--max-steps", type=int, default=None, help="Override env.max_steps")
    parser.add_argument("--episodes", type=int, default=None, help="Override episodes_per_cell")
    parser.add_argument("--include", nargs="+", default=None,
                        help="Only run these agent profiles (by name)")
    parser.add_argument("--exclude", nargs="+", default=None,
                        help="Skip these agent profiles (by name)")
    parser.add_argument(
        "--players", nargs="+", default=None, metavar="MODEL",
        help=(
            "Override the two-player pairing models positionally: the i-th "
            "MODEL replaces player i's model in the config's `players:` list "
            "(player 0, player 1, ...). PvP/Coop only; host / port / id stay "
            "as configured. Any model works, even one not listed in the YAML."
        ),
    )
    parser.add_argument("--no-map", action="store_true",
                        help="Skip map switching on reset (use whatever scene UE5 already has loaded)")
    parser.add_argument(
        "--set", nargs="+", default=None, dest="overrides", metavar="KEY=VALUE",
        help=(
            "Override any config field using a dotted path, e.g. "
            "`--set params.chunk_steps=16 params.obs_delay=0.5 env.max_steps=50`. "
            "Values are parsed as JSON when possible, so lists work too: "
            "`--set params.chunk_steps=[4,8,16]` for sweeping."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the expanded experiment list and exit (no UE5 needed)")
    parser.add_argument("--live", action="store_true",
                        help="Open a Tk window showing each observation in real time")
    parser.add_argument("--live-vlm-only", action="store_true",
                        help="With --live on PvP/Coop, show only frames sent to the VLM instead of realtime streaming")
    parser.add_argument("--live-fps", type=int, default=30,
                        help="Realtime live viewer refresh rate for PvP/Coop (default 30)")
    parser.add_argument(
        "--record-video",
        action="store_true",
        help="Save episode.mp4 videos for benchmark episodes (default: off)",
    )
    parser.add_argument(
        "--video-fps",
        type=int,
        default=30,
        help="Target FPS for --record-video (default 30)",
    )
    parser.add_argument(
        "--video-with-thinking",
        action="store_true",
        help="Add the model's visible reason/action text to --record-video output",
    )
    parser.add_argument(
        "--video-thinking-layout",
        choices=["dashboard", "top", "bottom", "overlay", "side"],
        default="side",
        help="Layout for --video-with-thinking: side puts the thinking panel to the right (default side)",
    )
    parser.add_argument(
        "--clock-mode",
        choices=["realtime", "pdq", "lcrt"],
        default=None,
        help=(
            "Clock protocol. realtime keeps the simulator running while the "
            "model thinks. pdq pauses during model inference and resumes only "
            "for action execution. lcrt is the paused-wallclock, virtual-time "
            "latency scheduler for two-player benchmarks."
        ),
    )
    parser.add_argument("--log", action="store_true",
                        help="Print each step's raw VLM response to stdout")
    parser.add_argument(
        "--prompt-skill", action="append", default=None,
        help=(
            "Markdown prompt-skill file to inject into VLM agents as reusable "
            "experience. May be passed multiple times."
        ),
    )
    parser.add_argument(
        "--no-prompt-skills", action="store_true",
        help="Disable prompt_skills configured in YAML for A/B comparison runs.",
    )
    parser.add_argument(
        "--api-debug", action="store_true",
        help=(
            "Dump every API request + response into <run_dir>/api_debug/. "
            "One JSON file per call; image content blocks get de-duplicated "
            "into <run_dir>/api_debug/images/<sha256>.<ext> and the JSON "
            "references them by relative path. Useful for postmortem on why "
            "the model returned an empty action or hallucinated a frame."
        ),
    )
    parser.add_argument(
        "--flat-output",
        action="store_true",
        help=(
            "For single-cell wrapper runs, write episodes directly under "
            "<output_root>/<YYYYMMDD_HHMMSS>/ instead of "
            "<output_root>/<agent>/<YYYYMMDD_HHMMSS>/."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    cfg = load_benchmark_config(args.config)
    if cfg.get("games") is not None:
        return _run_solo_suite(cfg, args)

    cfg = _override_env(cfg, args)
    cfg = _override_players(cfg, args.players)
    cfg = _override_prompt_skills(cfg, args)
    cfg = _apply_overrides(cfg, args.overrides)

    game_name = cfg.get("game")
    if not game_name:
        print(
            f"Config {args.config} is missing top-level `game:` key. "
            f"Registered games: {', '.join(list_games())}",
            file=sys.stderr,
        )
        return 2
    try:
        game = get_game(game_name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    # Per-experiment dirs land at:
    #   <output_root>/<game>/<agent>/<YYYYMMDD_HHMMSS>/
    # with one second-level timestamp generated per episode inside the
    # runner. No invocation-level aggregate files either; analysis scripts
    # are expected to walk the tree and summarise.
    output_base = (
        args.output_root
        or cfg.get("output_root")
        or "runs"
    )
    # Treat output_root as the storage root. Legacy configs append the game
    # name unless output_root already ends with that game name. Variant /
    # nested layouts can opt out explicitly with output_root_is_final: true.
    output_root_is_final = bool(cfg.get("output_root_is_final", False))
    output_root = (
        output_base
        if output_root_is_final
        or os.path.basename(os.path.normpath(output_base)) == game.name
        else os.path.join(output_base, game.name)
    )

    if game.mode in {"pvp", "coop"}:
        experiments = expand_two_player_experiments(
            cfg, game.name, game.default_task, game.num_agents,
        )
        if not experiments:
            print("No two-player matches expanded from config.", file=sys.stderr)
            return 2

        if args.dry_run:
            print(f"Would run {len(experiments)} match(es):")
            for i, exp in enumerate(experiments, 1):
                player_slug = "_vs_".join(
                    f"player{p.player_index + 1}-{p.agent.model}"
                    for p in exp.players
                )
                path = os.path.join(
                    output_root,
                    "<timestamp>",
                ) if _runtime_bool(cfg, args, "flat_output") else os.path.join(
                    output_root,
                    player_slug,
                    "<timestamp>",
                )
                ports = ", ".join(
                    f"player{p.player_index + 1}={p.host}:{p.port}"
                    for p in exp.players
                )
                print(f"  [{i:3d}] {path} | {ports}")
            print("\nExpanded config (first match):")
            print(json.dumps(experiments[0].to_dict(), indent=2, ensure_ascii=False))
            return 0

        logger = setup_root_logger(output_root, game_name=game.name, verbose=args.verbose)
        logger.info("Loaded config: %s", args.config)
        logger.info("Game         : %s (%s)", game.name, game.mode)
        logger.info("Output root  : %s", output_root)
        logger.info("Matches      : %d", len(experiments))

        clock_mode = args.clock_mode or cfg.get("clock_mode") or "realtime"
        summary = run_two_player_benchmark(
            experiments,
            output_root,
            game,
            live=_runtime_bool(cfg, args, "live"),
            log_vlm=_runtime_bool(cfg, args, "log"),
            api_debug=_runtime_bool(cfg, args, "api_debug"),
            live_vlm_only=_runtime_bool(cfg, args, "live_vlm_only"),
            live_fps=args.live_fps,
            clock_mode=clock_mode,
            record_video=_runtime_bool(cfg, args, "record_video"),
            video_fps=int(cfg.get("video_fps", args.video_fps)),
            video_with_thinking=_runtime_bool(cfg, args, "video_with_thinking"),
            video_thinking_layout=cfg.get(
                "video_thinking_layout",
                args.video_thinking_layout,
            ),
            flat_output=_runtime_bool(cfg, args, "flat_output"),
        )

        logger.info("--- Summary ---")
        logger.info(
            "total=%d ok=%d skip=%d err=%d int=%d",
            summary["n_matches"], summary["n_ok"],
            summary.get("n_skipped", 0),
            summary["n_error"], summary["n_interrupted"],
        )
        return 0 if summary["n_error"] == 0 else 1

    experiments = expand_experiments(cfg, game.name, game.default_task)
    if not experiments:
        print(
            "No experiments expanded from config. Check agents/params/include/exclude.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(f"Would run {len(experiments)} experiment(s):")
        for i, exp in enumerate(experiments, 1):
            if _runtime_bool(cfg, args, "flat_output"):
                path = os.path.join(output_root, "<timestamp>")
            else:
                path = os.path.join(
                    output_root,
                    exp.agent.model,
                    "<timestamp>",
                )
            print(f"  [{i:3d}] {path}")
        print("\nExpanded config (first cell):")
        print(json.dumps(experiments[0].to_dict(), indent=2, ensure_ascii=False))
        return 0

    logger = setup_root_logger(output_root, game_name=game.name, verbose=args.verbose)
    logger.info("Loaded config: %s", args.config)
    logger.info("Game         : %s", game.name)
    logger.info("Output root  : %s", output_root)
    logger.info("Experiments  : %d", len(experiments))

    clock_mode = args.clock_mode or cfg.get("clock_mode") or "realtime"
    summary = run_benchmark(
        experiments,
        output_root,
        game,
        live=_runtime_bool(cfg, args, "live"),
        log_vlm=_runtime_bool(cfg, args, "log"),
        api_debug=_runtime_bool(cfg, args, "api_debug"),
        clock_mode=clock_mode,
        record_video=_runtime_bool(cfg, args, "record_video"),
        video_fps=int(cfg.get("video_fps", args.video_fps)),
        video_with_thinking=_runtime_bool(cfg, args, "video_with_thinking"),
        video_thinking_layout=cfg.get(
            "video_thinking_layout",
            args.video_thinking_layout,
        ),
        flat_output=_runtime_bool(cfg, args, "flat_output"),
    )

    logger.info("-- Summary --")
    logger.info(
        "total=%d ok=%d skip=%d err=%d int=%d",
        summary["n_experiments"], summary["n_ok"],
        summary.get("n_skipped", 0),
        summary["n_error"], summary["n_interrupted"],
    )
    for cell in summary["cells"]:
        agg = cell["aggregated"]
        logger.info(
            "cell %s / %s: n=%d mean_steps=%s mean_wall=%ss",
            cell["agent"], cell["params_id"],
            agg.get("n_episodes"),
            agg.get("mean_steps"), agg.get("mean_wall_time_s"),
        )

    return 0 if summary["n_error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
