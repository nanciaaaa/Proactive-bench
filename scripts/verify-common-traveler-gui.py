#!/usr/bin/env python3
"""Create and use a common traveler with phone in the installed travel app."""
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
app='babaquna';v.adb('shell','am','force-stop','com.ku.simulator.babaquna');v.adb('shell','am','start','-n','com.ku.simulator.babaquna/.MainActivity')
for _ in range(20):
 time.sleep(.5)
 try:
  if v.cdp(app,"document.querySelector('main')?.dataset.ready")=='true':break
 except Exception:pass
else:raise AssertionError('not ready')
v.click(app,'.bottom-nav button',2);v.click(app,'.settings-list button',0);v.click(app,'.account-save')
assert v.cdp(app,"document.querySelectorAll('.confirm form input').length")==2
for i,value in enumerate(('王五','13700137000')):
 expr=f"(()=>{{const e=document.querySelectorAll('.confirm form input')[{i}];e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));return e.value}})()"
 assert v.cdp(app,expr)==value
v.click(app,'.confirm form button[type=submit]')
assert '13700137000' in v.cdp(app,"document.querySelector('.account-section')?.innerText")
v.click(app,'.bottom-nav button',0);v.click(app,'.travel-services button',1);v.click(app,'.flow-form .primary-action');v.click(app,'.flow-list button');v.click(app,'.flow-confirm button')
assert v.cdp(app,"document.querySelectorAll('.flow-confirm input')[0]?.value")=='王五'
assert v.cdp(app,"document.querySelectorAll('.flow-confirm input')[1]?.value")=='13700137000'
v.click(app,'.flow-confirm .primary-action')
order=v.api('/runtime/v1/apps/babaquna/state')['state']['orders'][-1]
assert order['traveler_name']=='王五' and order['traveler_phone']=='13700137000',order
report={'passed':True,'traveler':order['traveler_name'],'phone':order['traveler_phone'],'order':order['id']}
(v.RUNTIME/'common-traveler-gui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
