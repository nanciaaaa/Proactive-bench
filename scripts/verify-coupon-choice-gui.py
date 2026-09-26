#!/usr/bin/env python3
"""Verify service-specific vehicle options and coupon switching in installed Lianqi."""
import importlib.util
import json
import subprocess
import time
from pathlib import Path

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v',root/'scripts/verify-ui-provenance.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
subprocess.run(['adb','connect',f"127.0.0.1:{v.ENV['adb_port']}"],check=True,capture_output=True)
v.adb('reverse','tcp:8080',f"tcp:{v.INFO['controller_port']}")
app='lianqi'
v.adb('shell','am','force-stop','com.ku.simulator.lianqi')
v.adb('shell','am','start','-n','com.ku.simulator.lianqi/.MainActivity')
for _ in range(20):
    time.sleep(.5)
    try:
        if v.cdp(app,"document.querySelector('main')?.dataset.ready")=='true':break
    except Exception:pass
else:raise AssertionError('not ready')
counts={}
for i,label in enumerate(('即时','预约','接送机','商务')):
    v.click(app,'.service-strip button',i)
    v.click(app,'.flow-form .primary-action')
    titles=v.cdp(app,"[...document.querySelectorAll('.flow-list button b')].map(x=>x.innerText)")
    assert len(titles)>=3,(label,titles)
    counts[label]=titles
    v.click(app,'.flow-page .page-title button')
v.click(app,'.service-strip button',0)
v.click(app,'.flow-form .primary-action')
v.click(app,'.flow-list button',0)
v.click(app,'.flow-confirm .primary-action')
v.click(app,'.detail-page .primary-action')
v.click(app,'.payment-page>button:not(.primary-action)')
assert v.cdp(app,"document.querySelectorAll('.sheet li').length")>=2
state=lambda:v.api('/runtime/v1/apps/lianqi/state')['state']['orders'][-1]
base=state()['price']
v.click(app,'.sheet li button',0)
first=state();assert first['price']<base,first
v.click(app,'.payment-page>button:not(.primary-action)')
v.click(app,'.sheet li button',1)
second=state();assert second['price']<base and second['coupon_title']!=first['coupon_title'],second
v.click(app,'.payment-page>button:not(.primary-action)')
v.click(app,'.sheet-primary')
none=state();assert none['price']==base and none['discount']==0,none
report={'passed':True,'vehicle_options':counts,'base':base,'first_coupon':first['coupon_title'],'first_price':first['price'],'second_coupon':second['coupon_title'],'second_price':second['price'],'without_coupon':none['price']}
(v.RUNTIME/'coupon-choice-gui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
