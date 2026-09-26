#!/usr/bin/env python3
"""Click the account, discounted payment and music controls in KU WebViews."""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('provenance', ROOT / 'scripts/verify-ui-provenance.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

report_path = v.RUNTIME / 'product-feedback-report.json'
report = json.loads(report_path.read_text()) if report_path.exists() else {}
for app, cfg in ([] if '--final' in sys.argv else v.CONFIG.items()):
    v.adb('shell', 'am', 'force-stop', f'com.ku.simulator.{app}')
    v.adb('shell', 'am', 'start', '-n', f'com.ku.simulator.{app}/.MainActivity')
    for _ in range(20):
        time.sleep(1)
        try:
            if v.cdp(app, "document.querySelector('main')?.dataset.ready") == 'true':
                break
        except Exception:
            pass
    else:
        raise AssertionError(f'{app}: app did not start')
    assert not v.cdp(app, "!!document.querySelector('.popup')"), f'{app}: default popup'
    v.click(app, '.bottom-nav button', 2)
    checked = []
    for index, entry in enumerate(cfg['accountMenu']):
        v.click(app, '.settings-list button', index)
        if entry['kind'] == 'browse':
            assert v.cdp(app, "!!document.querySelector('.home')"), f'{app}: {entry["label"]}'
            v.click(app, '.bottom-nav button', 2)
        else:
            title = v.cdp(app, "document.querySelector('.account-section h2')?.innerText")
            assert title == entry['label'], f'{app}: {entry["label"]} opened {title}'
            if entry['kind'] == 'form':
                value = f'验收填写 {app} {entry["field"]}'
                v.cdp(app, f"document.querySelector('.account-section input').value={json.dumps(value)};document.querySelector('.account-section input').dispatchEvent(new Event('input',{{bubbles:true}}))")
                v.click(app, '.account-section .account-save')
                assert v.api(f'/runtime/v1/apps/{app}/state')['state']['account_fields'][entry['field']] == value
            v.click(app, '.account-section .page-title button')
        checked.append(entry['label'])
    report.setdefault(app, {}).update(account_options=checked, launch_popup=False)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

if '--accounts-only' in sys.argv:
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0)

order = v.api('/runtime/v1/apps/lianqi/state')['state']['orders'][-1]
assert order['base_price'] > order['price'] and order['discount'] == order['base_price'] - order['price']
v.adb('shell', 'am', 'start', '-n', 'com.ku.simulator.lianqi/.MainActivity')
time.sleep(3)
v.click('lianqi', '.bottom-nav button', 1)
v.click('lianqi', '.bill summary')
assert v.cdp('lianqi', "document.querySelector('.bill')?.innerText.includes('新客出行券')")
v.click('lianqi', '.bill-actions button:last-child')
order = v.api('/runtime/v1/apps/lianqi/state')['state']['orders'][-1]
assert order['status'] == 'PAID' and order['payments'][0]['amount'] == order['price']
report.setdefault('lianqi', {})['discounted_payment'] = {'base': order['base_price'], 'discount': order['discount'], 'paid': order['payments'][0]['amount']}

v.adb('shell', 'am', 'start', '-n', 'com.ku.simulator.wangyiyun/.MainActivity')
time.sleep(3)
v.click('wangyiyun', '.bottom-nav button', 0)
assert v.cdp('wangyiyun', "!!document.querySelector('.mini-player')")
v.click('wangyiyun', '.mini-player button:last-child')
assert not v.api('/runtime/v1/apps/wangyiyun/state')['state']['playing']
v.click('wangyiyun', '.mini-player button:last-child')
assert v.api('/runtime/v1/apps/wangyiyun/state')['state']['playing']
v.click('wangyiyun', '.mini-player .song-like')
assert v.api('/runtime/v1/apps/wangyiyun/state')['state']['favorites']
report.setdefault('wangyiyun', {})['player'] = 'pause, resume and current-song heart passed'

report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
