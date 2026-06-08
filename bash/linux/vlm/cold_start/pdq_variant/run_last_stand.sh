#!/usr/bin/env bash
# last_stand (solo) cold-start -- pdq_variant
# config: configs/vlm/cold_start/solo/last_stand/variant_pdq.yaml
# (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

# ========================= TWEAK HERE =========================
EPISODES=5

# How EPISODES is counted (per model):
#   fresh = always run EPISODES NEW episodes, ignore what's already there
#   topup = count existing finished episodes and only run the missing ones
COUNT=fresh

# Record the right-side reason/action panel in episode.mp4.
#   1 = on, 0 = plain gameplay video
VIDEO_WITH_THINKING=1

IP=127.0.0.1
PORT=12345

# Models to run -- ONE PER LINE. Default: only claude-opus-4-6.
# Uncomment a line to also run that model.
MODELS=()
MODELS+=(claude-opus-4-6)
# MODELS+=(claude-opus-4-7)
# MODELS+=(gpt-5.5)
# MODELS+=(gemini-3.1-pro-preview)

# =============================================================

VIDEO_PANEL_ARGS=()
if [ "$VIDEO_WITH_THINKING" != "0" ]; then VIDEO_PANEL_ARGS=(--video-with-thinking); fi

GAME=last_stand
OUTROOT=runs/pdq_variant
CONFIG="configs/vlm/cold_start/solo/$GAME/variant_pdq.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

echo "===== $GAME / pdq_variant / $IP:$PORT target=$EPISODES count=$COUNT ====="
for M in "${MODELS[@]}"; do
  CELL="$OUTROOT/$GAME/$M"
  HAVE=0
  if [ -d "$CELL" ]; then
    for E in "$CELL"/*/; do
      [ -f "${E}result.json" ] && HAVE=$((HAVE + 1))
    done
  fi
  if [ "${COUNT,,}" = "topup" ]; then TORUN=$((EPISODES - HAVE)); else TORUN=$EPISODES; fi
  echo
  if [ "$TORUN" -gt 0 ]; then
    echo "--- $M : have $HAVE, running $TORUN more ---"
    python scripts/run_benchmark.py --config "$CONFIG" --host "$IP" --port "$PORT" --episodes "$TORUN" --include "$M" --live --log --record-video "${VIDEO_PANEL_ARGS[@]}" "$@"
  else
    echo "--- $M : already has $HAVE of $EPISODES -- skip ---"
  fi
done
exit 0
