#!/usr/bin/env python3
"""P2 契约验收测试：mutation journal（actor/版本区分）+ passive packet 流（弹窗→packet 映射、cursor 续拉）。
依赖 live controller：seed / actions / read_mutations / drain_events 路由全链路。
"""
import json, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _call(base, path, method='GET', body=None, headers=None):
    import urllib.error, urllib.request
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class JournalPacketCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='p2-journal-')
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'local-controller/server.py'),
             '--instance-id', 'p2-test-1', '--runtime', f'{cls.tmp.name}/instance',
             '--port', '0', '--seed', '42'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.proc = proc
        cls.port = json.loads(proc.stdout.readline())['port']
        cls.base = f'http://127.0.0.1:{cls.port}'
        for _ in range(30):
            try:
                _call(cls.base, '/health')
                break
            except Exception:
                time.sleep(0.2)
        cls.key = json.loads(Path(cls.tmp.name, 'instance/instance.json').read_text())['runner_key']

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    # ---------- journal：actor 与版本 ----------
    def _rev(self, app):
        _, body = _call(self.base, f'/runtime/v1/apps/{app}/state')
        return body['revision']

    def test_seed_journal_has_runner_seed_actor(self):
        _call(self.base, '/local/v1/apps/dudu/bootstrap')
        fixture = {'episode_scope': 'eps-a', 'common_records': [
            {'app_id': 'dudu', 'entity_type': 'order',
             'semantic_ref': 'eps-a-order-1', 'record_version': 3,
             'data': {'item_id': 'dudu-places-00042', 'status': 'CONFIRMED', 'price': 88}}],
            'world_deltas': []}
        status, body = _call(self.base, '/local/v1/apps/dudu/seed', 'POST', fixture,
                             {'X-Runner-Key': self.key})
        self.assertEqual(status, 200, body)
        _, body = _call(self.base, '/local/v1/apps/dudu/mutations?since_cursor=0', 'GET',
                        headers={'X-Runner-Key': self.key})
        muts = body['data']['mutations']
        self.assertTrue(any(m['actor'] == 'runner_seed' and m['operation'] == 'seed:order'
                            and m['semantic_ref'] == 'eps-a-order-1'
                            and m['after_version'] == 'dudu.v3' for m in muts),
                        f'缺 runner_seed journal：{muts}')

    def test_correct_operation_journal_committed_versions_advance(self):
        _call(self.base, '/runtime/v1/apps/dudu/bootstrap')
        rev = self._rev('dudu')
        _, body = _call(self.base, '/runtime/v1/apps/dudu/actions', 'POST',
                        {'action_id': 'a1', 'type': 'create_order', 'expected_revision': rev,
                         'payload': {'item_id': 'dudu-places-00007'}})
        self.assertEqual(body['success'], True)
        _, jb = _call(self.base, '/local/v1/apps/dudu/mutations?since_cursor=0', 'GET',
                      headers={'X-Runner-Key': self.key})
        committed = [m for m in jb['data']['mutations'] if m['status'] == 'committed'
                     and m['actor'] == 'agent_gui']
        self.assertEqual(len(committed), 1, jb)
        self.assertEqual(int(committed[0]['before_version'].removeprefix('dudu.v')) + 1,
                         int(committed[0]['after_version'].removeprefix('dudu.v')))
        self.assertIn('orders', committed[0]['changed_fields'])

    def test_wrong_object_operation_journal_rejected(self):
        _call(self.base, '/runtime/v1/apps/lianqi/bootstrap')
        rev = self._rev('lianqi')
        status, body = _call(self.base, '/runtime/v1/apps/lianqi/actions', 'POST',
                             {'action_id': 'a2', 'type': 'pay', 'expected_revision': rev,
                              'payload': {'item_id': 'lianqi-routes-00001'}})
        self.assertEqual(status, 404)
        self.assertEqual(body['error']['code'], 'ORDER_NOT_FOUND')
        _, jb = _call(self.base, '/local/v1/apps/lianqi/mutations?since_cursor=0', 'GET',
                      headers={'X-Runner-Key': self.key})
        rejected = [m for m in jb['data']['mutations'] if m['status'] == 'rejected']
        self.assertTrue(any(m['trace_ref'] == 'ORDER_NOT_FOUND' for m in rejected), jb)

    def test_replay_same_action_adds_no_journal(self):
        _call(self.base, '/runtime/v1/apps/chaowei/bootstrap')
        rev = self._rev('chaowei')
        req = {'action_id': 'a3', 'type': 'create_order', 'expected_revision': rev,
               'payload': {'item_id': 'chaowei-articles-00011'}}
        first, r1 = _call(self.base, '/runtime/v1/apps/chaowei/actions', 'POST', req)
        second, r2 = _call(self.base, '/runtime/v1/apps/chaowei/actions', 'POST', req)
        self.assertEqual(first, 200)
        self.assertEqual(r1, r2, '重放必须字节等价')
        _, jb = _call(self.base, '/local/v1/apps/chaowei/mutations?since_cursor=0', 'GET',
                      headers={'X-Runner-Key': self.key})
        committed = [m for m in jb['data']['mutations']
                     if m['status'] == 'committed' and m['actor'] == 'agent_gui']
        self.assertEqual(len(committed), 1, '重放不得产生第二条 journal')

    def test_noop_operation_journal_noop_versions_equal(self):
        _call(self.base, '/runtime/v1/apps/babaquna/bootstrap')
        # 先 seed 一个弹窗保证 popup_queue 非空，再 dismiss 两次：第一次改变状态，第二次 noop
        _call(self.base, '/local/v1/apps/babaquna/seed', 'POST',
              {'episode_scope': 'eps-n', 'common_records': [
                  {'app_id': 'babaquna', 'entity_type': 'popup', 'semantic_ref': 'eps-n-pop-1',
                   'data': {'kind': 'promotion', 'title': '优惠', 'message': '立减'}}],
               'world_deltas': []},
              {'X-Runner-Key': self.key})
        rev = self._rev('babaquna')
        _, body = _call(self.base, '/runtime/v1/apps/babaquna/actions', 'POST',
                        {'action_id': 'a4', 'type': 'dismiss_popup', 'expected_revision': rev,
                         'payload': {}})
        self.assertEqual(body['success'], True)
        rev = self._rev('babaquna')
        _, body = _call(self.base, '/runtime/v1/apps/babaquna/actions', 'POST',
                        {'action_id': 'a5', 'type': 'dismiss_popup', 'expected_revision': rev,
                         'payload': {}})
        self.assertEqual(body['success'], True)
        _, jb = _call(self.base, '/local/v1/apps/babaquna/mutations?since_cursor=0', 'GET',
                      headers={'X-Runner-Key': self.key})
        noops = [m for m in jb['data']['mutations'] if m['status'] == 'noop']
        self.assertEqual(len(noops), 1, jb)
        self.assertEqual(noops[0]['changed_fields'], [])
        self.assertEqual(noops[0]['before_version'], noops[0]['after_version'])

    # ---------- passive packets：映射与 cursor ----------
    def test_popup_seed_emits_app_event_packet(self):
        _call(self.base, '/local/v1/apps/lianqi/bootstrap')
        fixture = {'episode_scope': 'eps-b', 'common_records': [
            {'app_id': 'lianqi', 'entity_type': 'popup', 'semantic_ref': 'eps-b-pop-1',
             'data': {'kind': 'promotion', 'title': '限时折扣', 'message': '首单立减 10 元'}}],
            'world_deltas': []}
        _call(self.base, '/local/v1/apps/lianqi/seed', 'POST', fixture,
              {'X-Runner-Key': self.key})
        _, body = _call(self.base, '/local/v1/apps/lianqi/passive/events?cursor=0')
        packets = body['data']['packets']
        self.assertTrue(any(p['type'] == 'app_event' and p['source'] == 'lianqi'
                            and '限时折扣' in p['text_projection'] for p in packets), packets)
        self.assertTrue(all(p['delivered_at'] for p in packets))

    def test_gui_action_emits_user_action_packet(self):
        _call(self.base, '/runtime/v1/apps/starrail/bootstrap')
        rev = self._rev('starrail')
        _call(self.base, '/runtime/v1/apps/starrail/actions', 'POST',
              {'action_id': 'a6', 'type': 'create_order', 'expected_revision': rev,
               'payload': {'item_id': 'starrail-stations-00009'}})
        _, body = _call(self.base, '/local/v1/apps/starrail/passive/events?cursor=0')
        packets = body['data']['packets']
        self.assertTrue(any(p['type'] == 'user_action' and p['source'] == 'starrail'
                            and '订单' in p['text_projection'] for p in packets), packets)
        self.assertTrue(all('payload' not in p and 'app_id' not in p for p in packets))

    def test_fault_emits_notification_packet(self):
        _call(self.base, '/local/v1/apps/dudu/bootstrap')
        _call(self.base, '/local/v1/faults', 'POST',
              {'kind': 'offline', 'path_pattern': '/runtime/', 'remaining': 1},
              {'X-Runner-Key': self.key})
        status, _ = _call(self.base, '/runtime/v1/apps/dudu/data/places?offset=0&limit=1')
        self.assertEqual(status, 503)
        _, body = _call(self.base, '/local/v1/apps/dudu/passive/events?cursor=0')
        self.assertTrue(any(p['type'] == 'notification' for p in body['data']['packets']),
                        body['data']['packets'])

    def test_drain_events_cursor_stable_no_loss_no_dup(self):
        _call(self.base, '/local/v1/apps/wangyiyun/bootstrap')
        # 三个弹窗 seed → 3 条 app_event packet
        for i, kind in enumerate(('permission', 'promotion', 'membership')):
            fixture = {'episode_scope': 'eps-c', 'common_records': [
                {'app_id': 'wangyiyun', 'entity_type': 'popup',
                 'semantic_ref': f'eps-c-pop-{i}',
                 'data': {'kind': kind, 'title': f'弹窗{i}', 'message': f'消息{i}'}}],
                'world_deltas': []}
            _call(self.base, '/local/v1/apps/wangyiyun/seed', 'POST', fixture,
                  {'X-Runner-Key': self.key})
        # 第一页只取 2 条
        _, body = _call(self.base, '/local/v1/apps/wangyiyun/passive/events?cursor=0&limit=2')
        page1 = body['data']['packets']
        self.assertEqual(len(page1), 2)
        cursor1 = body['data']['next_cursor']
        # 第二页取剩余 1 条
        _, body = _call(self.base, f'/local/v1/apps/wangyiyun/passive/events?cursor={cursor1}')
        page2 = body['data']['packets']
        self.assertEqual(len(page2), 1)
        # 无重复、无丢失、sequence 严格递增
        seqs = [p['sequence'] for p in page1 + page2]
        self.assertEqual(len(seqs), len(set(seqs)), 'packet 重复')
        self.assertEqual(seqs, sorted(seqs))
        pids = [p['packet_id'] for p in page1 + page2]
        self.assertEqual(len(pids), 3, 'packet 丢失')
        # 再续拉：空且 cursor 不变
        _, body = _call(self.base, f'/local/v1/apps/wangyiyun/passive/events?cursor={body["data"]["next_cursor"]}')
        self.assertEqual(body['data']['packets'], [])
        self.assertEqual(body['data']['next_cursor'], cursor1 + 1, 'cursor 必须稳定')

    def test_mutations_require_runner_key_passive_events_do_not(self):
        _call(self.base, '/local/v1/apps/dudu/bootstrap')
        status, body = _call(self.base, '/local/v1/apps/dudu/mutations?since_cursor=0')
        self.assertEqual(status, 403)
        self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')
        status, _ = _call(self.base, '/local/v1/apps/dudu/passive/events?cursor=0')
        self.assertEqual(status, 200, 'passive/events 是受控只读，Agent 可读')

    def test_gui_packet_with_real_screenshot_ref(self):
        # gui 类型 packet 落地链路：Store._emit_packet(type='gui', screenshot_ref=...) → drain_events 读回。
        # 真实截图引用需 adb 设备（P4 实机验证），此处验证 store 层落地。
        import sys as _sys
        _sys.path.insert(0, str(ROOT / 'local-controller'))
        from store import Store
        tmp = Path(self.tmp.name) / 'unit.sqlite'
        store = Store(str(tmp), 'unit-1', 42, '2026-09-22T10:00:00+08:00', Path(self.tmp.name) / 'events.jsonl')
        seq = store._emit_packet('dudu', 'gui', 'screenshot',
                                 {'url': '/local/v1/screenshots/123.png'},
                                 text_projection='当前屏幕截图',
                                 screenshot_ref='/local/v1/screenshots/123.png')
        result = store.drain_events('dudu', 0)
        self.assertEqual([p['sequence'] for p in result['packets']], [seq])
        self.assertEqual(result['packets'][0]['screenshot_ref'], '/local/v1/screenshots/123.png')
        store.db.close()


if __name__ == '__main__':
    unittest.main()
