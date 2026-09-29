"""Run LFM/LCM configs with explicit models and shared UE ports.

PvP follows the paper's pairwise protocol: every pairing of two distinct
models plays --episodes matches with each model as Player 1, so the default
of five gives ten matches per pairing.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_idc_best_skill_variants import GAME_SPECS
from omni_game_arena.clock import normalize_clock_mode


def pvp_pairings(models, opponents=None):
    """Unordered pairings of two distinct models, in first-seen order.

    With opponents, each model meets each opponent; without, every two of
    the models meet (a round robin). Self-play and repeats are dropped.
    """
    models = list(dict.fromkeys(models))
    if opponents:
        candidates = [(a, b) for a in models for b in dict.fromkeys(opponents)]
    else:
        candidates = itertools.combinations(models, 2)
    pairings, seen = [], set()
    for a, b in candidates:
        key = frozenset((a, b))
        if a != b and key not in seen:
            seen.add(key)
            pairings.append((a, b))
    return pairings


def build_commands(args, extra):
    spec = GAME_SPECS[args.game]
    clock = normalize_clock_mode(args.clock)
    if clock not in ('lfm', 'lcm'):
        raise ValueError('Cold start supports lfm or lcm')
    if args.episodes < 1 or not 1 <= args.port <= 65534:
        raise ValueError('Positive episodes and a base port in 1..65534 are required')
    if spec.mode != 'pvp' and args.opponents:
        raise ValueError('--opponents applies only to PvP')
    if spec.mode == 'pvp':
        pairings = pvp_pairings(args.models, args.opponents)
        if not pairings:
            raise ValueError('PvP needs two distinct models: pass --opponents, or two or more --models')
        # Each pairing plays both seatings, the same number of matches each.
        seatings = [seating for a, b in pairings for seating in ((a, b), (b, a))]
    else:
        seatings = [(model, model) for model in dict.fromkeys(args.models)]
    config = ROOT / 'configs/vlm/cold_start' / spec.mode / spec.name / f'vanilla_{clock}.yaml'
    commands = []
    for model, opponent in seatings:
        cmd = [sys.executable, str(ROOT / 'scripts/run_benchmark.py'),
               '--config', str(config), '--host', args.host, '--port', str(args.port),
               '--episodes', str(args.episodes), '--clock-mode', clock]
        if spec.mode == 'solo':
            cmd += ['--set', 'agents=' + json.dumps([model])]
        else:
            players = [dict(id=i, host=args.host, port=args.port + i, model=name)
                       for i, name in enumerate((model, opponent))]
            cmd += ['--set', 'players=' + json.dumps(players)]
        if args.output_root:
            cmd += ['--output-root', args.output_root]
        if args.dry_run:
            cmd += ['--dry-run']
        cmd += extra
        commands.append(cmd)
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', required=True, choices=sorted(GAME_SPECS))
    parser.add_argument('--clock', type=normalize_clock_mode, choices=('lfm', 'lcm'), default='lfm',
                        help='lfm (default): latency-free; lcm: server inference time charged.')
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--opponents', nargs='+',
                        help='PvP only: pair each --models entry with each opponent. '
                             'Without it, every two --models play each other.')
    parser.add_argument('--episodes', type=int, default=5,
                        help='Solo/Coop: episodes per model. PvP: matches per seating, '
                             'so each pairing plays twice this many (default: 5).')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=12345)
    parser.add_argument('--output-root')
    parser.add_argument('--dry-run', action='store_true')
    args, extra = parser.parse_known_args(argv)
    try:
        commands = build_commands(args, extra)
    except ValueError as exc:
        parser.error(str(exc))
    if GAME_SPECS[args.game].mode == 'pvp':
        pairings = len(commands) // 2
        print(f'[pvp] {pairings} pairing(s) x 2 seatings x {args.episodes} matches '
              f'= {len(commands) * args.episodes} matches', flush=True)
    for command in commands:
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
