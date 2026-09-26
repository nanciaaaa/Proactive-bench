#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"

ID="${1:?instance id required}"
BACKEND_PORT="${2:-$KU_BACKEND_PORT}"
VIEWER_PORT="${3:-$KU_VIEWER_PORT}"
VNC_PORT="${4:-$KU_VNC_PORT}"
ADB_PORT="${5:-$KU_ADB_PORT}"
CONTROLLER_PORT="${6:-0}"
validate_instance_id "$ID"

for app in dudu lianqi starrail babaquna wangyiyun chaowei; do
  [[ -f "$ROOT/dist/apks/$app-debug.apk" ]] || {
    echo "missing APK: dist/apks/$app-debug.apk; run scripts/build-apks.sh first" >&2
    exit 1
  }
done

# No online ADB device is required: KU CLI creates the emulator, and an already
# existing KU container is activated or reused instead of duplicated.
"$ROOT/scripts/start-ku-environment.sh" "$ID" "$BACKEND_PORT" "$VIEWER_PORT" "$VNC_PORT" "$ADB_PORT"
CONTAINER="$(ku_container_name "$ID")"
SERIAL="127.0.0.1:$ADB_PORT"
RUNTIME="$ROOT/runtime/instances/$ID"
cleanup() {
  "$ROOT/scripts/stop-ku-environment.sh" "$ID" >/dev/null 2>&1 || true
}
trap cleanup ERR

adb connect "$SERIAL" >/dev/null
export KU_IMAGE_ID="$(ku_image_id)"
export KU_IMAGE KU_SNAPSHOT
if [[ -f "$RUNTIME/instance.json" ]]; then
  # Reuse a healthy Controller; otherwise recover it on its recorded port.
  "$ROOT/scripts/ensure-controller.sh" "$ID"
else
  "$ROOT/scripts/start-instance.sh" "$ID" "$SERIAL" "$KU_AVD_NAME" "$CONTROLLER_PORT" "$CONTAINER"
fi
"$ROOT/scripts/reset-instance.sh" "$ID"
# Fail closed: never print URLs unless KU's own noVNC page answers on loopback.
wait_for_ku_novnc "$VNC_PORT"
trap - ERR

PORT="$(python3 -c "import json; print(json.load(open('$RUNTIME/instance.json'))['controller_port'])")"
printf 'simulator=%s controller=http://127.0.0.1:%s/console/ vnc=http://127.0.0.1:%s/vnc.html ku_backend=http://127.0.0.1:%s\n' \
  "$ID" "$PORT" "$VNC_PORT" "$BACKEND_PORT"
