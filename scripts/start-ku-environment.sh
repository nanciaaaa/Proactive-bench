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
validate_instance_id "$ID"
require_ku_runtime

PREFIX="${KU_NAME_PREFIX}_${ID}"
CONTAINER="$(ku_container_name "$ID")"
RUNTIME="$ROOT/runtime/instances/$ID"
CREATED=0

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  IMAGE_REF="$(docker container inspect --format '{{.Config.Image}}' "$CONTAINER")"
  if [[ "$IMAGE_REF" != "$KU_IMAGE" ]]; then
    echo "existing container $CONTAINER runs image ${IMAGE_REF:-unknown}, not the pinned KU image $KU_IMAGE; refusing to reuse it" >&2
    exit 1
  fi
  STATE="$(ku_container_status "$CONTAINER")"
  if [[ "$STATE" != running ]]; then
    # KU's image entrypoint owns emulator and noVNC startup; reuse it as-is.
    echo "activating existing KU container $CONTAINER (status=$STATE)" >&2
    docker start "$CONTAINER" >/dev/null
  fi
else
  # Delegate container, entrypoint, emulator and noVNC startup to KU's own CLI.
  (
    cd "$KU_REPO"
    "$KU_CLI" env run \
      --count 1 \
      --name-prefix "$PREFIX" \
      --image "$KU_IMAGE" \
      --backend-start-port "$BACKEND_PORT" \
      --viewer-start-port "$VIEWER_PORT" \
      --vnc-start-port "$VNC_PORT" \
      --adb-start-port "$ADB_PORT" \
      --env-file "$KU_ENV_FILE" \
      --launch-interval 0 \
      --vnc
  )
  CREATED=1
  if ! docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "KU CLI reported success but container $CONTAINER is missing" >&2
    exit 1
  fi
fi

if ! ku_require_published_ports "$CONTAINER" "$BACKEND_PORT" "$VIEWER_PORT" "$VNC_PORT" "$ADB_PORT"; then
  if (( CREATED )); then
    (cd "$KU_REPO"; "$KU_CLI" env rm "$CONTAINER") >/dev/null 2>&1 || true
  fi
  exit 1
fi

wait_for_ku_adb "$CONTAINER"
wait_for_ku_novnc "$VNC_PORT"
mkdir -p "$RUNTIME"
python3 - "$RUNTIME/ku-environment.json" <<PY
import json, pathlib
path = pathlib.Path(__import__('sys').argv[1])
path.write_text(json.dumps({
  'container': '$CONTAINER',
  'image': '$KU_IMAGE',
  'image_id': '$(ku_image_id)',
  'avd': '$KU_AVD_NAME',
  'snapshot': '$KU_SNAPSHOT',
  'backend_port': int('$BACKEND_PORT'),
  'viewer_port': int('$VIEWER_PORT'),
  'vnc_port': int('$VNC_PORT'),
  'adb_port': int('$ADB_PORT')
}, ensure_ascii=False, indent=2) + '\n')
PY
printf 'container=%s adb=127.0.0.1:%s vnc=http://127.0.0.1:%s/vnc.html\n' "$CONTAINER" "$ADB_PORT" "$VNC_PORT"
