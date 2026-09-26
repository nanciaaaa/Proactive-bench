#!/usr/bin/env python3
"""P4 实机闭环补充：GUI 可见性证据重试（处理 ANR 对话框遮挡）。

对每款 App：seed → 启动 → 等 12s → ui dump；若出现 ANR 对话框则点 Wait 再等 8s 重 dump；
断言注入弹窗文案出现在 UI 层级中；截图存档；最后 teardown 保持实例干净。
"""
import json, re, subprocess, sys, time, urllib.error, urllib.request
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


def sh(cmd, timeout=90):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def call(path, method='GET', data=None):
    headers = {'Content-Type': 'application/json', 'X-Runner-Key': KEY}
    req = urllib.request.Request(f'{BASE}{path}', method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def ui_dump():
    sh(ADB + ['shell', 'uiautomator', 'dump', '/sdcard/ui.xml'], timeout=120)
    p = sh(ADB + ['shell', 'cat', '/sdcard/ui.xml'], timeout=30)
    return p.stdout.decode('utf-8', errors='replace') if p.returncode == 0 else ''


def tap_center_of(text):
    """在 ui.xml 中找 text 节点的 bounds 中心并点击。"""
    xml = ui_dump()
    m = re.search(r'text="%s"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(text), xml)
    if m:
        x = (int(m.group(1)) + int(m.group(3))) // 2
        y = (int(m.group(2)) + int(m.group(4))) // 2
        sh(ADB + ['shell', 'input', 'tap', str(x), str(y)], timeout=30)
        return True
    return False


def screencap(name):
    p = sh(ADB + ['exec-out', 'screencap', '-p'], timeout=60)
    if p.returncode == 0 and p.stdout.startswith(b'\x89PNG'):
        (SHOT / name).write_bytes(p.stdout)


def run(app):
    ep = f'p4-loop-{app}'
    etype, edata = USER_ENTITY[app]
    fixture = {'episode_scope': ep, 'common_records': [
        {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'popup-p4',
         'data': {'kind': 'promotion', 'title': f'P4 闭环注入 · {app}',
                  'message': '本弹窗由 seed 注入，用于实机闭环验证。'}},
        {'app_id': app, 'entity_type': etype, 'semantic_ref': 'p4-entity-1',
         'data': edata}], 'world_deltas': []}
    call(f'/local/v1/apps/{app}/seed', 'POST',
         {'fixture': fixture, 'idempotency_key': f'p4-loop-gui-{app}'})

    sh(ADB + ['shell', 'am', 'force-stop', f'com.ku.simulator.{app}'], timeout=30)
    sh(ADB + ['shell', 'am', 'start', '-n',
              f'com.ku.simulator.{app}/.MainActivity'], timeout=30)
    time.sleep(14)
    xml = ui_dump()
    anr = 'isn\'t responding' in xml
    if anr:
        tapped = tap_center_of('Wait') or tap_center_of('等待')
        time.sleep(10)
        xml = ui_dump()
    shows = ('P4 闭环注入' in xml) and (app in xml)
    screencap(f'{app}-01b-injected.png')
    rec = {'app': app, 'anr_dialog': anr, 'gui_shows_popup': shows}
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    # 清理：teardown
    call(f'/local/v1/apps/{app}/teardown', 'POST', {'episode_scope': ep})
    return rec


if __name__ == '__main__':
    results = [run(a) for a in APPS]
    print('SUMMARY', sum(r['gui_shows_popup'] for r in results), '/', len(results))
