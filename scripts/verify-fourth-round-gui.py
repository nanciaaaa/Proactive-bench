#!/usr/bin/env python3
"""Verify the fourth-round order flows in the installed Android WebViews."""
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gui', ROOT / 'scripts/verify-ui-provenance.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
gui.adb('reverse', 'tcp:8080', f"tcp:{gui.INFO['controller_port']}")


def click(app, selector, index=0):
    assert gui.cdp(app, f"!!document.querySelectorAll({json.dumps(selector)})[{index}]") , (app, selector)
    gui.click(app, selector, index)


def fill(app, selector, value, index=0):
    expression = f"(()=>{{let el=document.querySelectorAll({json.dumps(selector)})[{index}];el.value={json.dumps(value)};el.dispatchEvent(new Event('input',{{bubbles:true}}));return el.value}})()"
    assert gui.cdp(app, expression) == value


def start(app):
    gui.adb('shell', 'am', 'force-stop', f'com.ku.simulator.{app}')
    gui.adb('shell', 'am', 'start', '-n', f'com.ku.simulator.{app}/.MainActivity')
    for _ in range(30):
        time.sleep(.5)
        try:
            if gui.cdp(app, "document.querySelector('main')?.dataset.ready") == 'true':
                return
        except Exception:
            pass
    raise AssertionError(f'{app} failed to load')


def state(app):
    return gui.api(f'/runtime/v1/apps/{app}/state')['state']


def pay(app, traveler=None):
    if traveler:
        for _ in range(10):
            if gui.cdp(app, "document.querySelector('main')?.innerText.includes('确认订单 · 出行人')"):
                break
            time.sleep(.2)
        assert gui.cdp(app, "document.querySelector('main')?.innerText.includes('确认订单 · 出行人')")
        fill(app, ".detail-page input", traveler[0], 0)
        fill(app, ".detail-page input", traveler[1], 1)
        click(app, '.detail-page .primary-action')
    else:
        click(app, '.detail-page .primary-action')
    assert gui.cdp(app, "!!document.querySelector('.payment-page')")
    if app == 'lianqi':
        assert gui.cdp(app, "document.querySelector('.reservation-summary')?.innerText.includes('预约时间')")
    if app in ('dudu', 'lianqi', 'babaquna'):
        click(app, '.payment-page > button:not(.primary-action)')
        assert gui.cdp(app, "!!document.querySelector('.sheet li button')")
        assert gui.cdp(app, "(()=>{let e=document.querySelector('.sheet li button'),s=document.querySelector('.sheet>div');return e.getBoundingClientRect().height>=48&&e.getBoundingClientRect().width>=112&&getComputedStyle(s).overflowY==='auto'})()")
        click(app, '.sheet li button')
        assert state(app)['orders'][-1]['discount'] > 0
    fill(app, '.payment-page input', '123456')
    click(app, '.payment-page .primary-action')
    assert state(app)['orders'][-1]['status'] == 'PAID'


results = []
start('dudu')
click('dudu', '.home > .primary-action')
click('dudu', '.flow-form .primary-action')
assert gui.cdp('dudu', "document.querySelectorAll('.flow-list button').length") >= 4
click('dudu', '.flow-list button', 3)
assert '确认并呼叫' in gui.cdp('dudu', "document.querySelector('.flow-confirm')?.innerText")
click('dudu', '.flow-confirm .primary-action')
pay('dudu')
results.append('dudu: query both car types, call, coupon and payment')
click('dudu', '.bottom-nav button', 1)
assert not gui.cdp('dudu', "document.querySelector('.utility')?.innerText.includes('取消')")
click('dudu', '.order-links button')
assert not gui.cdp('dudu', "document.querySelector('.detail-page')?.innerText.includes('取消行程')")
results.append('dudu: no cancellation entry')

start('starrail')
click('starrail', '.rail-search')
click('starrail', '.flow-list button')
assert gui.cdp('starrail', "!!document.querySelector('h2') && document.querySelector('main').innerText.includes('车次详情')")
click('starrail', '.train-detail .primary-action') if gui.cdp('starrail', "!!document.querySelector('.train-detail')") else click('starrail', '.detail-page .primary-action')
click('starrail', '.flow-list button')
click('starrail', '.flow-confirm .primary-action')
assert gui.cdp('starrail', "document.querySelector('main').innerText.includes('确认订单 · 出行人')")
pay('starrail', ('张三', '13800138000'))
click('starrail', '.bottom-nav button', 1)
click('starrail', '.utility button', 1)
click('starrail', '.flow-list button')
click('starrail', '.flow-list button', 1)
click('starrail', '.flow-list button')
assert state('starrail')['orders'][0]['status'] == 'CHANGED'
results.append('starrail: details, traveler, payment, chosen-order change')
click('starrail', '.bottom-nav button', 0)
click('starrail', '.rail-search')
click('starrail', '.flow-list button')
click('starrail', '.detail-page .primary-action')
click('starrail', '.flow-list button')
click('starrail', '.flow-confirm .primary-action')
pay('starrail', ('王五', '13700137000'))
click('starrail', '.bottom-nav button', 1)
click('starrail', '.utility button', 2)
click('starrail', '.flow-list button')
click('starrail', '.confirm button:last-child')
assert state('starrail')['orders'][-1]['status'] == 'REFUNDED'
results.append('starrail: selected-order refund')

start('babaquna')
click('babaquna', '.travel-services button', 2)
click('babaquna', '.flow-form .primary-action')
click('babaquna', '.flow-list button')
assert gui.cdp('babaquna', "document.querySelector('main').innerText.includes('景点介绍')")
click('babaquna', '.detail-page .primary-action')
pay('babaquna', ('李四', '13900139000'))
results.append('babaquna: attraction description, traveler, coupon and payment')

start('lianqi')
assert not gui.cdp('lianqi', "document.querySelector('.address-card')?.innerText.includes('服务车型')")
click('lianqi', '.service-strip button')
click('lianqi', '.flow-form .primary-action')
click('lianqi', '.flow-list button')
click('lianqi', '.flow-confirm .primary-action')
pay('lianqi')
click('lianqi', '.bottom-nav button', 1)
assert not gui.cdp('lianqi', "document.querySelector('.utility')?.innerText.includes('开始导航')")
click('lianqi', '.utility button')
click('lianqi', '.flow-list button')
click('lianqi', '.confirm button:last-child')
assert state('lianqi')['orders'][-1]['status'] == 'CANCELLED'
assert state('lianqi')['orders'][-1]['payments'][-1]['amount'] < 0
results.append('lianqi: reservation summary, paid cancellation and refund')
start('chaowei')
assert not gui.cdp('chaowei', "document.querySelector('.reader-tabs')?.innerText.includes('关注')")
click('chaowei', '.bottom-nav button', 1)
assert not gui.cdp('chaowei', "document.querySelector('.utility')?.innerText.includes('切换收藏')")
results.append('chaowei: no redundant favorite action')
start('wangyiyun')
assert gui.cdp('wangyiyun', "(()=>{let e=document.querySelector('.music-services>.static-service');return e&&getComputedStyle(e).backgroundColor==='rgba(0, 0, 0, 0)'&&e.getBoundingClientRect().height<70})()")
results.append('wangyiyun: static service icon layout')

report = {'passed': True, 'flows': results}
(gui.RUNTIME / 'fourth-round-gui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
