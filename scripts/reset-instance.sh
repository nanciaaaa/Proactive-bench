#!/usr/bin/env bash
# External KU-instance reset. One wall-clock deadline bounds every step so the
# caller (gui_capture uses a 300s subprocess timeout) never kills a live reset
# and every failure names the exact phase in runtime/instances/<id>/reset.log.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; ID="${1:?instance id required}"
RUNTIME="$ROOT/runtime/instances/$ID"; INFO="$RUNTIME/instance.json"
[[ -f "$INFO" ]] || { echo "missing $INFO" >&2; exit 1; }
readarray -t VALUES < <(python3 -c "import json; x=json.load(open('$INFO')); print(x['adb_serial']); print(x['controller_port']); print(x.get('ku_container','')); print(x.get('snapshot','init_state'))")
SERIAL="${VALUES[0]}"; PORT="${VALUES[1]}"; KU_CONTAINER="${VALUES[2]}"; SNAPSHOT="${VALUES[3]}"; API="http://127.0.0.1:$PORT"
RUNNER_KEY="$(python3 -c "import json; print(json.load(open('$INFO'))['runner_key'])")"
ADB_TIMEOUT="${ADB_RESET_TIMEOUT_SECONDS:-120}"
# Internal budget must stay below the caller timeout (gui_capture: 300s).
RESET_TOTAL_BUDGET_SECONDS="${RESET_TOTAL_BUDGET_SECONDS:-240}"
BOOT_LIMIT=180
RESET_START="$(date +%s)"
DEADLINE=$((RESET_START + RESET_TOTAL_BUDGET_SECONDS))
mkdir -p "$RUNTIME"; LOG="$RUNTIME/reset.log"; : >"$LOG"
PHASE="bootstrap"; PHASE_START="$RESET_START"
log(){ printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }
phase_begin(){ PHASE="$1"; PHASE_START="$(date +%s)"; log "phase=$PHASE status=start budget_remaining=$((DEADLINE - $(date +%s)))s"; }
phase_end(){ log "phase=$PHASE status=$1 duration=$(( $(date +%s) - PHASE_START ))s budget_remaining=$((DEADLINE - $(date +%s)))s"; }
run_step(){
  local name="$1"; shift
  if [[ "${1:-}" == "--" ]]; then shift; fi
  local remaining limit code
  PHASE="$name"
  remaining=$((DEADLINE - $(date +%s)))
  if (( remaining <= 0 )); then
    log "phase=$name status=failed reason=budget_exhausted elapsed=$(( $(date +%s) - RESET_START ))s"
    echo "reset budget ${RESET_TOTAL_BUDGET_SECONDS}s exhausted before phase=$name" >&2
    return 124
  fi
  limit="$ADB_TIMEOUT"
  if (( limit > remaining )); then limit="$remaining"; fi
  phase_begin "$name"
  if timeout --kill-after=10s "$limit" "$@"; then
    phase_end ok
  else
    code=$?
    if (( code == 1 )) && [[ "$name" == *-pm-path ]]; then
      phase_end missing
      return 0
    fi
    phase_end "failed code=$code step_limit=${limit}s"
    echo "phase=$name failed (code=$code, step limit ${limit}s, budget ${RESET_TOTAL_BUDGET_SECONDS}s)" >&2
    return "$code"
  fi
}
fail(){
  local code=$?
  log "reset status=failed phase=$PHASE exit_code=$code elapsed=$(( $(date +%s) - RESET_START ))s budget=${RESET_TOTAL_BUDGET_SECONDS}s"
  echo "reset failed at phase=$PHASE (exit=$code, elapsed=$(( $(date +%s) - RESET_START ))s of ${RESET_TOTAL_BUDGET_SECONDS}s budget); see $LOG" >&2
  CURRENT="$(curl --max-time 5 -fsS "$API/health" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("lifecycle",""))' 2>/dev/null || true)"
  if [[ "$CURRENT" != "CONTAMINATED" ]]; then
    curl --max-time 5 -fsS -X POST -H 'Content-Type: application/json' -H "X-Runner-Key: $RUNNER_KEY" -d '{"state":"FAILED"}' "$API/local/v1/instance/lifecycle" >/dev/null 2>&1 || true
    echo "reset failed; instance marked FAILED" >&2
  else
    echo "reset failed; instance remains CONTAMINATED" >&2
  fi
}
trap fail ERR
run_step controller-ensure -- "$ROOT/scripts/ensure-controller.sh" "$ID"
if [[ "$SERIAL" == *:* ]]; then run_step adb-connect -- adb connect "$SERIAL" >/dev/null; fi
run_step lifecycle-resetting -- curl -fsS -X POST -H 'Content-Type: application/json' -H "X-Runner-Key: $RUNNER_KEY" -d '{"state":"RESETTING"}' "$API/local/v1/instance/lifecycle" >/dev/null
for PKG in com.ku.simulator.dudu com.ku.simulator.lianqi com.ku.simulator.starrail com.ku.simulator.babaquna com.ku.simulator.wangyiyun com.ku.simulator.chaowei; do
  run_step "force-stop-${PKG##*.}" -- adb -s "$SERIAL" shell am force-stop "$PKG"
