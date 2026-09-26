#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"
require_ku_runtime
cd "$ROOT"

sha256sum -c dist/apks/SHA256SUMS
docker run --rm --entrypoint /bin/bash -v "$ROOT:/workspace:ro" "$KU_IMAGE" -lc '
set -euo pipefail
AAPT="$ANDROID_SDK_ROOT/build-tools/34.0.0/aapt"
SIGNER="$ANDROID_SDK_ROOT/build-tools/34.0.0/apksigner"
for app in dudu lianqi starrail babaquna wangyiyun chaowei; do
  apk="/workspace/dist/apks/$app-debug.apk"
  test -f "$apk"
  package="$($AAPT dump badging "$apk" | sed -n "s/^package: name=\x27\([^\x27]*\)\x27.*/\1/p")"
  test "$package" = "com.ku.simulator.$app"
  if ! signer_output="$("$SIGNER" verify "$apk" 2>&1)"; then
    printf "%s\n" "$signer_output" >&2
    exit 1
  fi
done
'
python3 - <<'PY'
import json
from pathlib import Path
manifest=json.loads(Path('dist/apks/manifest.json').read_text())
assert manifest['toolchain']['android_api']=='34'
assert manifest['toolchain']['compile_sdk']==34
assert manifest['toolchain']['target_sdk']==34
assert manifest['toolchain']['capacitor']=='6.2.1'
assert len(manifest['apps'])==6
print('six KU-aligned APKs verified')
PY
