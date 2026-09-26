#!/usr/bin/env python3
"""P3 episode 生命周期验收测试：teardown 隔离与足迹消失、freeze 拒写读可用、
user_memory 默认 feature_off、advance_time 留接口、seed journal 记录 episode_scope。"""
import json, sqlite3, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
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


def _controller(tmp, instance_id):
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / 'local-controller/server.py'),
         '--instance-id', instance_id, '--runtime', f'{tmp.name}/instance',
         '--port', '0', '--seed', '42'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    port = json.loads(proc.stdout.readline())['port']
    base = f'http://127.0.0.1:{port}'
    for _ in range(30):
        try:
            call(base, '/health')
            break
        except Exception:
            time.sleep(0.2)
    info = json.loads(Path(tmp.name, 'instance/instance.json').read_text())
    return proc, base, info['runner_key']


class EpisodeLifecycleCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ep-life-')
        cls.proc, cls.base, cls.key = _controller(cls.tmp, 'ep-test-1')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def seed(self, app, fixture, key):
        return call(self.base, f'/local/v1/apps/{app}/seed', 'POST',
                    {'fixture': fixture, 'idempotency_key': key}, self.key)

    def query(self, app, body, key=None):
        return call(self.base, f'/local/v1/apps/{app}/query', 'POST', body, key)

    def test_teardown_removes_episode_footprint_and_restores_baseline(self):
        app = 'dudu'
        fixture = {'episode_scope': 'ep-td', 'common_records': [
            {'app_id': app, 'entity_type': 'places', 'semantic_ref': 'plc-td',
             'data': {'title': '环球港', 'subtitle': '普陀 · 环球港商圈', 'price': 30, 'available': 5, 'rank': 0}},
            {'app_id': app, 'entity_type': 'order', 'semantic_ref': 'ord-td',
             'data': {'item_id': 'plc-td', 'status': 'CONFIRMED', 'price': 30, 'created_at': '2026-09-20T09:01:00+08:00'}},
            {'app_id': app, 'entity_type': 'favorite', 'semantic_ref': 'fav-td',
             'data': {'item_id': 'plc-fav-1'}},
            {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'pop-td',
             'data': {'kind': 'promotion', 'title': '限时特惠', 'message': '仅限今日。'}},
            {'app_id': app, 'entity_type': 'fault', 'semantic_ref': 'flt-td',
             'data': {'kind': 'timeout', 'path_pattern': '/runtime/v1/apps/dudu/data/', 'status': 503, 'remaining': 2}},
        ], 'world_deltas': []}
        status, body = self.seed(app, fixture, 'key-td')
        self.assertEqual(status, 200, body)
        # 注入后可见
        status, body = self.query(app, {'operation': 'search_places',
                                        'selector': {'keyword': '环球港'}, 'projection': ['title']})
        self.assertEqual(body['data'][0]['title'], '环球港')
        status, body = call(self.base, '/runtime/v1/apps/dudu/state')
        self.assertEqual(body['state']['orders'][0]['id'], 'ord-td')
        self.assertIn('plc-fav-1', body['state']['favorites'])
        status, body = call(self.base, '/local/v1/faults')
        self.assertEqual(body['faults'][0]['kind'], 'timeout')
        # teardown
        status, body = call(self.base, f'/local/v1/apps/{app}/teardown', 'POST',
                            {'episode_scope': 'ep-td'}, self.key)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data']['removed']['typed_entities'], 5)
        # 足迹消失
        status, body = self.query(app, {'operation': 'search_places',
                                        'selector': {'keyword': '环球港'}, 'projection': ['title']})
        self.assertEqual(body['data'], [])
        status, body = call(self.base, '/runtime/v1/apps/dudu/state')
        self.assertEqual(body['state']['orders'], [])
        self.assertNotIn('plc-fav-1', body['state']['favorites'])
        status, body = call(self.base, '/local/v1/faults')
        self.assertEqual(body['faults'], [])
        # popup 恢复默认无弹窗
        status, body = call(self.base, '/runtime/v1/apps/dudu/state')
        self.assertEqual(body['state']['popup_queue'], [])
        # journal 保留 seed 与 teardown，供 Runner 审计整个 episode。
        status, body = call(self.base, f'/local/v1/apps/{app}/mutations?since_cursor=0', 'GET', None, self.key)
        actors = [m['actor'] for m in body['data']['mutations']]
        self.assertIn('runner_teardown', actors)
        self.assertIn('runner_seed', actors)
        # seed 收据已删除：同 key 重播 = 重新注入而非返回旧收据
        status, body = self.seed(app, fixture, 'key-td')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['data']['applied']), 5)

    def test_teardown_preserves_other_episode(self):
        app = 'lianqi'
        ep_a = {'episode_scope': 'ep-a', 'common_records': [
            {'app_id': app, 'entity_type': 'routes', 'semantic_ref': 'rte-a',
             'data': {'title': '路线甲', 'subtitle': 'A', 'price': 10, 'available': 1, 'rank': 0}}], 'world_deltas': []}
        ep_b = {'episode_scope': 'ep-b', 'common_records': [
            {'app_id': app, 'entity_type': 'routes', 'semantic_ref': 'rte-b',
             'data': {'title': '路线乙', 'subtitle': 'B', 'price': 20, 'available': 2, 'rank': 1}},
            {'app_id': app, 'entity_type': 'order', 'semantic_ref': 'ord-b',
             'data': {'item_id': 'rte-b', 'status': 'CONFIRMED', 'price': 20, 'created_at': '2026-09-20T09:02:00+08:00'}}], 'world_deltas': []}
        self.assertEqual(self.seed(app, ep_a, 'k-a')[0], 200)
        self.assertEqual(self.seed(app, ep_b, 'k-b')[0], 200)
        status, body = call(self.base, f'/local/v1/apps/{app}/teardown', 'POST',
                            {'episode_scope': 'ep-a'}, self.key)
        self.assertEqual(status, 200)
        # ep-a 消失、ep-b 保留
        status, body = self.query(app, {'operation': 'search_routes',
                                        'selector': {'keyword': '路线'}, 'projection': ['title']})
        titles = [x['title'] for x in body['data']]
        self.assertNotIn('路线甲', titles)
        self.assertIn('路线乙', titles)
        status, body = call(self.base, '/runtime/v1/apps/lianqi/state')
        self.assertEqual([o['id'] for o in body['state']['orders']], ['ord-b'])
        # seed 收据只删 ep-a：k-a 重播重新注入、k-b 重播命中旧收据（applied 为原始列表且无重复）
        status, body = self.seed(app, ep_b, 'k-b')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['data']['applied']), 2)  # 原始收据的 applied
        status, body = self.query(app, {'operation': 'search_routes',
                                        'selector': {'keyword': '路线乙'}, 'projection': ['title']})
        self.assertEqual(len(body['data']), 1)  # 未重复注入

    def test_teardown_requires_runner_key(self):
        status, body = call(self.base, '/local/v1/apps/dudu/teardown', 'POST',
                            {'episode_scope': 'ep-x'})
        self.assertEqual(status, 403)
        self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')
        status, body = call(self.base, '/local/v1/apps/dudu/teardown', 'POST', {})
        self.assertEqual(status, 403)  # 未过凭据关，先于参数校验

    def test_teardown_missing_episode_scope_returns_episode_not_found(self):
        status, body = call(self.base, '/local/v1/apps/dudu/teardown', 'POST',
                            {'episode_scope': ''}, self.key)
        self.assertEqual(status, 404)
        self.assertEqual(body['error']['code'], 'EPISODE_NOT_FOUND')

    def test_freeze_rejects_writes_and_reads_still_work(self):
        app = 'starrail'
        # 预热：先注入一条数据供读回
        fixture = {'episode_scope': 'ep-fz', 'common_records': [
            {'app_id': app, 'entity_type': 'stations', 'semantic_ref': 'sta-fz',
             'data': {'title': '虹桥站', 'subtitle': '上海', 'price': 5, 'available': 50, 'rank': 0}}], 'world_deltas': []}
        self.assertEqual(self.seed(app, fixture, 'k-fz')[0], 200)
        status, body = call(self.base, '/local/v1/instance/freeze', 'POST', {}, self.key)
        self.assertEqual(status, 200)
        self.assertTrue(body['frozen'])
        # 业务写入全部被拒
        status, body = call(self.base, f'/runtime/v1/apps/{app}/actions', 'POST',
                            {'action_id': 'a-fz-1', 'type': 'favorite', 'payload': {'item_id': 'sta-fz'}, 'expected_revision': 0})
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'SESSION_FROZEN')
        status, body = self.seed(app, {'episode_scope': 'ep-fz2', 'common_records': [], 'world_deltas': []}, 'k-fz2')
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'SESSION_FROZEN')
        status, body = call(self.base, f'/local/v1/apps/{app}/data/stations', 'POST',
                            {'items': [{'title': '注入站'}]}, self.key)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'SESSION_FROZEN')
        # 读仍可用
        status, body = self.query(app, {'operation': 'search_stations',
                                        'selector': {'keyword': '虹桥站'}, 'projection': ['title']})
        self.assertEqual(status, 200)
        self.assertEqual(body['data'][0]['title'], '虹桥站')
        status, body = call(self.base, '/runtime/v1/apps/starrail/state')
        self.assertEqual(status, 200)
        status, body = call(self.base, f'/local/v1/apps/{app}/mutations?since_cursor=0', 'GET', None, self.key)
        self.assertEqual(status, 200)
        # 解冻后写入恢复
        status, body = call(self.base, '/local/v1/instance/unfreeze', 'POST', {}, self.key)
        self.assertEqual(status, 200)
        self.assertFalse(body['frozen'])
        status, body = call(self.base, f'/runtime/v1/apps/{app}/state')
        rev = body['revision']
        status, body = call(self.base, f'/runtime/v1/apps/{app}/actions', 'POST',
                            {'action_id': 'a-fz-2', 'type': 'favorite', 'payload': {'item_id': 'sta-fz'}, 'expected_revision': rev})
        self.assertEqual(status, 200, body)
        self.assertIn('sta-fz', body['state']['favorites'])

    def test_freeze_unfreeze_require_runner_key(self):
        for path in ('/local/v1/instance/freeze', '/local/v1/instance/unfreeze',
                     '/local/v1/instance/advance_time', '/local/v1/instance/feature_flag'):
            status, body = call(self.base, path, 'POST', {})
            self.assertEqual(status, 403, f'{path} 未落入管理分组')
            self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')

    def test_user_memory_default_feature_off_and_flag_enables(self):
        status, body = call(self.base, '/local/v1/apps/user_memory/query', 'POST', {}, self.key)
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'feature_off')
        self.assertIsNone(body['data'])
        # 打开 feature flag 后 envelope 正常
        status, body = call(self.base, '/local/v1/instance/feature_flag', 'POST',
                            {'flag': 'user_memory_enabled', 'enabled': True}, self.key)
        self.assertEqual(status, 200)
        status, body = call(self.base, '/local/v1/apps/user_memory/query', 'POST', {}, self.key)
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data']['memories'], [])
        # 复原默认关闭
        status, body = call(self.base, '/local/v1/instance/feature_flag', 'POST',
                            {'flag': 'user_memory_enabled', 'enabled': False}, self.key)
        self.assertEqual(status, 200)
        status, body = call(self.base, '/local/v1/apps/user_memory/query', 'POST', {}, self.key)
        self.assertEqual(body['status'], 'feature_off')

    def test_user_memory_query_requires_runner_key(self):
        status, body = call(self.base, '/local/v1/apps/user_memory/query', 'POST', {})
        self.assertEqual(status, 403)
        self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')

    def test_advance_time_advances_logical_time_without_exogenous_events(self):
        status, body = call(self.base, '/local/v1/clock')
        before = body['simulated_time']
        status, body = call(self.base, '/local/v1/instance/advance_time', 'POST',
                            {'logical_time': '2026-09-20T10:00:00+08:00'}, self.key)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data']['logical_time'], '2026-09-20T10:00:00+08:00')
        self.assertEqual(body['data']['applied_events'], [])
        self.assertIsInstance(body['data']['passive_cursor'], int)
        status, body = call(self.base, '/local/v1/clock')
        self.assertEqual(body['simulated_time'], '2026-09-20T10:00:00+08:00')
        self.assertNotEqual(before, body['simulated_time'])

    def test_advance_time_invalid_time_rejected(self):
        for value in (None, 'tomorrow', '2026-09-20T10:00:00', '2026-09-19T10:00:00+08:00'):
            status, body = call(self.base, '/local/v1/instance/advance_time', 'POST',
                                {'logical_time': value}, self.key)
            self.assertEqual(status, 400)
            self.assertEqual(body['error']['code'], 'INVALID_SIMULATED_TIME')

    def test_seed_journal_records_episode_scope(self):
        app = 'babaquna'
        fixture = {'episode_scope': 'ep-j', 'common_records': [
            {'app_id': app, 'entity_type': 'flights', 'semantic_ref': 'flt-j',
             'data': {'title': '航班甲', 'subtitle': '上海-北京', 'price': 500, 'available': 9, 'rank': 0}}], 'world_deltas': []}
        self.assertEqual(self.seed(app, fixture, 'k-j')[0], 200)
        status, body = call(self.base, f'/local/v1/apps/{app}/mutations?since_cursor=0', 'GET', None, self.key)
        self.assertEqual(status, 200)
        seed_rows = [m for m in body['data']['mutations'] if m['actor'] == 'runner_seed']
        self.assertTrue(seed_rows)
        self.assertTrue(all(m['episode_scope'] == 'ep-j' for m in seed_rows))


class TeardownFailureCase(unittest.TestCase):
    """teardown 恢复失败 → TEARDOWN_FAILED(500) 且实例进 FAILED。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ep-fail-')
        cls.proc, cls.base, cls.key = _controller(cls.tmp, 'ep-test-2')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def test_teardown_failure_marks_instance_failed(self):
        # 破坏 state_json，使 teardown 恢复时抛错
        db_path = Path(self.tmp.name, 'instance/state.sqlite')
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("UPDATE app_state SET state_json='{broken' WHERE app_id='wangyiyun'")
        conn.commit()
        conn.close()
        status, body = call(self.base, '/local/v1/apps/wangyiyun/teardown', 'POST',
                            {'episode_scope': 'ep-nope'}, self.key)
        self.assertEqual(status, 500)
        self.assertEqual(body['error']['code'], 'TEARDOWN_FAILED')
        status, body = call(self.base, '/local/v1/instance/status')
        self.assertEqual(body['lifecycle'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
