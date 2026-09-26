#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="${1:?instance id required}"; SERIAL="${2:-}"; AVD="${3:-}"; PORT="${4:-0}"; KU_CONTAINER="${5:-}"
RUNTIME="$ROOT/runtime/instances/$ID"; mkdir -p "$RUNTIME/logs"
if [[ -f "$RUNTIME/controller.pid" ]]; then
  OLD_PID="$(cat "$RUNTIME/controller.pid")"
  OLD_CMD=""
  if [[ -r "/proc/$OLD_PID/cmdline" ]]; then OLD_CMD="$(tr '\0' ' ' <"/proc/$OLD_PID/cmdline")"; fi
  if kill -0 "$OLD_PID" 2>/dev/null && {
    [[ "$OLD_CMD" == *"local-controller/server.py"* && "$OLD_CMD" == *"--instance-id $ID"* ]] ||
    [[ "$OLD_CMD" == *"controller-supervisor.sh"* && "$OLD_CMD" == *" $ID "* ]]
  }; then
    echo "instance already running" >&2
    exit 1
  fi
  rm -f "$RUNTIME/controller.pid"
fi
KU_IMAGE_VALUE="${KU_IMAGE:-}"; KU_SNAPSHOT_VALUE="${KU_SNAPSHOT:-init_state}"; KU_IMAGE_ID_VALUE="${KU_IMAGE_ID:-}"
if [[ -z "$KU_IMAGE_ID_VALUE" && -n "$KU_CONTAINER" ]]; then
  KU_IMAGE_ID_VALUE="$(docker inspect --format '{{.Image}}' "$KU_CONTAINER")"
fi
if [[ "$PORT" == 0 ]]; then
  PORT="$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
fi
rm -f "$RUNTIME/controller.stop"
nohup setsid "$ROOT/scripts/controller-supervisor.sh" "$ID" --runtime "$RUNTIME" --port "$PORT" --adb-serial "$SERIAL" --avd "$AVD" --snapshot "$KU_SNAPSHOT_VALUE" --ku-container "$KU_CONTAINER" --ku-image "$KU_IMAGE_VALUE" --ku-image-id "$KU_IMAGE_ID_VALUE" --baseline-version "${BASELINE_VERSION:-$KU_IMAGE_ID_VALUE}" </dev/null >"$RUNTIME/logs/controller.stdout" 2>"$RUNTIME/logs/controller.stderr" &
PID=$!; echo "$PID" >"$RUNTIME/controller.pid"
for _ in $(seq 1 100); do
  if [[ -s "$RUNTIME/logs/controller.stdout" ]] && curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  kill -0 "$PID" 2>/dev/null || { echo "controller failed to start" >&2; exit 1; }
  sleep .1
done
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null
HOST_PORT="$(python3 -c "import json; print(json.load(open('$RUNTIME/instance.json'))['controller_port'])")"
if [[ -n "$SERIAL" ]] && adb devices | grep -q "^$SERIAL[[:space:]]"; then "$ROOT/scripts/ensure-adb-reverse.sh" "$SERIAL" "$HOST_PORT"; fi
echo "instance=$ID pid=$PID port=$HOST_PORT console=http://127.0.0.1:$HOST_PORT/console/"
