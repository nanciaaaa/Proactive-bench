#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"

ID="${1:?instance id required}"
validate_instance_id "$ID"
CONTAINER="$(ku_container_name "$ID")"
"$ROOT/scripts/stop-instance.sh" "$ID"
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  (
    cd "$KU_REPO"
    "$KU_CLI" env rm "$CONTAINER"
  )
fi
