#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"
ID="${1:?instance id required}"
validate_instance_id "$ID"
INFO="$ROOT/runtime/instances/$ID/ku-environment.json"
[[ -f "$INFO" ]] || { echo "KU environment is not running for instance $ID; run scripts/start-simulator.sh $ID first" >&2; exit 1; }
readarray -t VALUES < <(python3 -c "import json; x=json.load(open('$INFO')); print(x['container']); print(x['vnc_port'])")
CONTAINER="${VALUES[0]}"; VNC_PORT="${VALUES[1]}"
docker container inspect "$CONTAINER" >/dev/null
wait_for_ku_novnc "$VNC_PORT"
echo "noVNC is owned by KU: http://127.0.0.1:$VNC_PORT/vnc.html"
