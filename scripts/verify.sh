#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
python3 -m unittest discover -s tests -v
if [[ -d node_modules ]]; then
  node --input-type=module - <<'JS'
import fs from 'node:fs'
import { compile } from '@vue/compiler-dom'
const source = fs.readFileSync('apps/shared/src/main.js', 'utf8')
const template = source.match(/template:`([\s\S]*?)`\s*\}\)\.mount/)[1]
compile(template, { onError: error => { throw error } })
console.log('shared Vue template OK')
JS
  npm run build:apps
else echo "SKIP web build: run scripts/bootstrap.sh first"; fi
if [[ -d node_modules ]]; then npm run build:uni; fi
for APP in dudu lianqi starrail babaquna wangyiyun chaowei; do test -f "apps/$APP/dist/index.html"; test -f "apps/$APP/uni-dist/index.html"; done
! rg -n "knowu_bench|/task/callback|/suite_family" local-controller apps scripts tests contracts emulator --glob '!README*' --glob '!verify.sh'
# 蓝图 §7：envelope 字段白名单断言（顶层键冻结为 §2.2 集合，coverage/error 子对象不得新增字段）
python3 - <<'PY'
import json
schema = json.load(open('contracts/envelope.schema.json'))
top = set(schema['properties'])
assert top == {'status','source','operation','semantic_ref','record_version','observed_at','coverage','data','error'}, f'unexpected envelope fields: {top}'
assert schema['additionalProperties'] is False
for sub in ('coverage','error'):
    assert schema['properties'][sub]['additionalProperties'] is False, sub
assert set(schema['properties']['coverage']['properties']) == {'scope','complete','as_of'}
assert set(schema['properties']['error']['properties']) == {'code','message','retryable'}
print('envelope whitelist OK')
PY
if [[ -f dist/apks/manifest.json ]]; then "$ROOT/scripts/verify-apks.sh"; fi
