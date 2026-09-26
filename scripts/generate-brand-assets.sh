#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
generate() {
  local app="$1" color="$2"
  java "$ROOT/scripts/GenerateBrandAssets.java" \
    "$ROOT/apps/assets/icons/$app.png" "$color" \
    "$ROOT/apps/$app/android/app/src/main/res"
}
generate dudu '#FF6B2C'
generate lianqi '#102A56'
generate starrail '#2468A2'
generate babaquna '#10B8D4'
generate wangyiyun '#D81E06'
generate chaowei '#1478E8'
echo 'six launcher and splash asset sets generated'
