#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../../run_python.sh" scripts/run_idc_best_skill_variants.py --game scene_escape --models claude-opus-4-6 "$@"
