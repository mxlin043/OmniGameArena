#!/usr/bin/env bash
# shared_floor IDC (coop self-cooperation)
# config: configs/vlm/idc/shared_floor.yaml

# ========================= TWEAK HERE =========================
MODEL=claude-opus-4-6
# MODEL=claude-opus-4-7
# MODEL=gpt-5.5
# MODEL=gemini-3.1-pro-preview

# Empty = use MODEL as the reflector model.
REFLECTOR_MODEL=

# Coop IDC uses MODEL for both players and one shared skill per round.
# PORT is player_1; player_2 automatically uses PORT + 1.
IP=127.0.0.1
PORT=12345
ROUNDS=10
EPISODES_PER_ROUND=5
PDQ_ROOT=runs/pdq
OUTPUT_ROOT=runs/idc

# Set this to an existing run directory to resume instead of starting fresh.
# Example: RESUME=runs/idc/shared_floor/claude-opus-4-7/20260530_120000
RESUME=

LIVE=1
LOG_VLM=0
API_DEBUG=0
VERBOSE=0
# =============================================================

LIVE_ARGS=()
[ "$LIVE" != "0" ] && LIVE_ARGS=(--live)

LOG_ARGS=()
[ "$LOG_VLM" != "0" ] && LOG_ARGS=(--log-vlm)

API_DEBUG_ARGS=()
[ "$API_DEBUG" != "0" ] && API_DEBUG_ARGS=(--api-debug)

VERBOSE_ARGS=()
[ "$VERBOSE" != "0" ] && VERBOSE_ARGS=(--verbose)

REFLECTOR_ARGS=()
[ -n "$REFLECTOR_MODEL" ] && REFLECTOR_ARGS=(--reflector-model "$REFLECTOR_MODEL")

PORT2=$((PORT + 1))

GAME=shared_floor
CONFIG="configs/vlm/idc/$GAME.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

if [ -n "$RESUME" ]; then
  if [ ! -f "$RESUME/idc_config.json" ]; then echo "[error] resume idc_config not found: $RESUME/idc_config.json"; exit 2; fi
  echo "===== $GAME / IDC resume / $IP:$PORT and $IP:$PORT2 ====="
  echo "resume=$RESUME"
  python scripts/run_idc.py --resume "$RESUME" --host "$IP" --port "$PORT" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VERBOSE_ARGS[@]}" "${REFLECTOR_ARGS[@]}" "$@"
else
  echo "===== $GAME / IDC coop / $MODEL self-coop / $IP:$PORT and $IP:$PORT2 rounds=$ROUNDS eps_per_round=$EPISODES_PER_ROUND ====="
  python scripts/run_idc.py --config "$CONFIG" --model "$MODEL" --host "$IP" --port "$PORT" --rounds "$ROUNDS" --episodes-per-round "$EPISODES_PER_ROUND" --pdq-root "$PDQ_ROOT" --output-root "$OUTPUT_ROOT" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VERBOSE_ARGS[@]}" "${REFLECTOR_ARGS[@]}" "$@"
fi
exit $?
