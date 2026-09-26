import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackendRegressionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'local-controller/server.py'), '--instance-id',
             'regression', '--runtime', self.tmp.name, '--port', '0'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        port = json.loads(self.proc.stdout.readline())['port']
        self.base = f'http://127.0.0.1:{port}'
        self.key = json.loads((self.runtime / 'instance.json').read_text())['runner_key']

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.tmp.cleanup()

    def call(self, path, body=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['X-Runner-Key'] = key
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def seed(self, app, fixture, key):
        return self.call(f'/local/v1/apps/{app}/seed',
                         {'fixture': fixture, 'idempotency_key': key}, self.key)

    def test_export_requires_runner_key(self):
        self.assertEqual(self.call('/local/v1/instance/export', {})[0], 403)
        self.assertEqual(self.call('/local/v1/instance/export', {}, self.key)[0], 200)

    def test_query_covers_visible_baseline_records(self):
        _, gui = self.call('/runtime/v1/apps/starrail/data/stations?q=' + quote('上海虹桥'))
        self.assertTrue(gui['items'])
        _, result = self.call('/local/v1/apps/starrail/query',
                              {'operation': 'search_stations',
                               'selector': {'keyword': '上海虹桥'}})
        self.assertTrue(any(row['title'] == gui['items'][0]['title'] for row in result['data']))
        self.assertTrue(result['coverage']['complete'])

    def test_adapter_source_version_and_pagination(self):
        _, bootstrap = self.call('/runtime/v1/apps/starrail/bootstrap')
        self.assertEqual(bootstrap['adapter_contract_version'], '1.0')
        _, result = self.call('/local/v1/apps/starrail/query',
                              {'operation': 'search_stations', 'selector': {'limit': 1}})
        self.assertEqual(result['source'], 'starrail')
        status, denied = self.call('/local/v1/apps/starrail/read_state', {})
        self.assertEqual((status, denied['source']), (403, 'starrail'))
        for limit in (0, -1, 'bad'):
            status, error = self.call('/local/v1/apps/starrail/query',
                                      {'operation': 'search_stations', 'selector': {'limit': limit}})
            self.assertEqual((status, error['error']['code']), (400, 'INVALID_PAGINATION'))
        status, error = self.call('/local/v1/apps/starrail/query',
                                  {'operation': 'search_stations', 'projection': []})
        self.assertEqual((status, error['error']['code']), (400, 'INVALID_PROJECTION'))
        for path in ('/local/v1/apps/starrail/passive/events?limit=0',
                     '/local/v1/apps/starrail/passive/events?cursor=-1'):
            self.assertEqual(self.call(path)[0], 400)

    def test_legacy_management_writes_require_runner_key(self):
        for path, body in (
            ('/local/v1/apps/dudu/data/places', {'items': [{'id': 'private-x', 'title': '私有地点'}]}),
            ('/local/v1/instance/lifecycle', {'state': 'FAILED'}),
            ('/local/v1/instance/reset', {}),
        ):
            self.assertEqual(self.call(path, body)[0], 403, path)
        _, gui = self.call('/runtime/v1/apps/dudu/data/places')
        self.assertFalse(any(row['id'] == 'private-x' for row in gui['items']))

    def test_teardown_restores_overwritten_background_record(self):
        path = '/local/v1/apps/dudu/data/places'
        background = {'id': 'background-x', 'title': '背景地点', 'subtitle': '原值', 'price': 7}
        self.assertEqual(self.call(path, {'items': [background]}, self.key)[0], 200)
        fixture = {'episode_scope': 'background', 'common_records': [
            {'app_id': 'dudu', 'entity_type': 'places', 'semantic_ref': 'background-x',
             'data': {'title': '场景地点', 'subtitle': '改值', 'price': 8}}], 'world_deltas': []}
        self.assertEqual(self.seed('dudu', fixture, 'background-key')[0], 200)
        self.assertEqual(self.call('/local/v1/apps/dudu/teardown',
                                   {'episode_scope': 'background'}, self.key)[0], 200)
        _, gui = self.call('/runtime/v1/apps/dudu/data/places')
        restored = next(row for row in gui['items'] if row['id'] == 'background-x')
        self.assertEqual((restored['title'], restored['subtitle'], restored['price']),
                         ('背景地点', '原值', 7))

    def test_freeze_blocks_telemetry_event(self):
        _, before = self.call('/local/v1/apps/dudu/events')
        self.assertEqual(self.call('/local/v1/instance/freeze', {}, self.key)[0], 200)
        status, _ = self.call('/runtime/v1/apps/dudu/telemetry',
                              {'type': 'page_view', 'payload': {'page': 'home'}})
        self.assertEqual(status, 409)
        _, after = self.call('/local/v1/apps/dudu/events')
        self.assertEqual(len(before['events']), len(after['events']))

    def test_world_delta_updates_gui_and_query(self):
        fixture = {'episode_scope': 'delta', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'stations', 'semantic_ref': 'station-x',
             'data': {'title': '旧站', 'price': 1, 'available': 2}}],
            'world_deltas': [{'op': 'update', 'semantic_ref': 'station-x',
                              'set': {'data.title': '新站'},
                              'allowed_changed_fields': ['station-x.title']}]}
        self.assertEqual(self.seed('starrail', fixture, 'delta-key')[0], 200)
        _, queried = self.call('/local/v1/apps/starrail/query',
                               {'operation': 'get_station_details', 'selector': {'semantic_ref': 'station-x'}})
        _, gui = self.call('/runtime/v1/apps/starrail/data/stations')
        self.assertEqual(queried['data']['title'], gui['items'][0]['title'])
        self.assertEqual(gui['items'][0]['title'], '新站')
        deleted = {'episode_scope': 'delta', 'common_records': [],
                   'world_deltas': [{'op': 'delete', 'semantic_ref': 'station-x', 'set': {},
                                     'allowed_changed_fields': ['station-x.title', 'station-x.price',
                                                                'station-x.available']}]}
        self.assertEqual(self.seed('starrail', deleted, 'delete-key')[0], 200)
        _, gui = self.call('/runtime/v1/apps/starrail/data/stations')
        self.assertFalse(any(item['id'] == 'station-x' for item in gui['items']))

    def test_add_delete_patch_require_exact_field_declaration(self):
        add = {'episode_scope': 'patch-fields', 'common_records': [], 'world_deltas': [
            {'op': 'add', 'entity_type': 'stations', 'semantic_ref': 'station-new',
             'set': {'title': '新站'}, 'allowed_changed_fields': []}]}
        status, body = self.seed('starrail', add, 'patch-add-bad')
        self.assertEqual((status, body['error']['code']), (400, 'PATCH_FIELDS_MISMATCH'))
        add['world_deltas'][0]['allowed_changed_fields'] = ['station-new.title']
        self.assertEqual(self.seed('starrail', add, 'patch-add-good')[0], 200)
        delete = {'episode_scope': 'patch-fields', 'common_records': [], 'world_deltas': [
            {'op': 'delete', 'semantic_ref': 'station-new', 'set': {},
             'allowed_changed_fields': []}]}
        status, body = self.seed('starrail', delete, 'patch-delete-bad')
        self.assertEqual((status, body['error']['code']), (400, 'PATCH_FIELDS_MISMATCH'))
        delete['world_deltas'][0]['allowed_changed_fields'] = ['station-new.title']
        self.assertEqual(self.seed('starrail', delete, 'patch-delete-good')[0], 200)

    def test_seed_two_keys_does_not_duplicate_and_hash_is_readback(self):
        fixture = {'episode_scope': 'idem', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'order', 'semantic_ref': 'order-x',
             'data': {'item_id': 'seat-x', 'price': 10}}], 'world_deltas': []}
        _, first = self.seed('starrail', fixture, 'key-1')
        self.assertEqual(self.seed('starrail', fixture, 'key-2')[0], 200)
        _, state = self.call('/local/v1/apps/starrail/read_state', {}, self.key)
        self.assertEqual(len(state['data']['state']['orders']), 1)
        readback = state['data']
        expected = hashlib.sha256(json.dumps(readback, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(first['data']['readback_hash'], expected)

    def test_order_query_reads_all_declared_snapshot_fields(self):
        snapshot = {'item_id': 'G1', 'price': 30, 'fare_difference': 5,
                    'reservation_time': '2026-09-24T10:00:00+08:00',
                    'train_schedule': '10:00 → 12:00'}
        fixture = {'episode_scope': 'order-fields', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'order', 'semantic_ref': 'order-fields-1',
             'data': snapshot}], 'world_deltas': []}
        self.assertEqual(self.seed('starrail', fixture, 'order-fields-key')[0], 200)
        _, result = self.call('/local/v1/apps/starrail/query', {
            'operation': 'get_order_details', 'selector': {'semantic_ref': 'order-fields-1'},
            'projection': ['fare_difference', 'reservation_time', 'train_schedule']})
        self.assertEqual(result['data'], {key: snapshot[key] for key in result['data']})

    def test_string_record_version_matches_envelope_schema(self):
        fixture = {'episode_scope': 'version', 'common_records': [
            {'app_id': 'starrail', 'entity_type': 'stations', 'semantic_ref': 'station-version',
             'record_version': 'starrail.v7', 'data': {'title': '版本车站'}}],
            'world_deltas': []}
        self.assertEqual(self.seed('starrail', fixture, 'version-key')[0], 200)
        _, result = self.call('/local/v1/apps/starrail/query', {
            'operation': 'get_station_details', 'selector': {'semantic_ref': 'station-version'}})
        self.assertEqual(result['record_version'], 'starrail.v7')

    def test_teardown_rolls_back_gui_action_and_keeps_journal(self):
        fixture = {'episode_scope': 'work', 'common_records': [
            {'app_id': 'chaowei', 'entity_type': 'articles', 'semantic_ref': 'article-x',
             'data': {'title': '文章', 'price': 1, 'available': 2}}], 'world_deltas': []}
        self.seed('chaowei', fixture, 'work-key')
        _, state = self.call('/runtime/v1/apps/chaowei/state')
        _, action = self.call('/runtime/v1/apps/chaowei/actions',
                              {'action_id': 'work-action', 'type': 'favorite',
                               'expected_revision': state['revision'],
                               'payload': {'item_id': 'article-x'}})
        self.assertTrue(action['success'])
        self.assertEqual(self.call('/local/v1/apps/chaowei/teardown',
                                   {'episode_scope': 'work'}, self.key)[0], 200)
        _, state = self.call('/runtime/v1/apps/chaowei/state')
        self.assertEqual(state['state']['favorites'], [])
        _, journal = self.call('/local/v1/apps/chaowei/mutations', key=self.key)
        self.assertTrue(any(m['actor'] == 'agent_gui' and m['episode_scope'] == 'work'
                            for m in journal['data']['mutations']))

    def test_teardown_restores_inventory_and_preserves_other_episode(self):
        def fixture(scope, ref):
            return {'episode_scope': scope, 'common_records': [
                {'app_id': 'lianqi', 'entity_type': 'routes', 'semantic_ref': ref,
                 'data': {'title': ref, 'price': 20, 'available': 2}}], 'world_deltas': []}
        self.seed('lianqi', fixture('work', 'route-work'), 'work-key')
        _, state = self.call('/runtime/v1/apps/lianqi/state')
        self.call('/runtime/v1/apps/lianqi/actions',
                  {'action_id': 'order-action', 'type': 'create_order',
                   'expected_revision': state['revision'],
                   'payload': {'item_id': 'route-work'}})
        self.seed('lianqi', fixture('other', 'route-other'), 'other-key')
        self.assertEqual(self.call('/local/v1/apps/lianqi/teardown',
                                   {'episode_scope': 'work'}, self.key)[0], 200)
        _, state = self.call('/runtime/v1/apps/lianqi/state')
        self.assertEqual(state['state']['orders'], [])
        _, summary = self.call('/local/v1/sqlite/summary')
        self.assertEqual(summary['tables']['inventory'], 0)
        _, other = self.call('/local/v1/apps/lianqi/query',
                             {'operation': 'get_route_details',
                              'selector': {'semantic_ref': 'route-other'}})
        self.assertEqual(other['data']['title'], 'route-other')

    def test_freeze_blocks_clock_fault_and_advance_time(self):
        self.assertEqual(self.call('/local/v1/instance/freeze', {}, self.key)[0], 200)
        self.assertEqual(self.call('/local/v1/clock', {'simulated_time': '2030-01-01T00:00:00+08:00'}, self.key)[0], 409)
        self.assertEqual(self.call('/local/v1/faults', {'app_id': 'dudu', 'kind': 'offline'}, self.key)[0], 409)
        self.assertEqual(self.call('/local/v1/instance/advance_time',
                                   {'logical_time': '2030-01-01T00:00:00+08:00'}, self.key)[0], 409)
        self.assertEqual(self.call('/local/v1/instance/feature_flag',
                                   {'flag': 'user_memory_enabled', 'enabled': True}, self.key)[0], 409)

    def test_restore_baseline_is_repeatable(self):
        fixture = {'episode_scope': 'restore', 'common_records': [
            {'app_id': 'dudu', 'entity_type': 'places', 'semantic_ref': 'place-x',
             'data': {'title': '测试地点'}}], 'world_deltas': []}
        self.seed('dudu', fixture, 'restore-key')
        request = {'episode_scope': 'restore', 'baseline_ref': 'init_state'}
        status, first = self.call('/local/v1/apps/dudu/restore_baseline', request, self.key)
        self.assertEqual(status, 200, first)
        status, second = self.call('/local/v1/apps/dudu/restore_baseline', request, self.key)
        self.assertEqual(status, 200, second)
        self.assertEqual(first['data']['baseline_hash'], second['data']['baseline_hash'])
        _, gui = self.call('/runtime/v1/apps/dudu/data/places?q=' + quote('测试地点'))
        self.assertEqual(gui['items'], [])


if __name__ == '__main__':
    unittest.main()
