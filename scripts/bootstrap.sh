#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
node scripts/create-apps.mjs
if [[ ! -d node_modules ]]; then npm install --no-audit --no-fund --cache "$ROOT/.npm-cache"; fi
python3 -c "import sys; sys.path.insert(0, 'local-controller'); from datasets import build_dataset; from pathlib import Path; print(build_dataset(Path('datasets/generated/catalog.sqlite')))"
echo "Bootstrap complete: $ROOT"
