<h1 align="center">
  <strong>OmniGameArena</strong><br>
  A Unified UE5 Benchmark for VLM Game Agents with Improvement Dynamics
</h1>

<p align="center">
  <a href="https://mxlin043.github.io/OmniGameArena/"><img src="https://img.shields.io/badge/Project-Page-6e5494?logo=googlechrome&logoColor=white" alt="Project Page"></a>
  <a href="https://arxiv.org/abs/2606.09826"><img src="https://img.shields.io/badge/arXiv-Paper-b31b1b?logo=arxiv&logoColor=white" alt="Paper"></a>
  <a href="https://huggingface.co/datasets/mxlin043/OmniGameArena"><img src="https://img.shields.io/badge/Hugging_Face-Environment-ff9d00?logo=huggingface&logoColor=white" alt="Hugging Face"></a>
  <a href="https://www.modelscope.ai/datasets/mxlin043/OmniGameArena"><img src="https://img.shields.io/badge/ModelScope-Environment-2f6cad?logo=modelscope&logoColor=white" alt="ModelScope"></a>
</p>

---

## 📌 Overview

**OmniGameArena** is a real-time benchmark of twelve Unreal Engine 5 games spanning **Solo**, **PvP**, and **Coop** play. Its **Improvement Dynamics Curve (IDC)** measures how agents improve by reflecting on their own experience and transferring learned skills to new game variants.

<div align="center">
  <img src="https://mxlin043.github.io/OmniGameArena/webpage/assets/paper/teaser.svg" width="100%" alt="OmniGameArena overview">
</div>

This repository contains the **agent and benchmark-runner code**. The UE5 game environments are distributed separately through the links above.

- 🎮 **12 games and 60 maps**: one original map and four variants (**Var1–Var4**) per game.
- ⏱️ **Two evaluation clocks**: LFM pauses the game during inference; LCM charges model inference time to the game clock.
- 📈 **Three-stage evaluation**: cold start, IDC reflection, and skill transfer.
- 🔌 **Multiple backends**: OpenAI-compatible APIs, Anthropic, self-hosted VLMs, NitroGen, and OpenP2P.
- 🎥 **Live viewing and video recording**, optionally with the model's reasoning and actions.

---

## 🕹️ Games

| Regime | Game IDs |
|---|---|
| 🧍 Solo | `obstacle_run_2d`, `obstacle_run_3d`, `last_stand`, `monster_shoot`, `scene_escape`, `cue_chase`, `solo_craft` |
| ⚔️ PvP | `sky_duel`, `crystal_guard`, `midline_clash` |
| 🤝 Coop | `shared_floor`, `handoff_run` |

---

## 🚀 Getting Started

### 1. Download and launch the UE5 environment

