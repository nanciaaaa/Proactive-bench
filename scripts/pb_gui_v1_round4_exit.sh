#!/usr/bin/env bash
# V1 round-4 gate exit (blueprint section 5, round 4):
#
#   preflight -> replay -> writeback -> evidence build
#
# One reusable exit for the V1 closed loop. Every step is the existing CLI with
# its own fail-closed validation (no rule is duplicated here) and the script
# stops at the first failure. Nothing outside this chain is reachable: image
# live, PB `evaluate.py`, Gold, the publication path and coverage expansion are
# V2 and are intentionally not wired in.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="$ROOT/scripts/pb_gui_v1_evidence.py"
HOST="$ROOT/scripts/pb_gui_image_host.py"

usage() {
    cat <<'EOF'
usage: pb_gui_v1_round4_exit.sh --pb-root PB_ROOT --episode NAME
       --release-report FILE --capture-dir DIR --backend-receipt FILE
       --host-dir DIR [--stage-dir DIR] [--bundle-dir DIR]
       [--evidence-bundle DIR] [--capabilities FILE] [--image-capacity N]

steps: preflight -> replay -> writeback [-> evidence build with --evidence-bundle]

defaults:
  --stage-dir   <HOST_DIR>/stage (a stage inside the repository lets the host
                receipt record a reusable project-relative reference)
  --bundle-dir  runtime/pb-gui-passive-image-bundle-<cluster>-<episode>-<passive>-v1

HOST_DIR receives host-receipt.json (step 1) and replay-receipt.json (step 2).
Every target directory must be empty or absent; existing artifacts are never
overwritten.
EOF
}

fail() {
    echo "$(basename "$0"): error: $*" >&2
    exit 2
}

PB_ROOT= EPISODE= RELEASE_REPORT= CAPTURE_DIR= BACKEND_RECEIPT= HOST_DIR=
STAGE_DIR= BUNDLE_DIR= EVIDENCE_BUNDLE= CAPABILITIES= IMAGE_CAPACITY=
while [ $# -gt 0 ]; do
    case "$1" in
        --pb-root) PB_ROOT="${2:?--pb-root needs a value}"; shift 2 ;;
        --episode) EPISODE="${2:?--episode needs a value}"; shift 2 ;;
        --release-report) RELEASE_REPORT="${2:?--release-report needs a value}"; shift 2 ;;
        --capture-dir) CAPTURE_DIR="${2:?--capture-dir needs a value}"; shift 2 ;;
        --backend-receipt) BACKEND_RECEIPT="${2:?--backend-receipt needs a value}"; shift 2 ;;
        --host-dir) HOST_DIR="${2:?--host-dir needs a value}"; shift 2 ;;
        --stage-dir) STAGE_DIR="${2:?--stage-dir needs a value}"; shift 2 ;;
        --bundle-dir) BUNDLE_DIR="${2:?--bundle-dir needs a value}"; shift 2 ;;
        --evidence-bundle) EVIDENCE_BUNDLE="${2:?--evidence-bundle needs a value}"; shift 2 ;;
        --capabilities) CAPABILITIES="${2:?--capabilities needs a value}"; shift 2 ;;
        --image-capacity) IMAGE_CAPACITY="${2:?--image-capacity needs a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument $1 (see --help)" ;;
    esac
done

[ -n "$PB_ROOT" ] || fail "--pb-root is required"
[ -n "$EPISODE" ] || fail "--episode is required"
[ -n "$RELEASE_REPORT" ] || fail "--release-report is required"
[ -n "$CAPTURE_DIR" ] || fail "--capture-dir is required"
[ -n "$BACKEND_RECEIPT" ] || fail "--backend-receipt is required"
[ -n "$HOST_DIR" ] || fail "--host-dir is required"
[[ "$EPISODE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || fail "--episode must be a single safe name: $EPISODE"
[ -f "$RELEASE_REPORT" ] || fail "release report not found: $RELEASE_REPORT"

STAGE_DIR="${STAGE_DIR:-$HOST_DIR/stage}"
HOST_RECEIPT="$HOST_DIR/host-receipt.json"
REPLAY_RECEIPT="$HOST_DIR/replay-receipt.json"
if [ -z "$BUNDLE_DIR" ]; then
    BUNDLE_DIR="$(python3 - "$RELEASE_REPORT" <<'PY' || exit 2
import json, sys
scope = json.load(open(sys.argv[1], encoding='utf8'))['episode_scope']
print('runtime/pb-gui-passive-image-bundle-{}-{}-{}-v1'.format(
    scope['cluster_id'], scope['episode_id'], scope['passive_id']))
PY
)" || fail "cannot derive --bundle-dir from $RELEASE_REPORT"
fi

echo "[1/4] preflight: verify the release chain and stage the PB image view" >&2
python3 "$HOST" preflight --release-report "$RELEASE_REPORT" --capture-dir "$CAPTURE_DIR" \
    --stage-dir "$STAGE_DIR" --output "$HOST_RECEIPT" --pb-root "$PB_ROOT" \
    ${CAPABILITIES:+--capabilities "$CAPABILITIES"}

echo "[2/4] replay: attach the staged device frames through PB Session(image)" >&2
python3 "$HOST" replay --host-receipt "$HOST_RECEIPT" --stage-dir "$STAGE_DIR" \
    --output "$REPLAY_RECEIPT" --pb-root "$PB_ROOT" \
    ${IMAGE_CAPACITY:+--image-capacity "$IMAGE_CAPACITY"} \
    ${CAPABILITIES:+--capabilities "$CAPABILITIES"}

echo "[3/4] writeback: write the verified image references into $BUNDLE_DIR" >&2
python3 "$HOST" writeback --host-receipt "$HOST_RECEIPT" --replay-receipt "$REPLAY_RECEIPT" \
    --stage-dir "$STAGE_DIR" --bundle-dir "$BUNDLE_DIR" --pb-root "$PB_ROOT"

if [ -n "$EVIDENCE_BUNDLE" ]; then
    echo "[4/4] evidence build: rebuild the repository-visible evidence bundle" >&2
    python3 "$EVIDENCE" build --bundle "$EVIDENCE_BUNDLE" --episode "$EPISODE" \
        "$RELEASE_REPORT" "$CAPTURE_DIR" "$BACKEND_RECEIPT" "$HOST_RECEIPT" \
        "$REPLAY_RECEIPT" "$BUNDLE_DIR/manifest.json"
else
    echo "[4/4] evidence build skipped (pass --evidence-bundle DIR to rebuild it)" >&2
fi

echo "round-4 exit complete: host=$HOST_RECEIPT replay=$REPLAY_RECEIPT bundle=$BUNDLE_DIR" >&2
