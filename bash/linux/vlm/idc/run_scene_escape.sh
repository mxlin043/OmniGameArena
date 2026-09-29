#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../../run_python.sh" scripts/run_idc.py --config configs/vlm/idc/scene_escape.yaml "$@"
