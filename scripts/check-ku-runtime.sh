#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"
require_ku_runtime

IMAGE_ID="$(ku_image_id)"
docker run --rm --entrypoint /bin/bash "$KU_IMAGE" -lc '
set -euo pipefail
test "$AVD_NAME" = "Pixel_8_API_34_x86_64"
java -version 2>&1 | grep -q "version \"17\."
test -d "$ANDROID_SDK_ROOT/platforms/android-34"
test -d "$ANDROID_SDK_ROOT/build-tools/34.0.0"
test -d "/root/.android/avd/$AVD_NAME.avd/snapshots/init_state"
test -x /app/docker/start_emulator.sh
test -x /app/docker/start_novnc.sh
'
printf 'KU runtime verified\nimage=%s\nimage_id=%s\navd=%s\nsnapshot=%s\nandroid_api=%s\nbuild_tools=%s\njava=%s\n' \
  "$KU_IMAGE" "$IMAGE_ID" "$KU_AVD_NAME" "$KU_SNAPSHOT" "$KU_ANDROID_API" "$KU_BUILD_TOOLS" "$KU_JAVA_VERSION"
