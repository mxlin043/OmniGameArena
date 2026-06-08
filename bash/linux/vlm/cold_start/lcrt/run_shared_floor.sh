#!/usr/bin/env bash
# shared_floor (coop) cold-start -- lcrt
# config: configs/vlm/cold_start/coop/shared_floor/vanilla_lcrt.yaml
# (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

# ========================= TWEAK HERE =========================
EPISODES=5

# How EPISODES is counted (per model self-play pair):
#   fresh = always run EPISODES NEW matches, ignore what's already there
#   topup = count existing finished matches and only run the missing ones
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
# MODELS+=(claude-sonnet-4-6)
# MODELS+=(gpt-5.5)
# MODELS+=(gpt-5.4)

# =============================================================

VIDEO_PANEL_ARGS=()
if [ "$VIDEO_WITH_THINKING" != "0" ]; then VIDEO_PANEL_ARGS=(--video-with-thinking); fi

GAME=shared_floor
OUTROOT=runs/lcrt
CONFIG="configs/vlm/cold_start/coop/$GAME/vanilla_lcrt.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

echo "===== $GAME / coop / lcrt / $IP:$PORT target=$EPISODES count=$COUNT ====="
for M in "${MODELS[@]}"; do
  CELL="$OUTROOT/$GAME/player1-${M}_vs_player2-${M}"
  HAVE=0
  if [ -d "$CELL" ]; then
    for E in "$CELL"/*/; do
      [ -f "${E}match_result.json" ] && HAVE=$((HAVE + 1))
    done
  fi
  if [ "${COUNT,,}" = "topup" ]; then TORUN=$((EPISODES - HAVE)); else TORUN=$EPISODES; fi
  echo
  if [ "$TORUN" -gt 0 ]; then
    echo "--- $M self-play : have $HAVE, running $TORUN more ---"
    python scripts/run_benchmark.py --config "$CONFIG" --host "$IP" --port "$PORT" --players "$M" "$M" --episodes "$TORUN" --live --log --record-video "${VIDEO_PANEL_ARGS[@]}" "$@"
  else
    echo "--- $M self-play : already has $HAVE of $EPISODES -- skip ---"
  fi
done
exit 0