done
if [[ -n "$KU_CONTAINER" ]]; then
  # KU owns the emulator console. Use its existing adb and snapshot policy.
  run_step snapshot-load -- docker exec "$KU_CONTAINER" adb emu avd snapshot load "$SNAPSHOT"
else
  run_step snapshot-load -- adb -s "$SERIAL" emu avd snapshot load "$SNAPSHOT"
fi
if [[ "$SERIAL" == *:* ]]; then run_step adb-reconnect -- adb connect "$SERIAL" >/dev/null; fi
run_step device-wait -- adb -s "$SERIAL" wait-for-device
PHASE="boot-wait"; PHASE_START="$(date +%s)"
log "phase=boot-wait status=start boot_limit=${BOOT_LIMIT}s budget_remaining=$((DEADLINE - $(date +%s)))s"
BOOT_DEADLINE=$(( $(date +%s) + BOOT_LIMIT ))
if (( BOOT_DEADLINE > DEADLINE )); then BOOT_DEADLINE="$DEADLINE"; fi
while :; do
  remaining=$((BOOT_DEADLINE - $(date +%s)))
  if (( remaining <= 0 )); then
    log "phase=boot-wait status=failed reason=boot_timeout boot_limit=${BOOT_LIMIT}s elapsed=$(( $(date +%s) - PHASE_START ))s"
    echo "phase=boot-wait failed: sys.boot_completed != 1 within ${BOOT_LIMIT}s (budget ${RESET_TOTAL_BUDGET_SECONDS}s)" >&2
    false
  fi
  step_limit=$(( remaining < 10 ? remaining : 10 ))
  BOOT_STATE="$(timeout --kill-after=5s "$step_limit" adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [[ "$BOOT_STATE" == 1 ]]; then phase_end ok; break; fi
  sleep 1
done
for APP in dudu lianqi starrail babaquna wangyiyun chaowei; do
  APK="$ROOT/dist/apks/$APP-debug.apk"; [[ -f "$APK" ]]
  LOCAL_HASH="$(sha256sum "$APK" | cut -d' ' -f1)"
  PHASE="$APP-pm-path"
  REMOTE_RAW="$(run_step "$PHASE" -- adb -s "$SERIAL" shell pm path "com.ku.simulator.$APP")"
  REMOTE="$(printf '%s\n' "$REMOTE_RAW" | head -n1 | tr -d '\r' | cut -d: -f2-)"
  if [[ -n "$REMOTE" ]]; then
    PHASE="$APP-remote-hash"
    REMOTE_HASH="$(run_step "$PHASE" -- adb -s "$SERIAL" shell sha256sum "$REMOTE")"
    [[ "${REMOTE_HASH%% *}" == "$LOCAL_HASH" ]] && continue
  fi
  if ! run_step "install-$APP" -- adb -s "$SERIAL" install -r "$APK" >/dev/null; then
    echo "APK install timed out or failed: $APP" >&2
    false
  fi
done
run_step controller-reset -- curl -fsS -X POST -H 'Content-Type: application/json' -H "X-Runner-Key: $RUNNER_KEY" -d '{}' "$API/local/v1/instance/reset" >/dev/null
run_step adb-reverse -- "$ROOT/scripts/ensure-adb-reverse.sh" "$SERIAL" "$PORT"
for APP in dudu lianqi starrail babaquna wangyiyun chaowei; do
  PKG="com.ku.simulator.$APP"; PHASE="$APP-verify-path"
  REMOTE_RAW="$(run_step "$PHASE" -- adb -s "$SERIAL" shell pm path "$PKG")"
  REMOTE="$(printf '%s\n' "$REMOTE_RAW" | head -n1 | tr -d '\r' | cut -d: -f2-)"
  PHASE="verify-$APP"; [[ -n "$REMOTE" ]]
  LOCAL_HASH="$(sha256sum "$ROOT/dist/apks/$APP-debug.apk" | cut -d' ' -f1)"
  PHASE="$APP-verify-hash"; REMOTE_HASH="$(run_step "$PHASE" -- adb -s "$SERIAL" shell sha256sum "$REMOTE")"
  REMOTE_HASH="${REMOTE_HASH%% *}"
  PHASE="verify-$APP"; [[ "$LOCAL_HASH" == "$REMOTE_HASH" ]]
done
trap - ERR
log "reset status=ok elapsed=$(( $(date +%s) - RESET_START ))s budget=${RESET_TOTAL_BUDGET_SECONDS}s"
echo "instance $ID reset to READY"
