#!/usr/bin/env bash
# shared_floor held-out variants with each model's best measured IDC skill.
# Coop self-cooperation uses the same model and best skill for both players.

# ========================= TWEAK HERE =========================
IP=127.0.0.1
PORT=12345
EPISODES=5
VARIANTS=(var1 var2 var3)

# Empty = auto-select the best measured IDC round.
# Example: SKILL_ROUND=5 uses round_05/skill_out.md.
SKILL_ROUND=

IDC_ROOT=runs/idc
# Empty = auto-select latest run under IDC_ROOT/shared_floor/<model>.
# Set this to one exact IDC run directory for reproducible variant eval.
# Example: IDC_RUN=runs/idc/shared_floor/claude-opus-4-7/20260530_120000
IDC_RUN=
OUTPUT_SUBDIR=unseen_variants
ARM_NAME=best_skill

# Models to evaluate -- ONE PER LINE.
MODELS=()
MODELS+=(claude-opus-4-6)
# MODELS+=(claude-opus-4-7)
# MODELS+=(gpt-5.5)
# MODELS+=(gemini-3.1-pro-preview)


# PORT is player_1; player_2 uses PORT + 1 unless PORT_P2 is changed.
PORT_P2=
LIVE=1
LOG=1
API_DEBUG=1
# The Python runner records video with the right-side thinking panel when RECORD_VIDEO=1.
RECORD_VIDEO=1
FLAT_OUTPUT=0
ALLOW_MISSING=0
DRY_RUN=0
# =============================================================

CONFIG_PATTERN="configs/vlm/cold_start/coop/shared_floor/variant_pdq_{variant}.yaml"

[ -z "$PORT_P2" ] && PORT_P2=$((PORT + 1))

SKILL_ROUND_ARGS=()
[ -n "$SKILL_ROUND" ] && SKILL_ROUND_ARGS=(--skill-round "$SKILL_ROUND")

LIVE_ARGS=()
[ "$LIVE" = "0" ] && LIVE_ARGS=(--no-live)

LOG_ARGS=()
[ "$LOG" = "0" ] && LOG_ARGS=(--no-log)

API_DEBUG_ARGS=()
[ "$API_DEBUG" = "0" ] && API_DEBUG_ARGS=(--no-api-debug)

VIDEO_ARGS=()
[ "$RECORD_VIDEO" = "0" ] && VIDEO_ARGS=(--no-video)

FLAT_ARGS=()
[ "$FLAT_OUTPUT" != "0" ] && FLAT_ARGS=(--flat-output)

ALLOW_ARGS=()
[ "$ALLOW_MISSING" != "0" ] && ALLOW_ARGS=(--allow-missing)

DRY_RUN_ARGS=()
[ "$DRY_RUN" != "0" ] && DRY_RUN_ARGS=(--dry-run)

GAME=shared_floor
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../.." || exit 1
if [ ! -f "scripts/run_idc_best_skill_variants.py" ]; then echo "[error] runner not found: scripts/run_idc_best_skill_variants.py"; exit 2; fi
if [ ! -f "configs/vlm/cold_start/coop/shared_floor/variant_pdq_var1.yaml" ]; then echo "[error] variant config not found for $GAME"; exit 2; fi

echo "===== $GAME / IDC best skill variants / $IP:$PORT and $IP:$PORT_P2 episodes=$EPISODES variants=${VARIANTS[*]} ====="
if [ -n "$IDC_RUN" ]; then
  echo "idc_run=$IDC_RUN"
  python scripts/run_idc_best_skill_variants.py --game "$GAME" --idc-run "$IDC_RUN" --config-pattern "$CONFIG_PATTERN" --variants "${VARIANTS[@]}" --episodes "$EPISODES" --host "$IP" --port "$PORT" --port-p2 "$PORT_P2" --output-subdir "$OUTPUT_SUBDIR" --arm-name "$ARM_NAME" "${SKILL_ROUND_ARGS[@]}" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VIDEO_ARGS[@]}" "${FLAT_ARGS[@]}" "${ALLOW_ARGS[@]}" "${DRY_RUN_ARGS[@]}" "$@"
else
  echo "idc_root=$IDC_ROOT"
  echo "models=${MODELS[*]}"
  python scripts/run_idc_best_skill_variants.py --game "$GAME" --idc-root "$IDC_ROOT" --config-pattern "$CONFIG_PATTERN" --models "${MODELS[@]}" --variants "${VARIANTS[@]}" --episodes "$EPISODES" --host "$IP" --port "$PORT" --port-p2 "$PORT_P2" --output-subdir "$OUTPUT_SUBDIR" --arm-name "$ARM_NAME" "${SKILL_ROUND_ARGS[@]}" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VIDEO_ARGS[@]}" "${FLAT_ARGS[@]}" "${ALLOW_ARGS[@]}" "${DRY_RUN_ARGS[@]}" "$@"
fi
exit $?
