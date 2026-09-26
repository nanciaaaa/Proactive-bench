#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
# shellcheck source=scripts/lib/ku-runtime.sh
source "$ROOT/scripts/lib/ku-runtime.sh"
require_ku_runtime
npm run build:apps
npm run build:uni
"$ROOT/scripts/generate-brand-assets.sh"
for APP in dudu lianqi starrail babaquna wangyiyun chaowei; do
  mkdir -p "apps/$APP/dist/uni"
  cp -R "apps/$APP/uni-dist/." "apps/$APP/dist/uni/"
  pushd "apps/$APP" >/dev/null
  [[ -d android ]] || npx cap add android
  npx cap sync android
  popd >/dev/null
done

# Compile all native projects with KU's exact JDK 17 / Android SDK 34 image.
docker run --rm \
  --entrypoint /bin/bash \
  --user "$(id -u):$(id -g)" \
  -e HOME=/workspace/.gradle-cache/ku-home \
  -e GRADLE_USER_HOME=/workspace/.gradle-cache \
  -v "$ROOT:/workspace" \
  -w /workspace \
  "$KU_IMAGE" -lc '
set -euo pipefail
mkdir -p "$HOME/.android"
test -d "$ANDROID_SDK_ROOT/platforms/android-34"
test -d "$ANDROID_SDK_ROOT/build-tools/34.0.0"
java -version 2>&1 | grep -q "version \"17\."
for app in dudu lianqi starrail babaquna wangyiyun chaowei; do
  (cd "apps/$app/android" && ./gradlew -Duser.home="$HOME" assembleDebug)
done
'

mkdir -p "$ROOT/dist/apks"
for APP in dudu lianqi starrail babaquna wangyiyun chaowei; do
  cp "apps/$APP/android/app/build/outputs/apk/debug/app-debug.apk" "$ROOT/dist/apks/$APP-debug.apk"
done
sha256sum dist/apks/*.apk >dist/apks/SHA256SUMS
python3 scripts/create-apk-manifest.py
