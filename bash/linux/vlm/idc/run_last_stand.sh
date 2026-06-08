#!/usr/bin/env bash
# last_stand IDC
# config: configs/vlm/idc/last_stand.yaml

# ========================= TWEAK HERE =========================
# MODEL=claude-opus-4-6
MODEL=claude-opus-4-7
# MODEL=gpt-5.5
# MODEL=gemini-3.1-pro-preview

# Empty = use MODEL as the reflector model.
REFLECTOR_MODEL=

IP=127.0.0.1
PORT=12345
ROUNDS=10
EPISODES_PER_ROUND=5
PDQ_ROOT=runs/pdq
OUTPUT_ROOT=runs/idc

# Set this to an existing run directory to resume instead of starting fresh.
# Example: RESUME=runs/idc/last_stand/claude-opus-4-7/20260530_120000
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

GAME=last_stand
CONFIG="configs/vlm/idc/$GAME.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

if [ -n "$RESUME" ]; then
  if [ ! -f "$RESUME/idc_config.json" ]; then echo "[error] resume idc_config not found: $RESUME/idc_config.json"; exit 2; fi
  echo "===== $GAME / IDC resume / $IP:$PORT ====="
  echo "resume=$RESUME"
  python scripts/run_idc.py --resume "$RESUME" --host "$IP" --port "$PORT" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VERBOSE_ARGS[@]}" "${REFLECTOR_ARGS[@]}" "$@"
else
  echo "===== $GAME / IDC / $MODEL / $IP:$PORT rounds=$ROUNDS eps_per_round=$EPISODES_PER_ROUND ====="
  python scripts/run_idc.py --config "$CONFIG" --model "$MODEL" --host "$IP" --port "$PORT" --rounds "$ROUNDS" --episodes-per-round "$EPISODES_PER_ROUND" --pdq-root "$PDQ_ROOT" --output-root "$OUTPUT_ROOT" "${LIVE_ARGS[@]}" "${LOG_ARGS[@]}" "${API_DEBUG_ARGS[@]}" "${VERBOSE_ARGS[@]}" "${REFLECTOR_ARGS[@]}" "$@"
fi
exit $?
