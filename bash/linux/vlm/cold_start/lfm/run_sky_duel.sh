#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../../../run_python.sh" scripts/run_cold_start.py --game sky_duel --clock lfm --models claude-opus-4-6 --opponents gpt-5.5 "$@"
