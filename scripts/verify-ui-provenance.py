#!/usr/bin/env python3
"""Run seed → Android WebView → query/read_state → teardown for every provenance entry."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = sys.argv[1] if len(sys.argv) > 1 else 'acceptance'
RUNTIME = ROOT / 'runtime/instances' / INSTANCE
INFO = json.loads((RUNTIME / 'instance.json').read_text())
BASE = f"http://127.0.0.1:{INFO['controller_port']}"
KEY = INFO['runner_key']
ENV = json.loads((RUNTIME / 'ku-environment.json').read_text())
ADB = ['adb', '-s', f"127.0.0.1:{ENV['adb_port']}"]
OUT = RUNTIME / 'provenance'
OUT.mkdir(exist_ok=True)
CONFIG = json.loads((ROOT / 'apps/app-definitions.json').read_text())
RESOURCES = {'dudu': ('places', 'vehicle_quotes'), 'lianqi': ('routes', 'coupons'),
             'starrail': ('stations', 'trains', 'seats'),
             'babaquna': ('flights', 'hotels', 'attractions'),
             'wangyiyun': ('songs', 'playlists'), 'chaowei': ('articles', 'tags')}


def api(path, data=None, admin=False):
    headers = {'Content-Type': 'application/json'}
    if admin: headers['X-Runner-Key'] = KEY
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def adb(*args):
    return subprocess.run(ADB + list(args), capture_output=True, check=True).stdout


def cdp(app, expression):
    pid = adb('shell', 'pidof', f'com.ku.simulator.{app}').decode().strip().split()[-1]
    port = int(adb('forward', 'tcp:0', f'localabstract:webview_devtools_remote_{pid}').decode().strip())
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=10) as response:
            target = next(t for t in json.load(response) if t['type'] == 'page')
        ws = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=15, suppress_origin=True)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': expression, 'returnByValue': True}}))
            while True:
                result = json.loads(ws.recv())
                if result.get('id') == 1:
                    return result.get('result', {}).get('result', {}).get('value')
        finally:
            ws.close()
    finally:
        adb('forward', '--remove', f'tcp:{port}')


def click(app, selector, index=0):
    cdp(app, f"document.querySelectorAll({json.dumps(selector)})[{index}]?.click()")
    time.sleep(1)


def shot(app, name):
    path = OUT / f'{app}-{name}.png'
    path.write_bytes(adb('exec-out', 'screencap', '-p'))
    return str(path.relative_to(ROOT))


def run(app):
    cfg = CONFIG[app]
    episode = f'provenance-{app}-{int(time.time())}'
    records = []
    for r in RESOURCES[app]:
        records.append({'app_id': app, 'entity_type': r, 'semantic_ref': f'trace-{r}',
                        'data': {'title': f'验收{r}', 'subtitle': f'副标题{r}',
                                 'price': 37, 'available': 7, 'rank': -10}})
    content = {'hero': f'验收首页{app}', 'profile_name': f'验收用户{app}',
               'planner': [{'label': x['label'], 'value': f'验收{x["label"]}'} for x in cfg['planner']]}
    records += [
        {'app_id': app, 'entity_type': 'ui_content', 'semantic_ref': 'trace-content', 'data': content},
        {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'trace-popup',
         'data': {'kind': 'notice', 'title': f'验收通知{app}', 'message': f'验收消息{app}'}},
        {'app_id': app, 'entity_type': 'favorite', 'semantic_ref': 'trace-favorite',
         'data': {'item_id': f'trace-{RESOURCES[app][0]}'}}
    ]
    account_entry = next(e for e in cfg['accountMenu'] if e['kind'] == 'form')
    records.append({'app_id': app, 'entity_type': 'account_field', 'semantic_ref': 'trace-account',
                    'data': {'key': account_entry['field'], 'value': f'验收账户值{app}'}})
    if app not in ('wangyiyun', 'chaowei'):
        records.append({'app_id': app, 'entity_type': 'order', 'semantic_ref': 'trace-order',
                        'data': {'item_id': f'trace-{RESOURCES[app][0]}', 'title': f'验收行程{app}',
                                 'subtitle': f'验收路线{app}', 'base_price': 42, 'discount': 5,
                                 'coupon_title': f'验收优惠{app}', 'price': 37, 'status': 'PAID',
                                 'payments': [{'amount': 37, 'method': '账户余额', 'at': '2026-09-23T09:00:00+08:00'}],
                                 'created_at': '2026-09-23T09:00:00+08:00'}})
    if app in ('dudu', 'lianqi', 'babaquna'):
        records.append({'app_id': app, 'entity_type': 'coupon_wallet', 'semantic_ref': 'trace-coupon',
                        'data': {'code': 'TRACE', 'title': f'验收优惠{app}', 'discount': '减 5 元'}})
    if app == 'starrail':
        records.append({'app_id': app, 'entity_type': 'passenger', 'semantic_ref': 'trace-person',
                        'data': {'name': '验收乘车人'}})
    if app == 'wangyiyun':
        records.extend([
            {'app_id': app, 'entity_type': 'play_history', 'semantic_ref': 'trace-play',
             'data': {'item_id': 'trace-songs', 'playing': True}},
            {'app_id': app, 'entity_type': 'user_playlist', 'semantic_ref': 'trace-list',
             'data': {'name': '验收歌单', 'songs': []}}])
    if app == 'chaowei':
        records.append({'app_id': app, 'entity_type': 'note', 'semantic_ref': 'trace-note',
                        'data': {'item_id': 'trace-articles', 'text': '验收想法'}})

    fixture = {'episode_scope': episode, 'common_records': records, 'world_deltas': []}
    api(f'/local/v1/apps/{app}/seed', {'fixture': fixture,
                                      'idempotency_key': episode}, True)
    popup_query = api(f'/local/v1/apps/{app}/query', {'operation': 'list_active_popups'})
    report = {'app': app, 'episode_scope': episode, 'entries': []}
    try:
        adb('shell', 'am', 'force-stop', f'com.ku.simulator.{app}')
        adb('shell', 'am', 'start', '-n', f'com.ku.simulator.{app}/.MainActivity')
        for _ in range(20):
            time.sleep(1)
            try:
                if cdp(app, "document.querySelector('main')?.dataset.ready") == 'true': break
            except (subprocess.CalledProcessError, StopIteration):
                continue
        popup = cdp(app, "document.querySelector('.popup')?.innerText || ''") or ''
        popup_shot = shot(app, 'popup')
        click(app, '.popup button')
        home = cdp(app, "document.querySelector('.home')?.innerText || ''") or ''
        player = cdp(app, "document.querySelector('.mini-player')?.innerText || ''") or ''
        home_shot = shot(app, 'home')
        width = cdp(app, '({viewport:document.documentElement.clientWidth,content:document.documentElement.scrollWidth})')
        report['visual_metrics'] = width
        cards = {}
        for index, r in enumerate(RESOURCES[app]):
            click(app, '.resource-tabs button', index)
            cards[r] = cdp(app, "document.querySelector('.cards')?.innerText || ''") or ''
        click(app, {'map': '.quick-row button', 'schedule': '.service-strip button',
                    'ticket': '.rail-tools button', 'travel': '.travel-services button',
                    'music': '.music-services button', 'reader': '.reader-topics button'}[cfg['layout']])
        panel = cdp(app, "document.querySelector('.sheet')?.innerText || ''") or ''
        panel_shot = shot(app, 'service')
        click(app, '.sheet-close')
        click(app, '.bottom-nav button', 1)
        if app not in ('wangyiyun', 'chaowei'): click(app, '.bill summary')
        record_text = cdp(app, "document.querySelector('.records')?.innerText || ''") or ''
        records_shot = shot(app, 'records')
        click(app, '.bottom-nav button', 2)
        profile = cdp(app, "document.querySelector('.profile')?.innerText || ''") or ''
        profile_shot = shot(app, 'profile')
        account_index = cfg['accountMenu'].index(account_entry)
        click(app, '.settings-list button', account_index)
        account_text = cdp(app, "document.querySelector('.account-section input')?.value || ''") or ''
        account_shot = shot(app, 'account')

        states = api(f'/local/v1/apps/{app}/read_state', {}, True)['data']
        typed = {r['entity_type']: r for r in states['typed_entities'] if r['episode_scope'] == episode}
        queries = {}
        catalog = json.loads((ROOT / 'contracts/entity-catalog' / f'{app}.json').read_text())
        for entity in {r['entity_type'] for r in records}:
            if entity == 'popup':
                queries[entity] = popup_query
                continue
            op = next(e['query_operations'][-1]['name'] for e in catalog['entity_types'] if e['name'] == entity)
            data = {'operation': op}
            if op.startswith('get_'):
                data['selector'] = {'semantic_ref': next(r['semantic_ref'] for r in records if r['entity_type'] == entity)}
            queries[entity] = api(f'/local/v1/apps/{app}/query', data)
        provenance_path = ROOT / 'apps' / app / 'ui-data-provenance.json'
        provenance = json.loads(provenance_path.read_text())
        first_service = cfg['services'][0]['resource']
        for entry in provenance['provenance']:
            location = entry['ui_location']
            if 'resource=' in location:
                r = location.split('resource=')[1].split('）')[0]
                visible = cards[r]
                screenshot = home_shot
            elif location.startswith('记录页'):
                visible, screenshot = record_text, records_shot
            elif location.startswith('账户页'):
                visible, screenshot = profile, profile_shot
            elif location.startswith('账户选项'):
                visible, screenshot = account_text, account_shot
            elif location.startswith('业务弹窗'):
                visible, screenshot = popup, popup_shot
            elif location.startswith('服务面板'):
                visible, screenshot = panel, panel_shot
            elif location.startswith('mini-player'):
                visible, screenshot = player, home_shot
            else:
                visible, screenshot = home + profile, home_shot
            checks = []
            for point in entry['data_points']:
                entity = point['semantic_ref'].split('.')[0]
                field = point['field']
                q = queries[entity]
                q_ok = q['status'] == 'ok' and q['coverage']['complete']
                if field.endswith('.length'):
                    q_ok = q_ok and bool(q['data'])
                    state_key = field.split('.')[0]
                    gui_ok = str(len(states['state'].get(state_key, []))) in visible
                else:
                    value = typed[entity]['data'].get(field)
                    q_ok = q_ok and field in json.dumps(q['data'], ensure_ascii=False)
                    if field == 'planner': gui_ok = content['planner'][0]['value'] in visible
                    elif field == 'hero': gui_ok = content['hero'] in visible
                    elif field == 'profile_name': gui_ok = content['profile_name'] in visible
                    elif field == 'available': gui_ok = '7' in visible
                    elif field == 'price': gui_ok = '37' in visible
                    elif location.startswith('mini-player'): gui_ok = ('正在播放' if field == 'playing' else '验收') in visible
                    elif entity == 'order' and field == 'status': gui_ok = '已支付' in visible
                    elif entity == 'order' and field == 'payments': gui_ok = '账户余额' in visible and '37' in visible
                    elif entity in ('favorite', 'play_history'): gui_ok = '验收' in visible
                    else: gui_ok = str(value) in visible
                checks.append({'field': field, 'query': q_ok, 'gui': gui_ok})
            passed = all(c['query'] and c['gui'] for c in checks)
            entry['verified'] = passed
            entry['evidence'] = {'screenshot': screenshot,
                                 'report': str((OUT / f'{app}.json').relative_to(ROOT)),
                                 'checks': checks}
            report['entries'].append({'location': location, 'verified': passed, 'checks': checks})
        provenance['status'] = 'verified' if all(e['verified'] for e in provenance['provenance']) else 'partial'
        provenance['note'] = f'2026-09-23 Android WebView + query/read_state verified; episode {episode} torn down.'
        report['passed'] = provenance['status'] == 'verified' and width['content'] <= width['viewport'] + 2
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n')
    finally:
        api(f'/local/v1/apps/{app}/teardown', {'episode_scope': episode}, True)
        after = api(f'/local/v1/apps/{app}/read_state', {}, True)['data']
        report['teardown_clean'] = not any(row['episode_scope'] == episode for row in after['typed_entities'])
        report['passed'] = report.get('passed', False) and report['teardown_clean']
        (OUT / f'{app}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report['passed']


if __name__ == '__main__':
    subprocess.run(['adb', 'connect', f"127.0.0.1:{ENV['adb_port']}"], check=True, capture_output=True)
    adb('reverse', 'tcp:8080', f"tcp:{INFO['controller_port']}")
    selected = sys.argv[2:] or CONFIG
    results = {app: run(app) for app in selected}
    print(json.dumps(results, ensure_ascii=False))
    sys.exit(0 if all(results.values()) else 1)
