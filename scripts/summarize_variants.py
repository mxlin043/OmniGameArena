"""Summarize canonical Var results, keeping failed attempts and missing cells visible."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_idc_best_skill_variants as runner

ARMS = ('no_skill', 'best_skill')


def expected_prompt_key(game, variant):
    """The game_prompt_key the variant config assigns (None for origin)."""
    spec = runner.GAME_SPECS[game]
    path = ROOT / runner.default_config_pattern(spec).format(game=game, variant=variant)
    cfg = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    defaults = cfg.get('agents_defaults') or cfg.get('players_defaults') or {}
    return defaults.get('game_prompt_key')


def finite_score(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def result_score(result, mode):
    if mode == 'pvp':
        value = ((result.get('player_results') or {}).get('player_1') or {}).get('score')
    elif mode == 'coop':
        value = result.get('coop_total_score')
    else:
        metrics = result.get('metrics') or {}
        value = (metrics.get('game') or {}).get('score', metrics.get('score'))
    return finite_score(value)


def invalid_reason(result, *, game, model, variant, arm, opponent):
    spec = runner.GAME_SPECS[game]
    if result.get('game') != game:
        return 'game_mismatch'
    with_skill = arm == 'best_skill'
    if (result.get('parameters') or {}).get('with_skill_prompt') is not with_skill:
        return 'skill_flag_mismatch'
    if spec.mode == 'solo':
        profiles = [result.get('agent') or {}]
    else:
        players = result.get('players') or []
        by_id = {p.get('player_id', p.get('player_index')): p.get('agent') or {} for p in players}
        if len(players) != 2 or set(by_id) != {1, 2}:
            return 'player_seats_mismatch'
        profiles = [by_id[1], by_id[2]]
    for index, profile in enumerate(profiles):
        expected_model = opponent if spec.mode == 'pvp' and index == 1 else model
        if profile.get('model') != expected_model:
            return 'model_mismatch'
        receives_skill = with_skill and (spec.mode != 'pvp' or index == 0)
        if bool(profile.get('prompt_skills')) != receives_skill:
            return 'skill_assignment_mismatch'
        suffix = f'_player{index + 1}' if spec.mode != 'solo' else ''
        key = profile.get('game_prompt_key')
        # Result JSON stores AgentProfile before factory's player substitution.
        if spec.mode != 'solo' and isinstance(key, str):
            key = key.replace('{player}', str(index + 1))
        if variant == 'origin':
            if key not in (None, '', game, game + suffix):
                return 'prompt_mismatch'
        else:
            expected = expected_prompt_key(game, variant)
            if isinstance(expected, str):
                expected = expected.replace('{player}', str(index + 1))
            if key != expected:
                return 'prompt_mismatch'
    if result_score(result, spec.mode) is None:
        return 'missing_or_nonfinite_score'
    return None


def summarize_cell(directory, *, game, model, variant, arm, target, opponent=None):
    spec = runner.GAME_SPECS[game]
    attempts = []
    for path in sorted(directory.rglob(spec.result_file)) if directory.exists() else []:
        row = dict(result_json=str(path.resolve()), counted=False)
        try:
            result = json.loads(path.read_text(encoding='utf-8-sig'))
            status = result.get('status')
            row['status'] = status
            reason = (status or 'missing_status') if status != 'ok' else invalid_reason(
                result, game=game, model=model, variant=variant, arm=arm, opponent=opponent)
            if reason is None:
                row['score'] = result_score(result, spec.mode)
                row['valid'] = True
            else:
                row['valid'] = False
                row['reason'] = reason or 'missing_status'
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            row.update(valid=False, reason='unreadable_or_invalid_result', error=str(exc))
        attempts.append(row)
    valid = [row for row in attempts if row['valid']]
    selected = valid[:target]
    for row in selected:
        row['counted'] = True
    scores = [row['score'] for row in selected]
    return dict(game=game, model=model, variant=variant, arm=arm, opponent_model=opponent,
                directory=str(directory), target_episodes=target, total_attempts=len(attempts),
                valid_episodes=len(valid), credited_episodes=len(selected),
                surplus_retained=max(0, len(valid) - target), complete=len(selected) == target,
                mean_score=statistics.mean(scores) if scores else None,
                sample_std_score=statistics.stdev(scores) if len(scores) > 1 else (0.0 if scores else None),
                scores=scores, attempts=attempts)


def build_summary(root, games, models=None, variants=None, episodes=5, pvp_episodes=1, opponents=None):
    if episodes < 1 or pvp_episodes < 1:
        raise ValueError('Episode targets must be positive')
    cells = []
    comparisons = []
    missing_games = []
    for game in games:
        spec = runner.GAME_SPECS[game]
        names = runner.resolve_variants(spec, variants)
        game_dir = root / game
        selected_models = list(dict.fromkeys(models)) if models else (
            sorted(p.name for p in game_dir.iterdir() if p.is_dir()) if game_dir.exists() else [])
        if not selected_models:
            missing_games.append(game)
        roster = runner.fixed_pvp_opponents(game) if spec.mode == 'pvp' else (None,)
        selected_opponents = tuple(dict.fromkeys(opponents)) if opponents and spec.mode == 'pvp' else roster
        if any(o not in roster for o in selected_opponents):
            raise ValueError(f'{game}: opponent outside the fixed IDC roster')
        target = pvp_episodes if spec.mode == 'pvp' else episodes
        for model in selected_models:
            for variant in names:
                pair_cells = {arm: [] for arm in ARMS}
                for opponent in selected_opponents:
                    for arm in ARMS:
                        directory = game_dir / model / variant / arm
                        if opponent is not None:
                            directory = directory / 'fixed_opponents' / opponent
                        cell = summarize_cell(directory, game=game, model=model, variant=variant,
                                              arm=arm, target=target, opponent=opponent)
                        cells.append(cell)
                        pair_cells[arm].append(cell)
                # A score comparison is published only when both arms and every opponent are complete.
                complete = all(c['complete'] for arm in ARMS for c in pair_cells[arm])
                means = {arm: statistics.mean(c['mean_score'] for c in pair_cells[arm]) if complete else None for arm in ARMS}
                comparisons.append(dict(game=game, model=model, variant=variant, complete=complete,
                                        no_skill_mean=means['no_skill'], best_skill_mean=means['best_skill'],
                                        absolute_gain=means['best_skill'] - means['no_skill'] if complete else None))
    return dict(generated_at_utc=datetime.now(timezone.utc).isoformat(), root=str(root),
                complete=bool(cells) and not missing_games and all(c['complete'] for c in cells),
                missing_games=missing_games,
                target_episodes=sum(c['target_episodes'] for c in cells),
                credited_episodes=sum(c['credited_episodes'] for c in cells),
                surplus_retained=sum(c['surplus_retained'] for c in cells), cells=cells, comparisons=comparisons)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='runs/variant_eval')
    parser.add_argument('--games', '--game', nargs='+', choices=sorted(runner.GAME_SPECS))
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--variants', '--variant', nargs='+')
    parser.add_argument('--opponents', nargs='+', help='PvP subset, matching the batch request.')
    parser.add_argument('--episodes', type=int, default=5)
    parser.add_argument('--pvp-episodes-per-opponent', type=int, default=1)
    parser.add_argument('--output', help='Optional JSON file; otherwise print JSON without writing files.')
    args = parser.parse_args(argv)
    try:
        root = (ROOT / args.root).resolve()
        summary = build_summary(root, args.games or list(runner.GAME_SPECS), args.models,
                                args.variants, args.episodes, args.pvp_episodes_per_opponent, args.opponents)
        if args.output:
            runner.write_manifest((ROOT / args.output).resolve(), summary)
        else:
            print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
        return 0 if summary['complete'] else 1
    except (OSError, ValueError) as exc:
        print(f'[error] {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
