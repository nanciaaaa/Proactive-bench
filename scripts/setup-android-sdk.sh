#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
echo 'No extension-owned Android SDK is installed. Validating the KU image instead.'
exec "$ROOT/scripts/check-ku-runtime.sh"
