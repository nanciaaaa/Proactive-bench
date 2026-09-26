#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="${1:?instance id required}"
echo 'Delegating Docker, AVD, emulator and snapshot handling to the KU runtime.'
exec "$ROOT/scripts/start-simulator.sh" "$ID" "${2:-6800}" "${3:-7860}" "${4:-5800}" "${5:-5556}" "${6:-0}"
