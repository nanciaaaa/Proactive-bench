#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTANCE="${1:?instance id required}"
shift

trap '[[ -n "${CHILD:-}" ]] && kill "$CHILD" 2>/dev/null || true; exit 0' INT TERM HUP
while :; do
  python3 "$ROOT/local-controller/server.py" --instance-id "$INSTANCE" "$@" &
  CHILD=$!
  wait "$CHILD" || true
  [[ -f "$ROOT/runtime/instances/$INSTANCE/controller.stop" ]] && exit 0
  sleep 1
done
