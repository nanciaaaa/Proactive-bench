#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"

ID="${1:?instance id required}"
COUNT="${2:-50}"
[[ "$COUNT" =~ ^[1-9][0-9]*$ ]] || { echo 'reset count must be a positive integer' >&2; exit 1; }
INFO="$ROOT/runtime/instances/$ID/ku-environment.json"
[[ -f "$INFO" ]] || { echo "missing KU environment metadata: $INFO" >&2; exit 1; }
readarray -t VALUES < <(python3 -c "import json; x=json.load(open('$INFO')); print(x['container']); print(x['adb_port'])")
CONTAINER="${VALUES[0]}"; ADB_PORT="${VALUES[1]}"; SERIAL="127.0.0.1:$ADB_PORT"
docker container inspect "$CONTAINER" >/dev/null
adb connect "$SERIAL" >/dev/null
export KU_IMAGE_ID="$(ku_image_id)"

CONTROLLER_PORT="${CONTROLLER_PORT:-34343}"
"$ROOT/scripts/start-instance.sh" "$ID" "$SERIAL" "$KU_AVD_NAME" "$CONTROLLER_PORT" "$CONTAINER"
trap '"$ROOT/scripts/stop-instance.sh" "$ID" >/dev/null 2>&1 || true' EXIT

STARTED="$(date +%s)"
for index in $(seq 1 "$COUNT"); do
  "$ROOT/scripts/reset-instance.sh" "$ID" >/dev/null
  curl -fsS "http://127.0.0.1:$CONTROLLER_PORT/health" >/dev/null
  printf 'device reset %d/%d passed\n' "$index" "$COUNT"
done
FINISHED="$(date +%s)"

python3 - "$ROOT" "$ID" "$COUNT" "$STARTED" "$FINISHED" "$CONTROLLER_PORT" <<'PY'
import json, pathlib, sys, urllib.request
root, instance_id, count, started, finished, port = sys.argv[1:]
def get(path):
 with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=20) as response:
  return json.load(response)
device=get('/local/v1/device/status')
assert device['available'] and device['boot_completed']
assert all(device['installed'].values())
assert any(f'tcp:8080 tcp:{port}' in entry for entry in device['reverse'])
for app in ('dudu','lianqi','starrail','babaquna','wangyiyun','chaowei'):
 state=get(f'/local/v1/apps/{app}/state')
 assert state['revision']==0 and not state['state']['orders']
assert get('/local/v1/faults')['faults']==[]
report={'instance_id':instance_id,'resets':int(count),'elapsed_seconds':int(finished)-int(started),'all_apps_installed':True,'boot_completed':True,'reverse_verified':True,'state_residue':False}
target=pathlib.Path(root)/'tests/results/device-reset-soak.json'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
PY