Download the Windows or Linux build from [Hugging Face](https://huggingface.co/datasets/mxlin043/OmniGameArena) or [ModelScope](https://www.modelscope.ai/datasets/mxlin043/OmniGameArena). Extract it and launch from the environment directory:

**Windows:**

```powershell
.\Windows\OmniGameArena.exe -RemoteInputPort=12345
```

**Linux:**

```bash
./Linux/OmniGameArena.sh -RemoteInputPort=12345
```

Keep the game running. It waits for Python over TCP: **12345** for Player 1 and **12346** for Player 2. Linux requires a GPU with Vulkan SM6 support.

You can also play directly: press **P** for map selection, **R** to restart during play, and **2** to switch the locally controlled player. Benchmark runners reset completed games automatically.

### 2. Install the agent code

Create a Python 3.11 environment and install dependencies from the repository root:

```bash
conda create -n omnigamearena python=3.11
conda activate omnigamearena
python -m pip install -r requirements.txt
```

Run the remaining Python commands from this directory.

### 3. Configure model endpoints

Open [configs/router.yaml](configs/router.yaml) and fill in your API keys:

```yaml
commercial:
  openai:
    base_url: https://api.openai.com/v1
    api_key: YOUR_OPENAI_API_KEY
  anthropic:
    base_url: https://api.anthropic.com
    api_key: YOUR_ANTHROPIC_API_KEY
```

GPT, Gemini and Kimi use the OpenAI-compatible route. To send Gemini or Kimi to their own endpoints, uncomment `model_overrides` in the same file and give each override its own `base_url` and `api_key`. Self-hosted Qwen and the NitroGen/OpenP2P policies take their local URLs from the `vlm` and `policy` sections.

You can also leave the keys empty and set `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` instead; a set environment variable takes precedence. Do not commit `router.yaml` after adding keys.

For a Responses endpoint, set `api_mode: responses`; for an Anthropic gateway, set its required `auth_style` (`x-api-key` or `bearer`).

### 4. Check the connection (optional)

The manual controller opens a live view of the running game:

```bash
python scripts/manual_control.py --map last_stand --host 127.0.0.1 --port 12345
```

Focus the viewer to send input. Switch maps with `--map` or its backtick console (`open last_stand`); on a two-player map, `--player 2` connects to base port + 1. Close the controller before running a benchmark.

<details>
<summary>Running UE on another machine</summary>

Start UE on the remote machine as in Step 1. From the Python machine, use `--host ue-host.example --port 12345` in the controller and benchmark commands. Allow TCP 12345–12346 from the Python machine.

If using SSH, run this on the Python machine and keep the tunnel open:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:12345:127.0.0.1:12345 -L 127.0.0.1:12346:127.0.0.1:12346 user@ue-host.example
```

Then use the local defaults, `--host 127.0.0.1 --port 12345`. The router's API URLs point to model services; these TCP ports connect to the game.

</details>

### 5. Run a benchmark

With UE running, start a no-skill baseline on Last Stand:

```bash
python scripts/run_cold_start.py --game last_stand --models claude-opus-4-6 --episodes 5
```

This connects to `127.0.0.1:12345` and saves results under `runs/lfm/last_stand/`. Use this baseline for the IDC example below. Add `--host` and `--port` when using another game address.

---

## 🧪 Usage

### Launcher scripts

Ready-made launchers live under `bash/`. Run them from the repository root and append CLI options as needed.

**Windows:**

```cmd
bash\win\vlm\cold_start\lfm\run_last_stand.cmd --episodes 5
```

**Linux:**

```bash
bash bash/linux/vlm/cold_start/lfm/run_last_stand.sh --episodes 5
```

### Cold-start evaluation

Solo and Coop run five episodes per model by default. PvP plays ten matches per pairing, five with each model as Player 1, and a model never plays itself. `--episodes` sets the count per model (Solo/Coop) or per seating (PvP).

Runs use **LFM** by default. To charge model inference time to the game clock, add `--clock lcm`. LCM requires inference-only timing in `usage.latency_checkpoint.engine_ttlt_ms` (OpenAI-compatible) or `X-Amzn-Bedrock-Invocation-Latency` (Anthropic), in milliseconds; missing or invalid timing stops the run.

Coop uses the same model for both players. In PvP, each `--models` entry plays each `--opponents` entry; without `--opponents`, every two of the listed models play each other:

```bash
python scripts/run_cold_start.py --game shared_floor --models claude-opus-4-6 --episodes 5
python scripts/run_cold_start.py --game midline_clash --models claude-opus-4-6 --opponents gpt-5.5 --episodes 5
python scripts/run_cold_start.py --game midline_clash --models claude-opus-4-6 gpt-5.5 gemini-3.1-pro-preview Kimi-K2.5 qwen3.5-397b-a17b
```

### Improvement Dynamics Curve (IDC)

IDC reflects on completed episodes, writes a reusable skill, and evaluates it in the next round. Configurations cover all twelve games.

<div align="center">
  <img src="https://mxlin043.github.io/OmniGameArena/webpage/assets/paper/idc-framework.png" width="100%" alt="Multi-round reflection: experience acquisition, a four-stage reflection module, and a persistent module">
</div>

Each new skill goes through an LLM judge (`validate_skill`) before it is used, with at most five judge calls per round. If the fifth draft still fails, the bullets the judge quoted are deleted and the rest is used.

```bash
python scripts/run_idc.py --config configs/vlm/idc/last_stand.yaml --model claude-opus-4-6
```

Each run evaluates one model on one game. `--model` selects the model; without it, the run uses the `model:` line of the YAML (`claude-opus-4-6`), and so do the IDC launchers under `bash/`. The paper runs IDC for six models: `claude-opus-4-7`, `claude-opus-4-6`, `gpt-5.5`, `gemini-3.1-pro-preview`, `gemini-3-flash-preview` and `qwen3.5-397b-a17b`. To reproduce a game, run it once per model; for Solo/Coop, run each model's cold start on that game first, and the Qwen model needs its self-hosted server (Step 3).

**Linux:**

```bash
for model in claude-opus-4-7 claude-opus-4-6 gpt-5.5 gemini-3.1-pro-preview gemini-3-flash-preview qwen3.5-397b-a17b; do
  python scripts/run_idc.py --config configs/vlm/idc/last_stand.yaml --model "$model"
done
```

**Windows (PowerShell):**

```powershell
foreach ($model in "claude-opus-4-7", "claude-opus-4-6", "gpt-5.5", "gemini-3.1-pro-preview", "gemini-3-flash-preview", "qwen3.5-397b-a17b") {
  python scripts/run_idc.py --config configs/vlm/idc/last_stand.yaml --model $model
}
```

For Solo/Coop, round 0 is the cold-start score: the earliest five completed LFM episodes of the same game and model under `runs/lfm` (another directory with `--lfm-root`), whatever their scores; with fewer than five the run stops. The default is ten rounds of five episodes after round 0, giving the curve R0–R10; adjust `--rounds` and `--episodes-per-round` as needed.

Coop gives the skill to both players. PvP generates its own round-0 baseline against the five opponents listed in the IDC YAML, with one match per opponent per round; only P1 learns a skill. Use `--pvp-episodes-per-opponent` to change that budget.

Results go to `runs/idc/<game>/<model>/<run>/`. Note the printed run directory for transfer. Resume with `--resume path/to/idc-run`; choose another tool-capable reflector with `--reflector-model`.

### Skill transfer to Var1–Var4

Use the completed IDC directory to compare no-skill and the best measured skill on a held-out variant:

```bash
python scripts/run_idc_best_skill_variants.py --game last_stand --idc-run "path/to/idc-run" --variants Var1 --episodes 5 --no-skill
python scripts/run_idc_best_skill_variants.py --game last_stand --idc-run "path/to/idc-run" --variants Var1 --episodes 5
```

Repeat the pair for Var2–Var4. Results are saved under the IDC run's `unseen_variants/`. If the empty-skill baseline is uniquely best, the runner reports that no learned best skill exists.

Coop gives the skill to both players; PvP uses the fixed IDC opponent roster and gives the skill to P1 only.

### Useful flags

| Flag | Applies to | Purpose |
|---|---|---|
| `--host HOST --port PORT` | All runners | UE game address |
| `--dry-run` | All runners | Inspect configuration before running |
| `--live --record-video --video-with-thinking` | Cold start | Live view and video with reasoning/actions |
| `--set key.path=value` | Cold start | Override a YAML field |
| `--no-live --no-log --no-video` | Transfer | Disable viewing and recording |

Run a script with `--help` for its full options.

---

## 🗂️ Repository Structure

```text
omni_game_arena/    Agents, UE clients, prompts, evaluation, and IDC
configs/            Model routes, maps, and benchmark configurations
scripts/            Python entry points
bash/               Windows and Linux launchers
requirements.txt
```

---

## 📦 Output Layout

```text
runs/lfm/<game>/...          LFM baseline episodes
runs/lcm/<game>/...          LCM episodes
runs/idc/<game>/<model>/...  IDC curves, skills, and transfer results
runs/variant_eval/...        Standalone variant evaluations
```

Episode directories contain result JSON, screenshots, reflection traces, and optionally `episode.mp4`.

---

## 📖 Citation

If you find our work helpful, please cite:

```bibtex
@misc{lin2026omnigamearena,
  title         = {OmniGameArena: A Unified UE5 Benchmark for VLM Game Agents with Improvement Dynamics},
  author        = {Lin, Mingxian and Qian, Shengju and Liu, Yuqi and Huang, Yi-Hua and Wang, Yiyu and Huang, Wei and Li, Yitang and Zhang, Fan and Hu, Zeyu and Zhu, Lingting and Wang, Xin and Qi, Xiaojuan},
  year          = {2026},
  eprint        = {2606.09826},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CV},
  doi           = {10.48550/arXiv.2606.09826},
  url           = {https://arxiv.org/abs/2606.09826}
}
```

---

## 📄 License

This project is released under the [Apache License 2.0](LICENSE).
