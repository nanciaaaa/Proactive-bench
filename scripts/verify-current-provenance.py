#!/usr/bin/env python3
"""Seed, inspect installed WebView, query, and tear down current visible fields."""
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
primary={'dudu':'vehicle_quotes','lianqi':'routes','starrail':'trains','babaquna':'flights','wangyiyun':'songs','chaowei':'articles'}
entry_selector={'dudu':'.quick-row button','lianqi':'.service-strip button','starrail':'.rail-search','babaquna':'.travel-services button','wangyiyun':'.music-services button','chaowei':'.reader-topics button'}
reports={}
for app,cfg in v.CONFIG.items():
    episode=f'current-provenance-{app}-{int(time.time())}'
    res=primary[app]
    title={'lianqi':'即时用车·验收舒适型','dudu':'验收快车'}.get(app,f'验收{res}')
    data={'title':title,'subtitle':f'验收说明{app}','price':37,'available':7,'rank':-10}
    if app=='chaowei':data['body']=f'验收正文{app}：这是一段可读的模拟内容。'
    records=[{'app_id':app,'entity_type':res,'semantic_ref':f'trace-{res}','data':data},
             {'app_id':app,'entity_type':'ui_content','semantic_ref':'trace-content','data':{'hero':f'验收首页{app}','profile_name':f'验收用户{app}','planner':cfg['planner']}},
             {'app_id':app,'entity_type':'popup','semantic_ref':'trace-popup','data':{'kind':'notice','title':f'验收通知{app}','message':f'验收消息{app}'}},
             {'app_id':app,'entity_type':'account_field','semantic_ref':'trace-account','data':{'key':next(e['field'] for e in cfg['accountMenu'] if e['kind']=='form'),'value':f'验收账户{app}'}}]
    if app not in ('wangyiyun','chaowei'):
        records.append({'app_id':app,'entity_type':'order','semantic_ref':'trace-order','data':{'item_id':f'trace-{res}','title':f'验收订单{app}','subtitle':f'验收服务{app}','origin':'验收起点','destination':'验收终点','service_type':cfg['services'][0]['label'],'base_price':42,'discount':5,'coupon_title':'验收优惠','price':37,'status':'PAID','payments':[{'amount':37,'method':'微信支付','at':'2026-09-23T09:00:00+08:00'}],'created_at':'2026-09-23T09:00:00+08:00'}})
    if app=='wangyiyun':
        records += [{'app_id':app,'entity_type':'play_history','semantic_ref':'trace-play','data':{'item_id':f'trace-{res}','playing':True}}, {'app_id':app,'entity_type':'user_playlist','semantic_ref':'trace-list','data':{'name':'验收歌单','songs':[f'trace-{res}']}}]
    if app=='chaowei':records.append({'app_id':app,'entity_type':'note','semantic_ref':'trace-note','data':{'item_id':f'trace-{res}','text':'验收想法'}})
    v.api(f'/local/v1/apps/{app}/seed',{'fixture':{'episode_scope':episode,'common_records':records,'world_deltas':[]},'idempotency_key':episode},True)
    checks=[]
    try:
        v.adb('shell','am','force-stop',f'com.ku.simulator.{app}')
        v.adb('shell','am','start','-n',f'com.ku.simulator.{app}/.MainActivity')
        for _ in range(20):
            time.sleep(.5)
            try:
                if v.cdp(app,"document.querySelector('main')?.dataset.ready")=='true':break
            except Exception:pass
        else:raise AssertionError(f'{app}: not ready')
        popup=v.cdp(app,"document.querySelector('.popup')?.innerText") or ''
        checks.append(('popup.title/message',f'验收通知{app}' in popup and f'验收消息{app}' in popup))
        v.click(app,'.popup button')
        home=v.cdp(app,"document.querySelector('.home')?.innerText") or ''
        v.click(app,entry_selector[app])
        if app in ('dudu','lianqi','babaquna'):v.click(app,'.flow-form .primary-action')
        service=v.cdp(app,"document.querySelector('.flow-page')?.innerText") or ''
        checks.append((f'{res}.title/subtitle',title in service and f'验收说明{app}' in service))
        service_shot=v.shot(app,'current-service')
        if app=='chaowei':
            v.click(app,'.flow-list button')
            detail=v.cdp(app,"document.querySelector('.reader-detail')?.innerText") or ''
            checks.append(('articles.body',f'验收正文{app}' in detail))
            detail_shot=v.shot(app,'current-detail')
        if app=='wangyiyun':
            player=v.cdp(app,"document.querySelector('.mini-player')?.innerText") or ''
            checks.append(('play_history.playing',title in player and '正在播放' in player))
        v.click(app,'.bottom-nav button',1)
        records_text=v.cdp(app,"document.querySelector('.records')?.innerText") or ''
        if app not in ('wangyiyun','chaowei'):
            checks.append(('order.list',f'验收订单{app}' in records_text))
            v.click(app,'.order-links button')
            detail=v.cdp(app,"document.querySelector('.detail-page')?.innerText") or ''
            checks.append(('order.details',all(x in detail for x in ('验收起点','验收终点','¥42','¥37','−¥5','验收优惠','微信支付','已支付'))))
            detail_shot=v.shot(app,'current-detail')
        if app=='wangyiyun':checks.append(('user_playlist.name','验收歌单' in records_text))
        if app=='chaowei':checks.append(('note.text','验收想法' in records_text))
        v.click(app,'.bottom-nav button',2)
        profile=v.cdp(app,"document.querySelector('.profile')?.innerText") or ''
        checks.append(('ui_content.profile_name',f'验收用户{app}' in profile))
        profile_shot=v.shot(app,'current-profile')
        idx=next(i for i,e in enumerate(cfg['accountMenu']) if e['kind']=='form')
        v.click(app,'.settings-list button',idx)
        account=v.cdp(app,"document.querySelector('.account-section input')?.value") or ''
        checks.append(('account_field.value',account==f'验收账户{app}'))
        state=v.api(f'/local/v1/apps/{app}/read_state',{},True)['data']
        typed={x['entity_type']:x for x in state['typed_entities'] if x['episode_scope']==episode}
        for record in records:
            entity=record['entity_type'];ref=record['semantic_ref']
            catalog=json.loads((root/'contracts/entity-catalog'/f'{app}.json').read_text())
            op=next(e['query_operations'][-1]['name'] for e in catalog['entity_types'] if e['name']==entity)
            payload={'operation':op}
            if op.startswith('get_'):payload['selector']={'semantic_ref':ref}
            result=v.api(f'/local/v1/apps/{app}/query',payload)
            checks.append((f'{entity}.query',result['status']=='ok' and result['coverage']['complete'] and entity in typed))
        width=v.cdp(app,'({viewport:document.documentElement.clientWidth,content:document.documentElement.scrollWidth})')
        checks.append(('viewport.width',width['content']<=width['viewport']+2))
        passed=all(ok for _,ok in checks)
        proof={'app':app,'passed':passed,'checks':[{'field':name,'passed':ok} for name,ok in checks],'visual_metrics':width,'episode_scope':episode,'screenshots':{'service':service_shot,'detail':locals().get('detail_shot'),'profile':profile_shot}}
        reports[app]=proof
        (v.OUT/f'{app}-current.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    finally:
        v.api(f'/local/v1/apps/{app}/teardown',{'episode_scope':episode},True)
    print(json.dumps({'app':app,'passed':passed,'checks':checks},ensure_ascii=False),flush=True)
(v.RUNTIME/'current-provenance-report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
assert all(x['passed'] for x in reports.values())
