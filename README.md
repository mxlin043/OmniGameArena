# OmniGameArena

**A Unified UE5 Benchmark for VLM Game Agents with Improvement Dynamics**

[Project Page](https://mxlin043.github.io/OmniGameArena/) | Paper (coming soon) | [Environment · Hugging Face](https://huggingface.co/datasets/mxlin043/OmniGameArena) | [Environment · ModelScope](https://www.modelscope.ai/datasets/mxlin043/OmniGameArena)

OmniGameArena is a real-time benchmark of **twelve** newly built Unreal Engine 5 games spanning **Solo**, **PvP**, and **Coop** play, built to evaluate vision-language-model (VLM) game agents. Beyond single-shot scores, it introduces the **Improvement Dynamics Curve (IDC)**, an agentic-reflection harness that measures how much an agent improves when it is allowed to learn from its own experience.

This repository contains the **agent and benchmark-runner code**. The UE5 game environments are distributed separately (links above).

## Features

- **12 UE5 games** across three regimes: 7 Solo, 3 PvP, 2 Coop.
- **Two evaluation clocks**:
  - **PDQ** (the *Quality* track): the simulator pauses while the model reasons and resumes only to execute the action, isolating decision quality from inference latency.
  - **LCRT** (the *Real-time* track): a paused-wallclock, virtual-time latency scheduler that keeps the game advancing during inference.
- **Improvement Dynamics Curve (IDC)**: a reflection / prompt-skill harness that injects reusable experience and measures the resulting gain (no-skill vs. best-skill across held-out map variants).
- **Pluggable agent backends**: commercial VLMs (OpenAI-compatible and Anthropic routes), self-hosted VLMs (e.g. SGLang), and specialized game policies (NitroGen, OpenP2P).
- **Episode video recording** (`episode.mp4`), optionally with a side panel showing the model's reasoning and actions.
- **Config-driven** (YAML) with rich CLI overrides.

## Games

| Regime | Games |
|---|---|
| **Solo** | ObstacleRun2D, ObstacleRun3D, LastStand, MonsterShoot, SceneEscape, CueChase, SoloCraft |
| **PvP**  | SkyDuel, CrystalGuard, MidlineClash |
| **Coop** | SharedFloor, HandoffRun |

## Getting started

### 1. Download and launch the UE5 environment

The games run in a standalone **Unreal Engine 5 build**, released on [Hugging Face](https://huggingface.co/datasets/mxlin043/OmniGameArena) or [ModelScope](https://www.modelscope.ai/datasets/mxlin043/OmniGameArena). Download the environment and launch it; it waits for the agent over TCP (`host:port`, default `127.0.0.1:12345`). PvP and Coop use a second client (default `127.0.0.1:12346`).

You can also **play the build directly** with a keyboard and mouse or a gamepad. In-game, press **`P`** to open the Map Select menu and **`R`** to reset the current game.

Keep the environment running, then drive it with the agent code below.

### 2. Install the agent code

```bash
conda create -n omnigamearena python=3.10
conda activate omnigamearena
pip install -r requirements.txt
```

### 3. Configure model endpoints

Edit **`configs/router.yaml`** and replace the placeholders with your own values:

```yaml
vlm:
  models:
    qwen3.5-397b-a17b:
      base_url: http://<qwen35_397b ip>:<port>/v1   # your self-hosted (e.g. SGLang) endpoint

commercial:
  openai:                                # OpenAI-compatible route (GPT, Gemini, Kimi, ...)
    base_url: <openai-compatible base url>
    api_key:  <your api key>
  anthropic:                             # Anthropic Messages route (Claude family)
    base_url: <anthropic base url>
    api_key:  <your api key>

policy:
  openp2p:  { url: <openp2p server ip>:<port> }
  nitrogen: { url: <nitrogen server ip>:<port> }
```

Commercial API keys can also be supplied through environment variables instead of being written into `router.yaml`.

### 4. Check the connection (optional)

Before launching a full benchmark, you can verify the agent-to-environment link with the manual teleop tool. It drives the running environment over the **same RemoteInput TCP channel the agents use**, opening a live view you can play with the keyboard and mouse (and a gamepad if one is connected):

```bash
python scripts/manual_control.py --map last_stand --host 127.0.0.1 --port 12345
```

If the live view streams and reacts to your input, the link is working. (The in-game `P` Map Select menu is mouse-only, which RemoteInput cannot click, so here you switch maps with `--map last_stand` or the in-app backtick `` ` `` console, e.g. `open last_stand`.)

For two-player **PvP/Coop** games, pick the side with `--player`: player 1 connects to the base `--port`, player 2 to base + 1 (default `12346`). `--player 2` only applies to PvP/Coop maps (it is refused for single-player ones), so give it a two-player `--map`:

```bash
python scripts/manual_control.py --map crystal_guard --player 2 --host 127.0.0.1 --port 12345
```

### 5. Run a benchmark

With the UE5 environment running, point the runner at its host and port:

```bash
python scripts/run_benchmark.py \
    --config configs/vlm/cold_start/solo/obstacle_run_2d/vanilla_pdq.yaml \
    --host 127.0.0.1 --port 12345
```

The game is chosen by the YAML's `game:` key.

## Usage

### Launcher scripts

Ready-made launchers live under `bash/` (`.cmd` for Windows, `.sh` for Linux). Run them from the repository root.

Windows:

```cmd
bash\win\vlm\cold_start\pdq\run_obstacle_run_3d.cmd
```

Linux:

```bash
bash/linux/vlm/cold_start/pdq/run_obstacle_run_3d.sh
```

Each launcher has a `TWEAK HERE` block near the top for `EPISODES`, model selection (`MODELS` for Solo/Coop, `P1` / `P2` for PvP), `IP` / `PORT`, and video options. PvP launchers run the full `P1 x P2` product (skipping same-model pairs); Coop launchers run self-play with both players on the same model.

### Improvement Dynamics Curve (IDC)

IDC measures how much an agent improves once it can reflect on its own play. It currently covers **LastStand** (solo) and **SharedFloor** (coop), and runs in two stages.

**1. Reflection run** — build the curve and learn a skill on the base map:

```bash
python scripts/run_idc.py --config configs/vlm/idc/last_stand.yaml --model claude-opus-4-6 \
    --host 127.0.0.1 --port 12345
```

Round 00 is the **no-skill baseline**, staged from the matching PDQ run under `runs/pdq` (run that cold-start benchmark first); each later round lets the agent reflect and accumulate a skill. Results land in `runs/idc/<game>/<model>/<timestamp>/`.

**2. Best skill on held-out variants** — inject the best learned skill and run unseen maps:

```bash
python scripts/run_idc_best_skill_variants.py --game last_stand \
    --variants var1 var2 var3 --host 127.0.0.1 --port 12345
```

It picks the highest-scoring round's skill and runs the held-out variants `variant_pdq_var{1,2,3}.yaml`, writing them under `.../unseen_variants/<varN>/best_skill/`. To get the **no-skill** number on a variant (the comparison baseline), run that variant config without a skill:

```bash
python scripts/run_benchmark.py \
    --config configs/vlm/cold_start/solo/last_stand/variant_pdq_var1.yaml \
    --host 127.0.0.1 --port 12345
```

Ready-made launchers live in `bash/<os>/vlm/idc/`: `run_<game>.cmd` / `.sh` (reflection) and `run_<game>_best_skill.cmd` / `.sh` (best-skill variants).

### Useful flags (`scripts/run_benchmark.py`)

| Flag | Description |
|---|---|
| `--clock-mode {pdq,lcrt}` | Quality clock (pdq) or latency-scheduled clock (lcrt) |
| `--episodes N` | Episodes per cell |
| `--players MODEL_A MODEL_B` | Override the two models for a PvP/Coop match |
| `--record-video [--video-with-thinking]` | Save `episode.mp4`, optionally with a reasoning side panel |
| `--set key.path=value` | Override any config field (parsed as JSON; lists become sweeps) |

Run `python scripts/run_benchmark.py --help` for the full list.

## Repository structure

```
omni_game_arena/          Core Python package
  benchmark/              Config loading, experiment expansion, runners, game registry
    games/                Per-game definitions
    improvement_dynamics_curve/   IDC harness
  env/                    UE5 environment clients (connect over host:port)
  models/                 VLM and policy backends (commercial / self-hosted)
  prompts/                Prompt templates (per game / IDC / methods)
  skill/                  Prompt-skill (reflection) machinery
  eval/                   Scoring + episode video recording
  adapters/  utils/       Glue code and helpers
configs/                  YAML benchmark and endpoint configs
  router.yaml             Central endpoint router (model URLs + API keys)
  maps.yaml               Map definitions
  vlm/ nitrogen/ openp2p/ Per-method config trees
bash/                     Ready-to-run launchers (win/*.cmd, linux/*.sh)
scripts/                  Python entry points (run_benchmark.py, run_idc*.py, manual_control.py)
requirements.txt
```

## Output layout

```
runs/<clock>/<game>/<model>/<timestamp>/                      # Solo
runs/<clock>/<game>/player1-<A>_vs_player2-<B>/<timestamp>/   # PvP / Coop
```

Each episode directory holds `result.json`, per-step frames, and (with `--record-video`) `episode.mp4`. `<clock>` is `pdq`, `lcrt`, or `pdq_variant`.

## Citation

```bibtex
@article{lin2026omnigamearena,
  title   = {OmniGameArena: A Unified UE5 Benchmark for VLM Game Agents with Improvement Dynamics},
  author  = {Lin, Mingxian and Qian, Shengju and Liu, Yuqi and Huang, Yi-Hua and Wang, Yiyu and Huang, Wei and Li, Yitang and Zhang, Fan and Hu, Zeyu and Zhu, Lingting and Wang, Xin and Qi, Xiaojuan},
  journal = {arXiv preprint},
  year    = {2026}
}
```
