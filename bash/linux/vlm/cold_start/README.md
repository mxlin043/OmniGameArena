# Linux VLM Cold-Start Scripts

This directory contains Linux `.sh` entry points for VLM cold-start benchmarks.

Use the newer scripts in these subdirectories for regular runs:

- `pdq/*.sh`
- `lcrt/*.sh`
- `pdq_variant/*.sh`

The top-level directory intentionally contains only this README and the three clock-mode subdirectories. Run scripts live inside the subdirectories.

## Quick Start

Run from the repository root:

```bash
bash/linux/vlm/cold_start/pdq/run_obstacle_run_3d.sh --dry-run
bash/linux/vlm/cold_start/pdq/run_obstacle_run_3d.sh
```

`--dry-run` expands the benchmark cells without starting real episodes. Use it to check configs, output paths, host/port settings, and model combinations.

## Script Groups

### `pdq`

Vanilla PDQ scripts. They use `vanilla_pdq.yaml` and write to `runs/pdq`.

Solo:

- `run_cue_chase.sh`
- `run_last_stand.sh`
- `run_monster_shoot.sh`
- `run_obstacle_run_2d.sh`
- `run_obstacle_run_3d.sh`
- `run_scene_escape.sh`
- `run_solo_craft.sh`

PvP:

- `run_crystal_guard.sh`
- `run_midline_clash.sh`
- `run_sky_duel.sh`

PvP scripts run the full `P1 x P2` product and automatically skip same-model matchups.

Coop:

- `run_handoff_run.sh`
- `run_shared_floor.sh`

Coop scripts use a single `MODELS` list and run each selected model as both player1 and player2.

### `lcrt`

Vanilla LCRT scripts. They use `vanilla_lcrt.yaml` and write to `runs/lcrt`.

Solo:

- `run_last_stand.sh`
- `run_monster_shoot.sh`
- `run_solo_craft.sh`

PvP:

- `run_midline_clash.sh`

Coop:

- `run_shared_floor.sh`

Only these tested games are currently covered for LCRT.

### `pdq_variant`

PDQ baseline scripts for games that also have held-out variant configs. These
launchers use the shared `variant_pdq.yaml` config and write to
`runs/pdq_variant`. Per-map configs such as `variant_pdq_var1.yaml` are used
by IDC best-skill variant evaluation, not by these cold-start launchers.

Solo:

- `run_last_stand.sh`

Coop:

- `run_shared_floor.sh`

Only LastStand and SharedFloor currently have `pdq_variant` launchers.

## Common Tweaks

Each newer script has a `TWEAK HERE` section near the top.

`EPISODES`

Target number of episodes or matches:

```bash
EPISODES=5
```

`COUNT`

Controls how existing results are counted:

```bash
COUNT=fresh
```

- `fresh`: always run `EPISODES` new episodes, ignoring existing results.
- `topup`: count completed results on disk and run only the missing episodes.

Solo scripts count completed episodes with `result.json`. PvP scripts count completed matches with `player_1/result.json` under each pair directory. Coop scripts count completed self-play matches with `match_result.json`.

`VIDEO_WITH_THINKING`

Controls whether `episode.mp4` includes the right-side reason/action text panel:

```bash
VIDEO_WITH_THINKING=1
```

- `1`: record the side panel.
- `0`: record plain gameplay video only.

If video recording causes frame drops, set this to `0`.

## Model Selection

Solo and Coop scripts use `MODELS`:

```bash
MODELS=()
MODELS+=(claude-opus-4-6)
# MODELS+=(gpt-5.5)
```

Uncomment a model line to include that model in the run.

PvP scripts use `P1` and `P2`:

```bash
P1=()
P1+=(claude-opus-4-6)

P2=()
P2+=(claude-sonnet-4-6)
```

The script runs every `P1 x P2` pair and skips pairs where both model names are identical.

Coop scripts do not use `P1/P2` because current Coop runs require both players to use the same model. The script automatically runs:

```bash
--players MODEL MODEL
```

## Host And Port

Scripts with direct environment endpoint overrides have these values in `TWEAK HERE`:

```bash
IP=127.0.0.1
PORT=12345
```

They are passed to `run_benchmark.py` as `--host` and `--port`.

For PvP and Coop, player client host/port values come from the YAML `players:` list. The usual default is:

- player1: `127.0.0.1:12345`
- player2: `127.0.0.1:12346`

If the two players connect to different UE instances, update the corresponding YAML `players` entries.

## Qwen Models

Some scripts include Qwen lines in their model lists. They are usually commented out examples, but a script may intentionally leave one enabled; always check the `TWEAK HERE` section before running. Solo and Coop PDQ scripts may include:

```bash
# MODELS+=(qwen3.5-397b-a17b)
# MODELS+=(qwen3.5-122b-a10b)
```

PvP PDQ scripts use `P1` / `P2` lists instead, so their Qwen examples look like:

```bash
# P1+=(qwen3.5-397b-a17b)
# P2+=(qwen3.5-397b-a17b)
```

LCRT and variant scripts currently do not include Qwen examples by default.

Before enabling or keeping a Qwen line active, deploy the target Qwen model and make sure the router/profile points to the correct model service endpoint.

For Solo runs, also check the script `IP` / `PORT` for the game environment. For PvP and Coop runs, check the YAML `players` host/port values for both game clients.

## Output Layout

Solo:

```text
runs/<clock>/<game>/<model>/<timestamp>/
```

PvP:

```text
runs/<clock>/<game>/player1-<modelA>_vs_player2-<modelB>/<timestamp>/
```

Coop self-play:

```text
runs/<clock>/<game>/player1-<model>_vs_player2-<model>/<timestamp>/
```

`<clock>` is `pdq`, `lcrt`, or `pdq_variant`.
