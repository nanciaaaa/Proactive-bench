#!/usr/bin/env python3
"""Exercise each app's primary and account utility through the installed WebView."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('provenance', ROOT / 'scripts/verify-ui-provenance.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

subprocess.run(['adb', 'connect', f"127.0.0.1:{v.ENV['adb_port']}"], check=True, capture_output=True)
v.adb('reverse', 'tcp:8080', f"tcp:{v.INFO['controller_port']}")
report = []
for app, cfg in v.CONFIG.items():
    v.adb('shell', 'am', 'force-stop', f'com.ku.simulator.{app}')
    v.adb('shell', 'am', 'start', '-n', f'com.ku.simulator.{app}/.MainActivity')
    for _ in range(20):
        time.sleep(1)
        try:
            if v.cdp(app, "document.querySelector('main')?.dataset.ready") == 'true': break
        except (subprocess.CalledProcessError, StopIteration):
            continue
    else: raise AssertionError(f'{app}: app did not become ready')
    assert not v.cdp(app, "!!document.querySelector('.popup')"), f'{app}: unsolicited launch popup'
    if app == 'dudu': v.click(app, '.resource-tabs button', 1)
    if app == 'starrail': v.click(app, '.resource-tabs button', 1)
    assert v.cdp(app, "!!document.querySelector('.cards article button')"), f'{app}: no primary card'
    v.click(app, '.cards article button:last-child')
    if app != 'wangyiyun':
        assert v.cdp(app, "!!document.querySelector('.confirm:not(.popup)')"), f'{app}: no confirmation'
        v.click(app, '.confirm:not(.popup) button:last-child')
    else:
        assert v.cdp(app, "!!document.querySelector('.mini-player')"), 'music: playback bar missing'
        assert v.cdp(app, "!!document.querySelector('.cards .song-like')"), 'music: per-song heart missing'
    time.sleep(1)
    state = v.api(f'/runtime/v1/apps/{app}/state')['state']
    key = 'play_history' if app == 'wangyiyun' else 'favorites' if app == 'chaowei' else 'orders'
    assert state.get(key), f'{app}: primary action missing from {key}'
    v.click(app, '.bottom-nav button', 1)
    assert v.cdp(app, "!!document.querySelector('.records')"), f'{app}: records page missing'
    if key == 'orders':
        assert v.cdp(app, "!!document.querySelector('.bill summary b')"), f'{app}: semantic bill missing'
    v.click(app, '.utility button')
    if cfg['utilities'][0].get('input'):
        assert v.cdp(app, "!!document.querySelector('.confirm form')"), f'{app}: utility form missing'
        v.click(app, '.confirm form button[type=submit]')
    elif cfg['utilities'][0].get('confirm'):
        assert v.cdp(app, "!!document.querySelector('.confirm:not(.popup)')"), f'{app}: utility confirmation missing'
        v.click(app, '.confirm:not(.popup) button:last-child')
    time.sleep(1)
    mutations = v.api(f'/local/v1/apps/{app}/mutations?since_cursor=0', admin=True)['data']['mutations']
    operations = [m['operation'] for m in mutations if m['actor'] == 'agent_gui' and m['status'] == 'committed']
    expected = {cfg['primary'], cfg['utilities'][0]['type']}
    assert expected <= set(operations), f'{app}: missing committed GUI mutations {expected - set(operations)}'
    row = {'app': app, 'primary': cfg['primary'], 'utility': cfg['utilities'][0]['type'],
           'state_field': key, 'state_count': len(state[key]), 'committed_gui_mutations': operations}
    report.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

target = v.RUNTIME / 'gui-flow-report.json'
target.write_text(json.dumps({'passed': True, 'flows': report}, ensure_ascii=False, indent=2) + '\n')
