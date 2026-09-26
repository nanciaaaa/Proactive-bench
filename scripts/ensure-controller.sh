#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="${1:?instance id required}"
RUNTIME="$ROOT/runtime/instances/$ID"
INFO="$RUNTIME/instance.json"
[[ -f "$INFO" ]] || { echo "missing $INFO" >&2; exit 1; }
mkdir -p "$RUNTIME/logs"
exec 9>"$RUNTIME/controller-recovery.lock"
flock 9
readarray -t VALUES < <(python3 -c "import json; x=json.load(open('$INFO')); print(x['controller_port']); print(x.get('adb_serial','')); print(x.get('avd','')); print(x.get('snapshot','init_state')); print(x.get('ku_container','')); print(x.get('ku_image','')); print(x.get('ku_image_id','')); print(x.get('baseline_version','unknown'))")
PORT="${VALUES[0]}"; SERIAL="${VALUES[1]}"; AVD="${VALUES[2]}"; SNAPSHOT="${VALUES[3]}"
KU_CONTAINER="${VALUES[4]}"; KU_IMAGE="${VALUES[5]}"; KU_IMAGE_ID="${VALUES[6]}"; BASELINE_VERSION="${VALUES[7]}"
API="http://127.0.0.1:$PORT"
if curl -fsS "$API/health" >/dev/null 2>&1; then exit 0; fi
rm -f "$RUNTIME/controller.stop"
PID_FILE="$RUNTIME/controller.pid"
if [[ -s "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  CMD=""
  [[ -r "/proc/$PID/cmdline" ]] && CMD="$(tr '\0' ' ' <"/proc/$PID/cmdline")"
  if [[ "$CMD" == *"controller-supervisor.sh"* && "$CMD" == *" $ID "* ]]; then
    for _ in $(seq 1 30); do
      curl -fsS "$API/health" >/dev/null 2>&1 && exit 0
      sleep .2
    done
  fi
fi
nohup setsid "$ROOT/scripts/controller-supervisor.sh" "$ID" \
  --runtime "$RUNTIME" --port "$PORT" --adb-serial "$SERIAL" --avd "$AVD" \
  --snapshot "$SNAPSHOT" --ku-container "$KU_CONTAINER" --ku-image "$KU_IMAGE" \
  --ku-image-id "$KU_IMAGE_ID" --baseline-version "$BASELINE_VERSION" \
  9>&- </dev/null >>"$RUNTIME/logs/controller.stdout" 2>>"$RUNTIME/logs/controller.stderr" &
echo "$!" >"$PID_FILE"
for _ in $(seq 1 100); do
  curl -fsS "$API/health" >/dev/null 2>&1 && exit 0
  sleep .2
done
echo "controller failed to become healthy on port $PORT" >&2
exit 1
