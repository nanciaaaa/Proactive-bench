#!/usr/bin/env python3
"""P4 实机闭环：GUI 可见性硬证据（Chrome DevTools Protocol 读 WebView DOM 文本）。

对每款 App：seed 注入带独特标题的弹窗 → 启动 → 经 webview_devtools_remote socket
用 CDP Runtime.evaluate 读 document.body.innerText → 断言弹窗标题出现在渲染后的 GUI 中
→ 截图存档 → teardown。证据写 runtime/instances/p4-loop/screenshots/gui-evidence.jsonl。
"""
import json, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

import websocket

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


def screencap(name):
    p = sh(ADB + ['exec-out', 'screencap', '-p'], timeout=60)
    if p.returncode == 0 and p.stdout.startswith(b'\x89PNG'):
        (SHOT / name).write_bytes(p.stdout)
        return str(SHOT / name)
    return None


def cdp_eval(app, expression):
    """经 app 的 webview_devtools_remote socket 执行 JS 并取回值。"""
    p = sh(ADB + ['shell', 'pidof', f'com.ku.simulator.{app}'], timeout=30)
    pid = p.stdout.decode().strip().split()[-1] if p.returncode == 0 and p.stdout.strip() else None
    if not pid:
        return None, 'no-pid'
    lp = sh(ADB + ['forward', 'tcp:0', f'localabstract:webview_devtools_remote_{pid}'], timeout=30)
    if lp.returncode != 0:
        return None, 'forward-failed'
    local_port = int(lp.stdout.decode().strip())
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{local_port}/json/list', timeout=10) as r:
            targets = json.loads(r.read())
        pages = [t for t in targets if t.get('type') == 'page']
        if not pages:
            return None, 'no-page-target'
        ws_url = pages[0]['webSocketDebuggerUrl']
        ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': expression, 'returnByValue': True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1:
                    r = msg.get('result', {}).get('result', {})
                    return r.get('value'), None
        finally:
            ws.close()
    except Exception as e:  # noqa: BLE001
        return None, f'cdp-error:{type(e).__name__}'
    finally:
        sh(ADB + ['forward', '--remove', f'tcp:{local_port}'], timeout=30)


def run(app):
    ep = f'p4-loop-{app}'
    etype, edata = USER_ENTITY[app]
    title = f'P4 闭环注入 · {app}'
    fixture = {'episode_scope': ep, 'common_records': [
        {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'popup-p4',
         'data': {'kind': 'promotion', 'title': title, 'message': '本弹窗由 seed 注入，用于实机闭环验证。'}},
        {'app_id': app, 'entity_type': etype, 'semantic_ref': 'p4-entity-1', 'data': edata}],
        'world_deltas': []}
    call(f'/local/v1/apps/{app}/seed', 'POST',
         {'fixture': fixture, 'idempotency_key': f'p4-loop-gui-{app}-2'})

    sh(ADB + ['shell', 'am', 'force-stop', f'com.ku.simulator.{app}'], timeout=30)
    sh(ADB + ['shell', 'am', 'start', '-n', f'com.ku.simulator.{app}/.MainActivity'], timeout=30)
    time.sleep(15)

    inner, err = cdp_eval(app, 'document.body.innerText')
    rec = {'app': app, 'episode_scope': ep,
           'gui_shows_popup': bool(inner and title in inner),
           'cdp_error': err,
           'dom_snippet': (inner or '')[:200],
           'screenshot': screencap(f'{app}-02c-gui-evidence.png')}
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    (SHOT / 'gui-evidence.jsonl').open('a').write(json.dumps(rec, ensure_ascii=False) + '\n')
    call(f'/local/v1/apps/{app}/teardown', 'POST', {'episode_scope': ep})
    return rec


if __name__ == '__main__':
    results = [run(a) for a in APPS]
    ok = sum(r['gui_shows_popup'] for r in results)
    print('GUI-EVIDENCE SUMMARY', ok, '/', len(results))
