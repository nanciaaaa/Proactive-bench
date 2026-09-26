#!/usr/bin/env python3
"""Click user-reported third-round paths on installed KU APKs."""
import importlib.util, json, subprocess, time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v',root/'scripts/verify-ui-provenance.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
subprocess.run(['adb','connect',f"127.0.0.1:{v.ENV['adb_port']}"],check=True,capture_output=True)
v.adb('reverse','tcp:8080',f"tcp:{v.INFO['controller_port']}")
checks={}
def start(app):
 v.adb('shell','am','force-stop',f'com.ku.simulator.{app}')
 v.adb('shell','am','start','-n',f'com.ku.simulator.{app}/.MainActivity')
 for _ in range(20):
  time.sleep(.5)
  try:
   if v.cdp(app,"document.querySelector('main')?.dataset.ready")=='true':return
  except Exception:pass
 raise AssertionError(f'{app} not ready')
def check(name,value):checks[name]=bool(value)
start('chaowei');v.click('chaowei','.reader-topics button',1);v.click('chaowei','.flow-list button')
check('topic_has_multiple_entries',v.cdp('chaowei',"document.querySelectorAll('.topic-entries button').length")>=2)
start('starrail');v.click('starrail','.rail-search');v.click('starrail','.flow-list button');v.click('starrail','.bottom-nav button',0);v.click('starrail','.rail-search')
check('fresh_train_query_has_no_selection','已选车次' not in (v.cdp('starrail',"document.querySelector('.flow-page')?.innerText") or ''))
start('wangyiyun');v.click('wangyiyun','.resource-tabs button',1);v.click('wangyiyun','.cards article')
check('playlist_card_opens_playlist',v.cdp('wangyiyun',"!!document.querySelector('.playlist-detail')"))
check('playlist_has_multiple_tracks',v.cdp('wangyiyun',"document.querySelectorAll('.detail-page .flow-list button').length")>=2)
start('dudu');v.click('dudu','.bottom-nav button',2)
check('ride_has_no_favorite_stat','收藏' not in (v.cdp('dudu',"document.querySelector('.account-stats')?.innerText") or ''))
v.click('dudu','.account-stats button',1)
check('coupon_stat_opens_coupon_page','优惠券' in (v.cdp('dudu',"document.querySelector('.account-section .page-title')?.innerText") or ''))
v.click('dudu','.bottom-nav button',2);v.click('dudu','.account-card')
check('profile_header_opens_own_page',v.cdp('dudu',"!!document.querySelector('.account-section .page-title')"))
v.click('dudu','.bottom-nav button',0)
check('unsupported_icons_visible',v.cdp('dudu',"document.querySelectorAll('.quick-row .static-service').length")>=2)
check('recommended_places_visible',v.cdp('dudu',"!!document.querySelector('.recommended-places')"))
v.click('dudu','.quick-row button');v.click('dudu','.flow-form .primary-action');v.click('dudu','.flow-list button');v.click('dudu','.flow-confirm .primary-action')
check('dudu_has_no_cancel_action',not v.cdp('dudu',"!!document.querySelector('.detail-page .secondary-action')"))
report={'passed':all(checks.values()),'checks':checks}
(v.RUNTIME/'third-round-gui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
