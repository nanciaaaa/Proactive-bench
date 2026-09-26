#!/usr/bin/env python3
"""P1 类型化数据层验收测试：seed 幂等、patch 语义、query 投影/coverage/错误区分、
read_state、四类数据注入与 GUI 路由同源、无硬编码业务数据残留。"""
import json, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def call(base, path, method='GET', data=None, key=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['X-Runner-Key'] = key
    req = urllib.request.Request(f'{base}{path}', method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


class TypedDataLayerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='typed-layer-')
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'local-controller/server.py'),
             '--instance-id', 'typed-test-1', '--runtime', f'{cls.tmp.name}/instance',
             '--port', '0', '--seed', '42'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.port = json.loads(cls.proc.stdout.readline())['port']
        cls.base = f'http://127.0.0.1:{cls.port}'
        for _ in range(30):
            try:
                call(cls.base, '/health')
                break
            except Exception:
                time.sleep(0.2)
        info = json.loads(Path(cls.tmp.name, 'instance/instance.json').read_text())
        cls.key = info['runner_key']

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def seed(self, app, fixture, key=None):
        return call(self.base, f'/local/v1/apps/{app}/seed', 'POST',
                    {'fixture': fixture, 'idempotency_key': key}, self.key)

    def query(self, app, body):
        return call(self.base, f'/local/v1/apps/{app}/query', 'POST', body)

    def test_seed_display_entity_readback_matches_gui_route(self):
        fixture = {'episode_scope': 'ep-disp', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'stations', 'semantic_ref': 'sta-north',
             'data': {'title': '上海南站', 'subtitle': '上海 · 轨道交通 1/3/15 号线',
                      'price': 12, 'available': 88, 'rank': 0}}], 'world_deltas': []}
        status, body = self.seed('starrail', fixture, 'key-disp')
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data']['applied'][0]['entity_type'], 'stations')
        # query 单条读回
        status, body = self.query('starrail', {'operation': 'get_station_details',
                                               'selector': {'semantic_ref': 'sta-north'},
                                               'projection': ['title', 'price', 'available']})
        self.assertEqual(status, 200)
        self.assertEqual(body['coverage'], {'scope': 'single_record', 'complete': True, 'as_of': None})
        self.assertEqual(body['data'], {'title': '上海南站', 'price': 12, 'available': 88})
        # GUI 数据路由同源（injected_records 相同）
        status, body = call(self.base, '/runtime/v1/apps/starrail/data/stations')
        first = body['items'][0]
        self.assertEqual(first['title'], '上海南站')
        self.assertEqual(first['price'], 12)
        # read_state 完整读回
        status, body = call(self.base, '/local/v1/apps/starrail/read_state', 'POST',
                            {'scope': ['stations']}, self.key)
        self.assertEqual(status, 200)
        typed = [t for t in body['data']['typed_entities'] if t['semantic_ref'] == 'sta-north']
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0]['data']['title'], '上海南站')

    def test_ui_content_seed_changes_runtime_state_and_query(self):
        data = {'hero': '今日出发', 'profile_name': '旅客甲',
                'planner': [{'label': '出发', 'value': '北京南'}]}
        fixture = {'episode_scope': 'ep-content', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'ui_content',
             'semantic_ref': 'home-content', 'data': data}], 'world_deltas': []}
        status, _ = self.seed('starrail', fixture, 'key-content')
        self.assertEqual(status, 200)
        status, body = self.query('starrail', {'operation': 'get_ui_content_details',
                                               'selector': {'semantic_ref': 'home-content'},
                                               'projection': ['hero', 'planner', 'profile_name']})
        self.assertEqual(status, 200)
        self.assertEqual(body['data'], data)
        status, body = call(self.base, '/runtime/v1/apps/starrail/state')
        self.assertEqual(body['state']['ui_content'], data)
        status, _ = call(self.base, '/local/v1/apps/starrail/teardown', 'POST',
                         {'episode_scope': 'ep-content'}, self.key)
        self.assertEqual(status, 200)
        status, body = call(self.base, '/runtime/v1/apps/starrail/state')
        self.assertNotIn('ui_content', body['state'])

    def test_seed_rejects_entities_outside_app_catalog(self):
        for app in ('wangyiyun', 'chaowei'):
            fixture = {'episode_scope': f'ep-no-coupon-{app}', 'common_records': [
                {'app_id': app, 'entity_type': 'coupon_wallet', 'semantic_ref': 'wrong-coupon',
                 'data': {'title': '不适用优惠'}}], 'world_deltas': []}
            status, body = self.seed(app, fixture)
            self.assertEqual(status, 400)
            self.assertEqual(body['error']['code'], 'INVALID_FIXTURE')

    def test_as_of_compares_instants_across_timezones(self):
        fixture = {'episode_scope': 'ep-timezone', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'stations', 'semantic_ref': 'sta-timezone',
             'updated_at': '2026-09-23T09:00:00+08:00',
             'data': {'title': '时区站'}}], 'world_deltas': []}
        status, _ = self.seed('starrail', fixture, 'key-timezone')
        self.assertEqual(status, 200)
        request = {'operation': 'get_station_details',
                   'selector': {'semantic_ref': 'sta-timezone'}, 'projection': ['title']}
        status, body = self.query('starrail', request | {'as_of': '2026-09-23T01:30:00+00:00'})
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['title'], '时区站')
        status, body = self.query('starrail', request | {'as_of': '2026-09-23T00:30:00+00:00'})
        self.assertEqual(status, 404)
        self.assertEqual(body['error']['code'], 'NOT_FOUND')
        status, body = self.query('starrail', request | {'as_of': '2026-09-23T01:30:00'})
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'INVALID_AS_OF')

    def test_gui_order_uses_seeded_card_price(self):
        fixture = {'episode_scope': 'ep-price', 'common_records': [
            {'app_id': 'lianqi', 'entity_type': 'routes', 'semantic_ref': 'route-price',
             'data': {'title': '接送路线', 'price': 37, 'available': 7, 'rank': -1}}],
             'world_deltas': []}
        status, _ = self.seed('lianqi', fixture, 'key-price')
        self.assertEqual(status, 200)
        _, current = call(self.base, '/runtime/v1/apps/lianqi/state')
        status, body = call(self.base, '/runtime/v1/apps/lianqi/actions', 'POST',
                            {'action_id': 'gui-price-1', 'type': 'create_order',
                             'payload': {'item_id': 'route-price', 'expected_price': 37},
                             'expected_revision': current['revision']})
        self.assertEqual(status, 200)
        self.assertEqual(body['state']['orders'][-1]['price'], 37)

    def test_coupon_action_keeps_queryable_coupon_record(self):
        fixture = {'episode_scope': 'ep-coupon-action', 'common_records': [
            {'app_id': 'lianqi', 'entity_type': 'coupons', 'semantic_ref': 'coupon-action',
             'data': {'title': '满 50 减 10', 'subtitle': '满 50 减 10', 'price': 0, 'available': 7}},
            {'app_id': 'lianqi', 'entity_type': 'order', 'semantic_ref': 'coupon-order',
             'data': {'item_id': 'route-1', 'price': 64, 'status': 'CONFIRMED'}}],
             'world_deltas': []}
        status, _ = self.seed('lianqi', fixture, 'key-coupon-action')
        self.assertEqual(status, 200)
        _, current = call(self.base, '/runtime/v1/apps/lianqi/state')
        status, body = call(self.base, '/runtime/v1/apps/lianqi/actions', 'POST',
                            {'action_id': 'gui-coupon-1', 'type': 'use_coupon',
                             'payload': {'item_id': 'coupon-action'},
                             'expected_revision': current['revision']})
        self.assertEqual(status, 200)
        self.assertEqual(body['state']['coupons'][-1]['title'], '满 50 减 10')
        status, body = self.query('lianqi', {'operation': 'list_coupons'})
        self.assertEqual(status, 200)
        self.assertIn('满 50 减 10', [row['title'] for row in body['data']])

    def test_seed_idempotent_same_fixture_same_key(self):
        fixture = {'episode_scope': 'ep-idem', 'common_records': [
            {'app_id': 'dudu', 'entity_type': 'places', 'semantic_ref': 'plc-a',
             'data': {'title': '世纪公园', 'subtitle': '浦东新区 · 城市绿地', 'price': 20, 'available': 5, 'rank': 0}}],
            'world_deltas': []}
        _, first = self.seed('dudu', fixture, 'key-idem')
        _, second = self.seed('dudu', fixture, 'key-idem')
        self.assertEqual(first, second)
        status, body = self.query('dudu', {'operation': 'search_places', 'selector': {'keyword': '世纪公园'}})
        self.assertEqual(status, 200)
        self.assertEqual(len(body['data']), 1)
        status, body = call(self.base, '/runtime/v1/apps/dudu/data/places')
        self.assertEqual(len([x for x in body['items'] if x['title'] == '世纪公园']), 1)

    def test_seed_same_key_different_content_rejected(self):
        fixture = {'episode_scope': 'ep-reuse', 'common_records': [
            {'app_id': 'dudu', 'entity_type': 'places', 'semantic_ref': 'plc-b',
             'data': {'title': '静安寺', 'price': 20}}], 'world_deltas': []}
        self.seed('dudu', fixture, 'key-reuse')
        fixture['common_records'][0]['data']['title'] = '外滩'
        status, body = self.seed('dudu', fixture, 'key-reuse')
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'IDEMPOTENCY_KEY_REUSED')

    def test_seed_user_state_entities_readback_via_query_and_state(self):
        fixture = {'episode_scope': 'ep-user', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'order', 'semantic_ref': 'ord-1',
             'data': {'item_id': 'G1234', 'price': 553, 'status': 'CONFIRMED', 'created_at': '2026-09-21T10:00:00+08:00'}},
            {'app_id': 'starrail', 'entity_type': 'favorite', 'semantic_ref': 'fav-1',
             'data': {'item_id': 'G1234'}},
            {'app_id': 'starrail', 'entity_type': 'passenger', 'semantic_ref': 'pas-1',
             'data': {'name': '张三'}}], 'world_deltas': []}
        status, body = self.seed('starrail', fixture, 'key-user')
        self.assertEqual(status, 200)
        status, body = self.query('starrail', {'operation': 'list_orders'})
        self.assertEqual(status, 200)
        self.assertTrue(body['coverage']['complete'])
        self.assertIn({'semantic_ref': 'ord-1', 'item_id': 'G1234', 'price': 553,
                       'status': 'CONFIRMED', 'created_at': '2026-09-21T10:00:00+08:00'}, body['data'])
        status, body = self.query('starrail', {'operation': 'list_passengers'})
        self.assertEqual(body['data'], [{'semantic_ref': 'pas-1', 'name': '张三', 'phone': ''}])
        # GUI state 路由同源
        status, body = call(self.base, '/runtime/v1/apps/starrail/state')
        self.assertIn({'id': 'ord-1', 'item_id': 'G1234', 'price': 553, 'status': 'CONFIRMED',
                       'created_at': '2026-09-21T10:00:00+08:00'}, body['state']['orders'])
        self.assertIn('G1234', body['state']['favorites'])
        self.assertIn({'name': '张三', 'id': 'pas-1'}, body['state']['passengers'])

    def test_seed_popup_overrides_popup_queue(self):
        fixture = {'episode_scope': 'ep-popup', 'common_records': [
            {'app_id': 'wangyiyun', 'entity_type': 'popup', 'semantic_ref': 'popup-promo',
             'data': {'kind': 'promotion', 'title': '限时优惠', 'message': '本周精选权益已经到账。'}}],
            'world_deltas': []}
        status, body = self.seed('wangyiyun', fixture, 'key-popup')
        self.assertEqual(status, 200)
        status, body = self.query('wangyiyun', {'operation': 'list_active_popups'})
        self.assertEqual(status, 200)
        self.assertEqual(body['data'][0]['title'], '限时优惠')
        status, body = call(self.base, '/runtime/v1/apps/wangyiyun/state')
        self.assertEqual(body['state']['popup_queue'][0]['title'], '限时优惠')

    def test_seed_fault_entity_lands_in_faults(self):
        fixture = {'episode_scope': 'ep-fault', 'common_records': [
            {'app_id': 'lianqi', 'entity_type': 'fault', 'semantic_ref': 'fault-net',
             'data': {'kind': 'offline', 'path_pattern': '/runtime/', 'remaining': 1}}],
            'world_deltas': []}
        status, body = self.seed('lianqi', fixture, 'key-fault')
        self.assertEqual(status, 200)
        status, body = call(self.base, '/local/v1/faults')
        self.assertEqual(len(body['faults']), 1)
        self.assertEqual(body['faults'][0]['kind'], 'offline')

    def test_world_deltas_patch_semantics(self):
        base = {'episode_scope': 'ep-delta', 'common_records': [
            {'app_id': 'chaowei', 'entity_type': 'articles', 'semantic_ref': 'art-1',
             'data': {'title': '科学边界', 'subtitle': '一场关于未知的讨论', 'price': 10}}],
            'world_deltas': []}
        self.seed('chaowei', base, 'key-delta-base')
        # add 已存在
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'add', 'semantic_ref': 'art-1', 'set': {'title': 'x'},
             'allowed_changed_fields': ['art-1.title']}]}, 'key-delta-add')
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'PATCH_TARGET_EXISTS')
        # update 不存在
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'update', 'semantic_ref': 'art-missing', 'set': {'title': 'y'},
             'allowed_changed_fields': ['title']}]}, 'key-delta-upd')
        self.assertEqual(status, 404)
        self.assertEqual(body['error']['code'], 'PATCH_TARGET_NOT_FOUND')
        # update 实际变化集≠声明集
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'update', 'semantic_ref': 'art-1', 'set': {'title': '新标题'},
             'allowed_changed_fields': ['subtitle']}]}, 'key-delta-mis')
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'PATCH_FIELDS_MISMATCH')
        # 合法 update：record_version 递增
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'update', 'semantic_ref': 'art-1', 'set': {'title': '新标题'},
             'allowed_changed_fields': ['title']}]}, 'key-delta-ok')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['applied'][1], {'delta': 'update', 'semantic_ref': 'art-1'})
        status, body = self.query('chaowei', {'operation': 'get_article_details',
                                              'selector': {'semantic_ref': 'art-1'}})
        self.assertEqual(body['data']['title'], '新标题')
        self.assertEqual(body['record_version'], 'chaowei.v2')
        # delete 不存在
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'delete', 'semantic_ref': 'art-missing', 'set': {}}]}, 'key-delta-del')
        self.assertEqual(status, 404)
        self.assertEqual(body['error']['code'], 'PATCH_TARGET_NOT_FOUND')
        # 合法 delete
        status, body = self.seed('chaowei', {**base, 'world_deltas': [
            {'op': 'delete', 'semantic_ref': 'art-1', 'set': {},
             'allowed_changed_fields': ['art-1.title', 'art-1.subtitle', 'art-1.price']}]}, 'key-delta-del-ok')
        self.assertEqual(status, 200)
        status, body = self.query('chaowei', {'operation': 'get_article_details',
                                              'selector': {'semantic_ref': 'art-1'}})
        self.assertEqual(status, 404)
        self.assertEqual(body['status'], 'not_found')

    def test_query_projection_coverage_and_errors(self):
        fixture = {'episode_scope': 'ep-cov', 'common_records': [
            {'app_id': 'babaquna', 'entity_type': 'hotels', 'semantic_ref': f'hot-{i}',
             'data': {'title': f'测试酒店{i}', 'subtitle': f'城景{i}', 'price': 100 + i, 'available': 9 - i}}
            for i in range(3)], 'world_deltas': []}
        self.seed('babaquna', fixture, 'key-cov')
        # 非法 projection 字段
        status, body = self.query('babaquna', {'operation': 'search_hotels',
                                               'selector': {'keyword': '测试'},
                                               'projection': ['title', 'secret_field']})
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'INVALID_PROJECTION')
        # 非法 selector 字段
        status, body = self.query('babaquna', {'operation': 'search_hotels',
                                               'selector': {'nope': 1}, 'projection': ['title']})
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'INVALID_OPERATION')
        # 未知 operation
        status, body = self.query('babaquna', {'operation': 'hack_everything'})
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'INVALID_OPERATION')
        # 非法 as_of
        status, body = self.query('babaquna', {'operation': 'search_hotels', 'as_of': 123})
        self.assertEqual(status, 400)
        self.assertEqual(body['error']['code'], 'INVALID_AS_OF')
        # projection 只返回声明字段
        status, body = self.query('babaquna', {'operation': 'search_hotels',
                                               'selector': {'keyword': '测试'},
                                               'projection': ['title']})
        self.assertEqual(status, 200)
        self.assertTrue(all(set(r) == {'semantic_ref', 'title'} for r in body['data']))
        self.assertTrue(body['coverage']['complete'])
        self.assertEqual(body['coverage']['scope'], 'query_scope')
        # limit 截断 → complete=false
        status, body = self.query('babaquna', {'operation': 'search_hotels',
                                               'selector': {'keyword': '测试', 'limit': 1},
                                               'projection': ['title']})
        self.assertEqual(len(body['data']), 1)
        self.assertFalse(body['coverage']['complete'])
        # 空结果 ≠ not_found：无匹配关键词返回 ok + 空列表 + complete=true
        status, body = self.query('babaquna', {'operation': 'search_hotels',
                                               'selector': {'keyword': '不存在的内容'}})
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data'], [])
        self.assertTrue(body['coverage']['complete'])
        # 单条不存在 → not_found 404（区别于空列表）
        status, body = self.query('babaquna', {'operation': 'get_hotel_details',
                                               'selector': {'semantic_ref': 'hot-missing'}})
        self.assertEqual(status, 404)
        self.assertEqual(body['status'], 'not_found')

    def test_query_needs_no_key_but_seed_does(self):
        status, _ = self.query('dudu', {'operation': 'search_places'})
        self.assertEqual(status, 200)
        status, body = call(self.base, '/local/v1/apps/dudu/seed', 'POST',
                            {'fixture': {'episode_scope': 'x', 'common_records': [], 'world_deltas': []}})
        self.assertEqual(status, 403)
        self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')

    def test_no_hardcoded_business_data_in_shared_template(self):
        main = (ROOT / 'apps/shared/src/main.js').read_text()
        self.assertNotIn('||3', main, '账户页优惠券数硬编码 3 未清理')
        self.assertNotIn('coupons?.length||3', main)


if __name__ == '__main__':
    unittest.main()
