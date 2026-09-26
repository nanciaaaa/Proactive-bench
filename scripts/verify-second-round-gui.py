#!/usr/bin/env python3
"""Click the installed second-round flows through Android WebView."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v',ROOT/'scripts/verify-ui-provenance.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
subprocess.run(['adb','connect',f"127.0.0.1:{v.ENV['adb_port']}"],check=True,capture_output=True)
v.adb('reverse','tcp:8080',f"tcp:{v.INFO['controller_port']}")

def check(app,expr):
    value=v.cdp(app,expr)
    assert value, f'{app}: {expr}'
    return value

def fill(app,selector,value,index=0):
    js=f"(()=>{{const e=document.querySelectorAll({json.dumps(selector)})[{index}];e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));return e.value}})()"
    assert v.cdp(app,js)==value

def start(app):
    v.adb('shell','am','force-stop',f'com.ku.simulator.{app}')
    v.adb('shell','am','start','-n',f'com.ku.simulator.{app}/.MainActivity')
    for _ in range(20):
        time.sleep(.5)
        try:
            if v.cdp(app,"document.querySelector('main')?.dataset.ready")=='true':break
        except Exception:pass
    else:raise AssertionError(f'{app}: not ready')
    assert not v.cdp(app,"!!document.querySelector('.popup')")

def pay(app):
    check(app,"!!document.querySelector('.detail-page .bill-detail')")
    v.click(app,'.detail-page .primary-action')
    check(app,"!!document.querySelector('.payment-page')")
    v.click(app,'.pay-methods button',1)
    fill(app,'.payment-page input','123456')
    v.click(app,'.payment-page .primary-action')
    time.sleep(.5)
    state=v.api(f'/runtime/v1/apps/{app}/state')['state']
    order=state['orders'][-1]
    assert order['status']=='PAID' and order['payments'][-1]['method']=='支付宝支付',order
    assert 'password' not in json.dumps(state)
    return order

results=[]
ONLY=set(os.environ.get("ONLY_APPS", "dudu,lianqi,starrail,babaquna,wangyiyun,chaowei").split(","))
for app,entry in [('dudu','.quick-row button'),('lianqi','.service-strip button')]:
    if app not in ONLY:continue
    start(app);v.click(app,entry)
    check(app,"!!document.querySelector('.flow-form')")
    fill(app,'.flow-form input','人民广场北门',0)
    fill(app,'.flow-form input','虹桥火车站南门',1)
    v.click(app,'.flow-form .primary-action')
    check(app,"!!document.querySelector('.flow-list button')")
    v.click(app,'.flow-list button')
    v.click(app,'.flow-confirm .primary-action')
    order=pay(app)
    assert order['origin']=='人民广场北门' and order['destination']=='虹桥火车站南门',order
    v.click(app,'.bottom-nav button',0)
    check(app,"document.querySelector('.ride-status')?.innerText.includes(order. vehicle_plate)".replace('order. vehicle_plate',json.dumps(order['vehicle_plate'])))
    results.append({'app':app,'flow':'地点→车型→订单→支付宝假密码','order':order['id'],'plate':order['vehicle_plate']})

if 'starrail' in ONLY:
 app='starrail';start(app);v.click(app,'.rail-search')
 check(app,"!!document.querySelector('.flow-list button')")
 v.click(app,'.flow-list button')
 check(app,"document.querySelector('.flow-page')?.innerText.includes('选择席别')")
 v.click(app,'.flow-list button')
 fill(app,'.flow-confirm input','张三',0);fill(app,'.flow-confirm input','13800138000',1)
 v.click(app,'.flow-confirm .primary-action');order=pay(app)
 assert order['train'] and order['seat'] and order['traveler_name']=='张三' and order['traveler_phone']=='13800138000',order
 results.append({'app':app,'flow':'站点→车次→席别→出行人→支付','order':order['id']})
 
if 'babaquna' in ONLY:
 app='babaquna';start(app);v.click(app,'.travel-services button',1)
 check(app,"!!document.querySelector('.flow-form')")
 v.click(app,'.flow-form .primary-action');check(app,"!!document.querySelector('.flow-list button')")
 v.click(app,'.flow-list button')
 fill(app,'.flow-confirm input','李四',0);fill(app,'.flow-confirm input','13900139000',1)
 v.click(app,'.flow-confirm .primary-action');order=pay(app)
 assert order['traveler_name']=='李四' and order['traveler_phone']=='13900139000',order
 results.append({'app':app,'flow':'酒店查询→结果→出行人→支付','order':order['id']})
 
if 'wangyiyun' in ONLY:
 app='wangyiyun';start(app);v.click(app,'.bottom-nav button',1)
 v.click(app,'.utility button');check(app,"!!document.querySelector('.confirm form')")
 fill(app,'#utility-input','验收歌单');v.click(app,'.confirm form button[type=submit]')
 v.click(app,'.bottom-nav button',0);v.click(app,'.music-services button',0)
 v.click(app,'.flow-list button');check(app,"!!document.querySelector('.song-page')")
 v.click(app,'.song-page .detail-actions button',0);v.click(app,'.song-page .detail-actions button',1)
 v.click(app,'.song-page .detail-actions button',2);v.click(app,'.song-page .account-rows button')
 state=v.api(f'/runtime/v1/apps/{app}/state')['state'];assert state['play_history'] and state['favorites'] and state['playlists'][0]['songs'],state
 v.click(app,'.bottom-nav button',1);v.click(app,'.records .order-list button')
 check(app,"!!document.querySelector('.playlist-detail')||!!document.querySelector('.detail-page .flow-list')")
 check(app,"!!document.querySelector('.detail-page .flow-list button')")
 results.append({'app':app,'flow':'创建歌单→歌曲详情→红心与收藏→歌单单曲'})
 
if 'chaowei' in ONLY:
 app='chaowei';start(app);v.click(app,'.reader-topics button',0)
 v.click(app,'.flow-list button');body=check(app,"document.querySelector('.reader-detail')?.innerText")
 assert '车厢座位字母' in body,body
 v.click(app,'.reader-detail .primary-action')
 state=v.api(f'/runtime/v1/apps/{app}/state')['state'];assert state['favorites'],state
 results.append({'app':app,'flow':'话题入口→文章正文→收藏'})
 
path=v.RUNTIME/'second-round-gui-report.json';path.write_text(json.dumps({'passed':True,'flows':results},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':True,'flows':results},ensure_ascii=False))
