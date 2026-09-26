#!/usr/bin/env bash
set -euo pipefail

EXTENSION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$EXTENSION_ROOT/config/ku-runtime.env"
export KU_IMAGE KU_AVD_NAME KU_SNAPSHOT KU_ANDROID_API KU_BUILD_TOOLS KU_JAVA_VERSION

KU_REPO="${KU_REPO:-$(cd "$EXTENSION_ROOT/../KnowU-Bench" && pwd)}"
KU_CLI="${KU_CLI:-$KU_REPO/.venv/bin/mobile-world}"
KU_ENV_FILE="${KU_ENV_FILE:-$KU_REPO/.env}"

require_ku_runtime() {
  command -v docker >/dev/null || { echo 'docker is required' >&2; return 1; }
  command -v adb >/dev/null || { echo 'adb is required on the host' >&2; return 1; }
  [[ -x "$KU_CLI" ]] || { echo "KU CLI not found: $KU_CLI" >&2; return 1; }
  [[ -f "$KU_ENV_FILE" ]] || { echo "KU environment file not found: $KU_ENV_FILE" >&2; return 1; }
  docker image inspect "$KU_IMAGE" >/dev/null
}

validate_instance_id() {
  [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || {
    echo 'instance id may contain only letters, numbers, dot, underscore and dash' >&2
    return 1
  }
}

ku_container_name() {
  local instance_id="$1"
  printf '%s_%s_0\n' "$KU_NAME_PREFIX" "$instance_id"
}

ku_image_id() {
  docker image inspect "$KU_IMAGE" --format '{{.Id}}'
}

ku_container_status() {
  docker container inspect --format '{{.State.Status}}' "$1"
}

# Echo the host port a container publishes for one container port; fail if the
# port is not published. Docker CLI output looks like "5800/tcp -> 0.0.0.0:5800".
ku_published_port() {
  local container="$1" container_port="$2" mapping
  while IFS= read -r mapping; do
    case "$mapping" in
      "$container_port/"*) printf '%s\n' "${mapping##*:}"; return 0 ;;
    esac
  done < <(docker port "$container" 2>/dev/null)
  return 1
}

# Fail closed unless the container publishes exactly the requested host ports.
# KU CLI silently shifts to the next free port set when a port is busy, so the
# requested ports must be confirmed against the real bindings before any URL is
# printed or recorded.
ku_require_published_ports() {
  local container="$1" backend="$2" viewer="$3" vnc="$4" adb_port="$5" spec port expected label actual
  for spec in "6800:$backend:backend" "7860:$viewer:viewer" "5800:$vnc:vnc" "5556:$adb_port:adb"; do
    port="${spec%%:*}"; spec="${spec#*:}"; expected="${spec%%:*}"; label="${spec##*:}"
    if ! actual="$(ku_published_port "$container" "$port")"; then
      echo "KU container $container does not publish the $label port $port" >&2
      return 1
    fi
    if [[ "$actual" != "$expected" ]]; then
      echo "KU container $container publishes $label on host port $actual instead of the requested $expected; stop the conflicting process or pass a free port set" >&2
      return 1
    fi
  done
}

# Fail closed unless KU's own noVNC page answers on the published loopback port.
wait_for_ku_novnc() {
  local port="$1" timeout="${2:-${KU_NOVNC_READY_TIMEOUT_SECONDS:-120}}" elapsed=0
  until curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:$port/vnc.html"; do
    if (( elapsed >= timeout )); then
      echo "KU noVNC did not answer on http://127.0.0.1:$port/vnc.html within ${timeout}s" >&2
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

wait_for_ku_adb() {
  local container="$1" timeout="${2:-${KU_ADB_READY_TIMEOUT_SECONDS:-600}}" elapsed=0 status
  until docker exec "$container" adb get-state 2>/dev/null | grep -qx device; do
    if ! status="$(ku_container_status "$container" 2>/dev/null)"; then
      echo "KU container $container is gone before the emulator became ready" >&2
      return 1
    fi
    if [[ "$status" != running ]]; then
      echo "KU container $container is not running (status=$status) before the emulator became ready" >&2
      return 1
    fi
    if (( elapsed >= timeout )); then
      echo "KU emulator did not become ready within ${timeout}s" >&2
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  until [[ "$(docker exec "$container" adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; do
    if (( elapsed >= timeout )); then
      echo "KU Android boot did not complete within ${timeout}s" >&2
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
}
