#!/usr/bin/env bash
# midline_clash (pvp) cold-start -- lcrt
# config: configs/vlm/cold_start/pvp/midline_clash/vanilla_lcrt.yaml
# (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

# ========================= TWEAK HERE =========================
EPISODES=5

# How EPISODES is counted (per pairing):
#   fresh = always run EPISODES NEW matches, ignore what's already there
#   topup = count finished matches already on disk, run only the missing ones
COUNT=fresh

# Record the right-side reason/action panel in episode.mp4.
#   1 = on, 0 = plain gameplay video
VIDEO_WITH_THINKING=1

# Two-player pairing. P1 = player1, P2 = player2 (1-indexed -- exactly what
# shows up in the output dirs player1-.../player2-... and what you think in).
# The run loops EVERY P1 x EVERY P2, but skips same-model matchups.
# Any model works (even one the YAML never listed).

# --- Player 1 models -- ONE PER LINE. (uncomment to add; # to drop.) ---
P1=()
P1+=(claude-opus-4-6)
# P1+=(claude-sonnet-4-6)
# P1+=(gpt-5.5)
# P1+=(gpt-5.4)

# --- Player 2 models -- ONE PER LINE. (uncomment to add; # to drop.) ---
P2=()
P2+=(claude-opus-4-6)
P2+=(claude-sonnet-4-6)
# P2+=(gpt-5.5)
# P2+=(gpt-5.4)

# =============================================================

VIDEO_PANEL_ARGS=()
if [ "$VIDEO_WITH_THINKING" != "0" ]; then VIDEO_PANEL_ARGS=(--video-with-thinking); fi

GAME=midline_clash
OUTROOT=runs/lcrt
CONFIG="configs/vlm/cold_start/pvp/$GAME/vanilla_lcrt.yaml"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../../.." || exit 1
if [ ! -f "$CONFIG" ]; then echo "[error] config not found: $CONFIG"; exit 2; fi

echo
echo "===== $GAME / pvp / lcrt target=$EPISODES count=$COUNT ====="
# $A = this round's player1 model, $B = player2 model. They map to
# run_benchmark's positional --players (player1 first, player2 second);
# you never touch python's 0-indexing here. Ports come from the config
# players: list (player1 -> 12345, player2 -> 12346).
for A in "${P1[@]}"; do
  for B in "${P2[@]}"; do
    echo
    if [ "${A,,}" = "${B,,}" ]; then
      echo "--- player1=$A vs player2=$B : same model -- skip ---"
    else
      CELL="$OUTROOT/$GAME/player1-${A}_vs_player2-${B}"
      HAVE=0
      if [ -d "$CELL" ]; then
        for E in "$CELL"/*/; do
          [ -f "${E}player_1/result.json" ] && HAVE=$((HAVE + 1))
        done
      fi
      if [ "${COUNT,,}" = "topup" ]; then TORUN=$((EPISODES - HAVE)); else TORUN=$EPISODES; fi
      if [ "$TORUN" -gt 0 ]; then
        echo "--- player1=$A vs player2=$B : have $HAVE, running $TORUN more ---"
        python scripts/run_benchmark.py --config "$CONFIG" --players "$A" "$B" --episodes "$TORUN" --live --log --record-video "${VIDEO_PANEL_ARGS[@]}" "$@"
      else
        echo "--- player1=$A vs player2=$B : already has $HAVE of $EPISODES -- skip ---"
      fi
    fi
  done
done
exit 0
