#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${IDLE_CPU_DISPLAY:-} != 1 ]]; then
  exec xvfb-run --auto-servernum --server-args='-screen 0 1280x720x24 -nolisten tcp' env IDLE_CPU_DISPLAY=1 bash scripts/run.sh "$@"
fi
mkdir -p .tmp
openbox >"$PWD/.tmp/openbox.log" 2>&1 &
wm_pid=$!
trap 'kill "$wm_pid" 2>/dev/null || true' EXIT
sleep 1
python3 scripts/benchmark.py "$@"
