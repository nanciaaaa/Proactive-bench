#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; ID="${1:?instance id required}"; FILE="$ROOT/runtime/instances/$ID/controller.pid"
[[ -f "$FILE" ]] || exit 0
PID="$(cat "$FILE")"
CMD=""
if [[ -r "/proc/$PID/cmdline" ]]; then CMD="$(tr '\0' ' ' <"/proc/$PID/cmdline")"; fi
if ! {
  [[ "$CMD" == *"controller-supervisor.sh"* && "$CMD" == *" $ID "* ]] ||
  [[ "$CMD" == *"local-controller/server.py"* && "$CMD" == *"--instance-id $ID"* ]]
}; then
  echo "stale controller pid file ignored: $PID" >&2
  rm -f "$FILE"
  exit 0
fi
touch "$ROOT/runtime/instances/$ID/controller.stop"
if [[ -f "$ROOT/runtime/instances/$ID/instance.json" ]]; then
  PORT="$(python3 -c "import json; print(json.load(open('$ROOT/runtime/instances/$ID/instance.json'))['controller_port'])")"
  RUNNER_KEY="$(python3 -c "import json; print(json.load(open('$ROOT/runtime/instances/$ID/instance.json'))['runner_key'])")"
  curl -fsS -X POST -H 'Content-Type: application/json' -H "X-Runner-Key: $RUNNER_KEY" -d '{"state":"STOPPED"}' "http://127.0.0.1:$PORT/local/v1/instance/lifecycle" >/dev/null 2>&1 || true
fi
kill "$PID" 2>/dev/null || true
for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep .1; done
rm -f "$FILE"
