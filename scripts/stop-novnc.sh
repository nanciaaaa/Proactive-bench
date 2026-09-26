#!/usr/bin/env bash
set -euo pipefail
ID="${1:?instance id required}"
echo "noVNC lifecycle is owned by the KU container for instance $ID; stop it with scripts/stop-ku-environment.sh $ID"
