#!/usr/bin/env bash
# shared_floor (coop) cold-start -- pdq
# config: configs/vlm/cold_start/coop/shared_floor/vanilla_pdq.yaml
# (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

# ========================= TWEAK HERE =========================
EPISODES=5

# How EPISODES is counted (per model self-play pair):
#   fresh = always run EPISODES NEW matches, ignore what's already there
#   topup = count finished matches already on disk, run only the missing ones
COUNT=fresh

# Record the right-side reason/action panel in episode.mp4.
#   1 = on, 0 = plain gameplay video
VIDEO_WITH_THINKING=1

# Coop self-play models -- ONE PER LINE. Default: only claude-opus-4-6.
# Each selected model is run as BOTH player1 and player2.
# Uncomment a line to also run that model.
MODELS=()
MODELS+=(claude-opus-4-6)
# MODELS+=(claude-opus-4-7)
# MODELS+=(claude-sonnet-4-6)
# MODELS+=(gpt-5.5)
# MODELS+=(gpt-5.4)
# MODELS+=(gemini-3.1-flash-lite-preview)
# MODELS+=(gemini-3.1-pro-preview)
# MODELS+=(Kimi-K2.5)

# Qwen models require a self-hosted deployment first.
# Deploy the target Qwen model, obtain its host and port, then update the coop YAML players before uncommenting.
# MODELS+=(qwen3.5-397b-a17b)
# MODELS+=(qwen3.5-122b-a10b)
# =============================================================

VIDEO_PANEL_ARGS=()
if [ "$VIDEO_WITH_THINKING" != "0" ]; then VIDEO_PANEL_ARGS=(--video-with-thinking); fi

GAME=shared_floor
OUTROOT=runs/pdq
CONFIG="configs/vlm/cold_start/coop/$GAME/vanilla_pdq.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

echo
echo "===== $GAME / coop / pdq target=$EPISODES count=$COUNT ====="
# Coop requires a same-model pair, so $M is passed as both players.
# Ports come from the config players: list (player1 -> 12345, player2 -> 12346).
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
    python scripts/run_benchmark.py --config "$CONFIG" --players "$M" "$M" --episodes "$TORUN" --live --log --record-video "${VIDEO_PANEL_ARGS[@]}" "$@"
  else
    echo "--- $M self-play : already has $HAVE of $EPISODES -- skip ---"
  fi
done
exit 0
