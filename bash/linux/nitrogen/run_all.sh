#!/usr/bin/env bash
# Run every NitroGen game script in this directory.
# Extra args are passed through to each game script.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FAILURES=()
COUNT=0

for SCRIPT in "$SCRIPT_DIR"/run_*.sh; do
  NAME="$(basename "$SCRIPT")"
  case "$NAME" in
    run_one.sh|run_all.sh) continue ;;
  esac
  COUNT=$((COUNT + 1))
  echo
  echo "===== Running $NAME ====="
  bash "$SCRIPT" "$@"
  RC=$?
  if [ "$RC" -ne 0 ]; then
    echo "[error] $NAME failed with exit code $RC"
    FAILURES+=("$NAME")
  fi
done

echo
echo "===== NitroGen run_all complete: $COUNT script(s) ====="
if [ "${#FAILURES[@]}" -gt 0 ]; then
  FAIL_STR=""
  for F in "${FAILURES[@]}"; do
    if [ -z "$FAIL_STR" ]; then FAIL_STR="$F"; else FAIL_STR="$FAIL_STR, $F"; fi
  done
  echo "Failed: $FAIL_STR"
  exit 1
fi

echo "All scripts completed successfully."
exit 0
