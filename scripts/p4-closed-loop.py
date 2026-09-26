#!/usr/bin/env python3
"""P4 实机闭环：六款 App 注入→GUI 可见→readback→journal→teardown。

前提：KU AVD 已启动（ku_simulator_extension_p4-loop_0，host adb 127.0.0.1:5556）、
controller 实例 p4-loop 已运行（port 34343）、adb reverse tcp:8080→34343 已设、六 APK 已装。
证据输出：runtime/instances/p4-loop/screenshots/{app}-01-injected.png / {app}-02-torn-down.png
          + uiautomator XML + loop-report.json
"""
import json, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INST = ROOT / 'runtime/instances/p4-loop'
SHOT = INST / 'screenshots'
SHOT.mkdir(parents=True, exist_ok=True)
PORT = 34343
BASE = f'http://127.0.0.1:{PORT}'
KEY = json.loads((INST / 'instance.json').read_text())['runner_key']
ADB = ['adb', '-s', '127.0.0.1:5556']

APPS = ['dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei']
# 每 App 的用户态实体（订单类 App 用 order；wangyiyun/chaowei 用收藏/笔记）
USER_ENTITY = {
    'dudu': ('order', {'item_id': 'P4-CAR-01', 'price': 38, 'status': 'CONFIRMED',
                       'created_at': '2026-09-22T14:30:00+08:00'}),
    'lianqi': ('order', {'item_id': 'P4-CAR-02', 'price': 55, 'status': 'CONFIRMED',
                         'created_at': '2026-09-22T14:31:00+08:00'}),
    'starrail': ('order', {'item_id': 'P4-TRN-G9', 'price': 553, 'status': 'CONFIRMED',
                           'created_at': '2026-09-22T14:32:00+08:00'}),
    'babaquna': ('order', {'item_id': 'P4-FLT-88', 'price': 1280, 'status': 'CONFIRMED',
                           'created_at': '2026-09-22T14:33:00+08:00'}),
    'wangyiyun': ('favorite', {'item_id': 'P4-SONG-7'}),
    'chaowei': ('favorite', {'item_id': 'P4-ART-3'}),
}
READBACK_OP = {
    'dudu': 'list_orders', 'lianqi': 'list_orders', 'starrail': 'list_orders',
    'babaquna': 'list_orders', 'wangyiyun': 'list_favorites', 'chaowei': 'list_favorites',
}


def call(path, method='GET', data=None, key=True):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['X-Runner-Key'] = KEY
    req = urllib.request.Request(f'{BASE}{path}', method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def screenshot(name):
    p = sh(ADB + ['exec-out', 'screencap', '-p'], timeout=30)
    if p.returncode == 0 and p.stdout.startswith(b'\x89PNG'):
        (SHOT / name).write_bytes(p.stdout)
        return str(SHOT / name)
    return None


def ui_dump(name):
    sh(ADB + ['shell', 'uiautomator', 'dump', '/sdcard/ui.xml'], timeout=60)
    p = sh(ADB + ['shell', 'cat', '/sdcard/ui.xml'], timeout=30)
    if p.returncode == 0:
        txt = p.stdout.decode('utf-8', errors='replace')
        (SHOT / name).write_text(txt, errors='replace')
        return txt
    return ''


def app_main():
    for app in APPS:
        ep = f'p4-loop-{app}'
        etype, edata = USER_ENTITY[app]
        fixture = {'episode_scope': ep, 'common_records': [
            {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'popup-p4',
             'data': {'kind': 'promotion', 'title': f'P4 闭环注入 · {app}',
                      'message': '本弹窗由 seed 注入，用于实机闭环验证。'}},
            {'app_id': app, 'entity_type': etype, 'semantic_ref': 'p4-entity-1',
             'data': edata}], 'world_deltas': []}
        rec = {'app': app, 'episode_scope': ep}

        # 1. 注入
        st, body = call(f'/local/v1/apps/{app}/seed', 'POST',
                        {'fixture': fixture, 'idempotency_key': f'p4-loop-{app}'})
        rec['seed_status'] = st
        rec['seed_error'] = body.get('error', {}).get('code') if st != 200 else None

        # 2. 启动 App（先 force-stop 确保重新拉取 state）
        sh(ADB + ['shell', 'am', 'force-stop', f'com.ku.simulator.{app}'], timeout=20)
        sh(ADB + ['shell', 'am', 'start', '-n',
                  f'com.ku.simulator.{app}/.MainActivity'], timeout=20)
        time.sleep(6)
        rec['screenshot_injected'] = screenshot(f'{app}-01-injected.png')
        xml = ui_dump(f'{app}-01-injected.xml')
        rec['gui_shows_popup'] = 'P4 闭环注入' in xml and app in xml

        # 3. readback（query）
        st, body = call(f'/local/v1/apps/{app}/query', 'POST',
                        {'operation': READBACK_OP[app]})
        rec['readback_status'] = st
        rec['readback_coverage_complete'] = body.get('coverage', {}).get('complete')
        rec['readback_rows'] = len(body.get('data') or [])
        rec['readback_has_entity'] = any(
            (r.get('semantic_ref') == 'p4-entity-1') or (r.get('id') == 'p4-entity-1')
            for r in (body.get('data') or []))

        # 4. journal（read_mutations，管理凭据）
        st, body = call(f'/local/v1/apps/{app}/mutations?since_cursor=0')
        rec['mutations_status'] = st
        muts = (body.get('data') or {}).get('mutations') or []
        rec['mutations_seed_actors'] = sorted({m.get('actor') for m in muts if m.get('status') == 'committed'})
        rec['mutation_count'] = len(muts)

        # 5. teardown
        st, body = call(f'/local/v1/apps/{app}/teardown', 'POST', {'episode_scope': ep})
        rec['teardown_status'] = st
        rec['teardown_error'] = body.get('error', {}).get('code') if st != 200 else None

        # 6. teardown 后回读应消失
        st, body = call(f'/local/v1/apps/{app}/query', 'POST',
                        {'operation': READBACK_OP[app]})
        rec['post_teardown_readback_status'] = st
        rec['post_teardown_rows'] = len(body.get('data') or [])
        st, body = call(f'/local/v1/apps/{app}/read_state')
        stq, _ = call(f'/local/v1/apps/{app}/query', 'POST', {'operation': 'list_active_popups'})

        # 7. teardown 后重启 App 截图（弹窗应恢复默认确定性弹窗）
        sh(ADB + ['shell', 'am', 'force-stop', f'com.ku.simulator.{app}'], timeout=20)
        sh(ADB + ['shell', 'am', 'start', '-n',
                  f'com.ku.simulator.{app}/.MainActivity'], timeout=20)
        time.sleep(6)
        rec['screenshot_torndown'] = screenshot(f'{app}-02-torn-down.png')
        xml2 = ui_dump(f'{app}-02-torn-down.xml')
        rec['injected_popup_gone'] = 'P4 闭环注入' not in xml2

        print(json.dumps(rec, ensure_ascii=False))
        (SHOT / 'loop-report.jsonl').open('a').write(json.dumps(rec, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    app_main()
