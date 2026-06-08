#!/usr/bin/env bash
# Run one standard NitroGen solo benchmark config from the repo root.
# Usage:
#   bash/linux/nitrogen/run_one.sh obstacle_run_3d [extra run_benchmark args...]

usage() {
  echo "Usage:"
  echo "  bash/linux/nitrogen/run_one.sh GAME [extra run_benchmark args...]"
  echo
  echo "Games:"
  echo "  obstacle_run_3d"
  echo "  obstacle_run_2d"
  echo "  last_stand"
  echo "  monster_shoot"
  echo "  cue_chase"
  echo "  scene_escape"
  echo "  solo_craft"
  exit 2
}

if [ -z "${1:-}" ]; then usage; fi

GAME="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../../.." || exit 1

: "${IP:=127.0.0.1}"
: "${PORT:=12345}"
: "${EPISODES:=5}"

CONFIG="configs/nitrogen/$GAME.yaml"
if [ ! -f "$CONFIG" ]; then
  echo "[error] Config not found: $CONFIG"
  echo
  usage
fi

echo
echo "===== NitroGen: $GAME ====="
echo "config=$CONFIG"
echo "host=$IP port=$PORT"
echo "standard=$EPISODES episode(s), live, log, gameplay video recording"
python scripts/run_benchmark.py \
  --config "$CONFIG" \
  --host "$IP" \
  --port "$PORT" \
  --episodes "$EPISODES" \
  --live \
  --log \
  --record-video \
  --video-with-thinking \
  "$@"
exit $?
