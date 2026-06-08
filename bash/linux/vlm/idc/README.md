# VLM IDC Linux Launchers

This directory contains Linux `.sh` launchers for VLM Improvement Dynamics Curve (IDC) runs and the follow-up best-skill variant evaluation.

Run these scripts from a shell after editing the `TWEAK HERE` block. Each script changes to the repository root before invoking Python.

## Scripts

| Script | Purpose |
| --- | --- |
| `run_last_stand.sh` | Run IDC on the LastStand base map. |
| `run_shared_floor.sh` | Run IDC on the SharedFloor coop base map. Player 1 and player 2 use the same model and shared skill. |
| `run_last_stand_best_skill.sh` | Evaluate LastStand held-out variants with the best measured IDC skill. |
| `run_shared_floor_best_skill.sh` | Evaluate SharedFloor held-out variants with the best measured IDC skill. |

## Regular IDC Runs

Use these first:

```bash
bash/linux/vlm/idc/run_last_stand.sh
bash/linux/vlm/idc/run_shared_floor.sh
```

Important tweak variables:

| Variable | Meaning |
| --- | --- |
| `MODEL` | Player model for the IDC run. The default active model is `claude-opus-4-6`; other examples are commented with `#`. |
| `REFLECTOR_MODEL` | Optional separate model for reflection. Empty means reuse `MODEL`. |
| `IP` / `PORT` | Target environment host and port. For SharedFloor, `PORT` is player 1 and player 2 uses `PORT + 1`. |
| `ROUNDS` | Number of IDC rounds after round 00. Default: `10`. |
| `EPISODES_PER_ROUND` | Episodes per IDC round. Default: `5`. |
| `PDQ_ROOT` | Source root for official PDQ baseline traces used as round 00. Default: `runs/pdq`. |
| `OUTPUT_ROOT` | IDC output root. Default: `runs/idc`. |
| `RESUME` | Existing IDC run directory to resume. Empty starts a fresh run. |
| `LIVE` | `1` opens the live viewer and IDC progress panel. |
| `LOG_VLM` | `1` prints player VLM responses during episodes. |
| `API_DEBUG` | `1` dumps player API debug files; reflection API logs are always saved per round. |

Fresh runs write to:

```text
runs/idc/<game>/<model>/<timestamp>/
```

Resume example:

```bash
RESUME=runs/idc/last_stand/claude-opus-4-6/20260529_191934
```

The resume directory must contain `idc_config.json`.

## Best-Skill Variant Runs

Use these after IDC has produced `idc_curve.json` and round results:

```bash
bash/linux/vlm/idc/run_last_stand_best_skill.sh
bash/linux/vlm/idc/run_shared_floor_best_skill.sh
```

These scripts call `scripts/run_idc_best_skill_variants.py`. The runner finds the skill that produced the highest measured base-map score, injects that skill into the normal benchmark prompt, and runs held-out map variants.

Output is written under the source IDC run:

```text
runs/idc/<game>/<model>/<timestamp>/unseen_variants/<varN>/best_skill/...
```

Important tweak variables:

| Variable | Meaning |
| --- | --- |
| `IDC_ROOT` | Root used for automatic IDC run discovery. Default: `runs/idc`. |
| `IDC_RUN` | Optional exact IDC run directory. Empty means auto-select the latest usable run for each enabled model. |
| `MODELS` | Models to evaluate when `IDC_RUN` is empty. Only uncomment models that already have IDC runs. |
| `VARIANTS` | Held-out variants to run. Default: `var1 var2 var3`. |
| `EPISODES` | Target successful episodes per variant. Default: `5`. |
| `SKILL_ROUND` | Optional forced skill source round. Empty means auto-select by best measured score. |
| `OUTPUT_SUBDIR` | Subdirectory inside the IDC run. Default: `unseen_variants`. |
| `ARM_NAME` | Evaluation arm name. Default: `best_skill`. |
| `RECORD_VIDEO` | `1` records videos with the right-side thinking panel; set `0` if video recording causes frame drops. |
| `FLAT_OUTPUT` | `1` writes benchmark episodes directly under the variant arm directory. |
| `ALLOW_MISSING` | `1` skips models with missing IDC runs instead of failing. |
| `DRY_RUN` | `1` prints planned work without running episodes. |

Automatic IDC run selection:

1. For each enabled model, the runner scans `IDC_ROOT/<game>/<model>/`.
2. A run is a candidate only if it contains `idc_curve.json`.
3. Complete runs, detected by `round_10/round_result.json`, are preferred.
4. The newest timestamp directory is selected from the complete set, or from all candidates if no complete run exists.

For reproducible evaluation, set `IDC_RUN` explicitly:

```bash
IDC_RUN=runs/idc/last_stand/claude-opus-4-6/20260529_191934
```

When `IDC_RUN` is set, the script evaluates only that one run and infers the model from `idc_config.json` or from the parent directory name.

## Config Paths

Regular IDC configs:

```text
configs/vlm/idc/last_stand.yaml
configs/vlm/idc/shared_floor.yaml
```

Best-skill variant configs:

```text
configs/vlm/cold_start/solo/last_stand/variant_pdq_{variant}.yaml
configs/vlm/cold_start/coop/shared_floor/variant_pdq_{variant}.yaml
```

## Notes

- SharedFloor IDC is coop self-cooperation: both players use the same model and the same skill each round.
- Round 00 is staged from official PDQ baseline traces, so `PDQ_ROOT` must already contain matching PDQ outputs for the selected game and model.
- If a self-hosted model is used, deploy it first, then set `MODEL`, `IP`, and `PORT` before running the script.
