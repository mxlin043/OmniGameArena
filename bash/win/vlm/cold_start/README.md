# Windows VLM Cold-Start Scripts

This directory contains Windows `.cmd` entry points for VLM cold-start benchmarks.

Use the newer scripts in these subdirectories for regular runs:

- `pdq\*.cmd`
- `lcrt\*.cmd`
- `pdq_variant\*.cmd`

The top-level directory intentionally contains only this README and the three clock-mode subdirectories. Run scripts live inside the subdirectories.

## Quick Start

Run from the repository root:

```cmd
bash\win\vlm\cold_start\pdq\run_obstacle_run_3d.cmd --dry-run
bash\win\vlm\cold_start\pdq\run_obstacle_run_3d.cmd
```

`--dry-run` expands the benchmark cells without starting real episodes. Use it to check configs, output paths, host/port settings, and model combinations.

## Script Groups

### `pdq`

Vanilla PDQ scripts. They use `vanilla_pdq.yaml` and write to `runs\pdq`.

Solo:

- `run_cue_chase.cmd`
- `run_last_stand.cmd`
- `run_monster_shoot.cmd`
- `run_obstacle_run_2d.cmd`
- `run_obstacle_run_3d.cmd`
- `run_scene_escape.cmd`
- `run_solo_craft.cmd`

PvP:

- `run_crystal_guard.cmd`
- `run_midline_clash.cmd`
- `run_sky_duel.cmd`

PvP scripts run the full `P1 x P2` product and automatically skip same-model matchups.

Coop:

- `run_handoff_run.cmd`
- `run_shared_floor.cmd`

Coop scripts use a single `MODELS` list and run each selected model as both player1 and player2.

### `lcrt`

Vanilla LCRT scripts. They use `vanilla_lcrt.yaml` and write to `runs\lcrt`.

Solo:

- `run_last_stand.cmd`
- `run_monster_shoot.cmd`
- `run_solo_craft.cmd`

PvP:

- `run_midline_clash.cmd`

Coop:

- `run_shared_floor.cmd`

Only these tested games are currently covered for LCRT.

### `pdq_variant`

PDQ baseline scripts for games that also have held-out variant configs. These
launchers use the shared `variant_pdq.yaml` config and write to
`runs\pdq_variant`. Per-map configs such as `variant_pdq_var1.yaml` are used
by IDC best-skill variant evaluation, not by these cold-start launchers.

Solo:

- `run_last_stand.cmd`

Coop:

- `run_shared_floor.cmd`

Only LastStand and SharedFloor currently have `pdq_variant` launchers.

## Common Tweaks

Each newer script has a `TWEAK HERE` section near the top.

`EPISODES`

Target number of episodes or matches:

```cmd
set "EPISODES=5"
```

`COUNT`

Controls how existing results are counted:

```cmd
set "COUNT=fresh"
```

- `fresh`: always run `EPISODES` new episodes, ignoring existing results.
- `topup`: count completed results on disk and run only the missing episodes.

Solo scripts count completed episodes with `result.json`. PvP scripts count completed matches with `player_1\result.json` under each pair directory. Coop scripts count completed self-play matches with `match_result.json`.

`VIDEO_WITH_THINKING`

Controls whether `episode.mp4` includes the right-side reason/action text panel:

```cmd
set "VIDEO_WITH_THINKING=1"
```

- `1`: record the side panel.
- `0`: record plain gameplay video only.

If video recording causes frame drops, set this to `0`.

## Model Selection

Solo and Coop scripts use `MODELS`:

```cmd
set "MODELS="
set "MODELS=!MODELS! claude-opus-4-6"
@REM set "MODELS=!MODELS! gpt-5.5"
```

Uncomment a model line to include that model in the run.

PvP scripts use `P1` and `P2`:

```cmd
set "P1="
set "P1=!P1! claude-opus-4-6"

set "P2="
set "P2=!P2! claude-sonnet-4-6"
```

The script runs every `P1 x P2` pair and skips pairs where both model names are identical.

Coop scripts do not use `P1/P2` because current Coop runs require both players to use the same model. The script automatically runs:

```cmd
--players MODEL MODEL
```

## Host And Port

Scripts with direct environment endpoint overrides have these values in `TWEAK HERE`:

```cmd
set "IP=127.0.0.1"
set "PORT=12345"
```

They are passed to `run_benchmark.py` as `--host` and `--port`.

For PvP and Coop, player client host/port values come from the YAML `players:` list. The usual default is:

- player1: `127.0.0.1:12345`
- player2: `127.0.0.1:12346`

If the two players connect to different UE instances, update the corresponding YAML `players` entries.

## Qwen Models

Some scripts include Qwen lines in their model lists. They are usually commented out examples, but a script may intentionally leave one enabled; always check the `TWEAK HERE` section before running. Solo and Coop PDQ scripts may include:

```cmd
@REM set "MODELS=!MODELS! qwen3.5-397b-a17b"
@REM set "MODELS=!MODELS! qwen3.5-122b-a10b"
```

PvP PDQ scripts use `P1` / `P2` lists instead, so their Qwen examples look like:

```cmd
@REM set "P1=!P1! qwen3.5-397b-a17b"
@REM set "P2=!P2! qwen3.5-397b-a17b"
```

LCRT and variant scripts currently do not include Qwen examples by default.

Before enabling or keeping a Qwen line active, deploy the target Qwen model and make sure the router/profile points to the correct model service endpoint.

For Solo runs, also check the script `IP` / `PORT` for the game environment. For PvP and Coop runs, check the YAML `players` host/port values for both game clients.

## Output Layout

Solo:

```text
runs\<clock>\<game>\<model>\<timestamp>\
```

PvP:

```text
runs\<clock>\<game>\player1-<modelA>_vs_player2-<modelB>\<timestamp>\
```

Coop self-play:

```text
runs\<clock>\<game>\player1-<model>_vs_player2-<model>\<timestamp>\
```

`<clock>` is `pdq`, `lcrt`, or `pdq_variant`.
