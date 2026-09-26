#!/usr/bin/env bash
set -euo pipefail
SERIAL="${1:?ADB serial required}"; PORT="${2:?host port required}"
adb -s "$SERIAL" wait-for-device
adb -s "$SERIAL" reverse --remove tcp:8080 >/dev/null 2>&1 || true
adb -s "$SERIAL" reverse tcp:8080 "tcp:$PORT"
adb -s "$SERIAL" reverse --list | grep -F "tcp:8080 tcp:$PORT" >/dev/null

