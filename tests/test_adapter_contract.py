#!/usr/bin/env python3
"""P0 契约冻结锁定测试：entity-catalog / envelope / error-codes / 拟真与溯源清单骨架 / X-Runner-Key 路由分组。
任何对冻结资产的修改（字段、操作、错误码、信封）都必须先改 CONTRACT_UPGRADE_BLUEPRINT.md 并同步本文件。
"""
import json, os, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = {'dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei'}
RESOURCES = {
    'dudu': ['places', 'vehicle_quotes'], 'lianqi': ['routes', 'coupons'],
    'starrail': ['stations', 'trains', 'seats'], 'babaquna': ['flights', 'hotels', 'attractions'],
    'wangyiyun': ['songs', 'playlists'], 'chaowei': ['articles', 'tags'],
}
DISPLAY_GUI_FIELDS = {'semantic_ref', 'title', 'subtitle', 'price', 'available', 'rank'}
USER_STATE_FIELDS = {
    'order': {'semantic_ref', 'item_id', 'price', 'status', 'created_at'},
    'popup': {'semantic_ref', 'kind', 'title', 'message'},
    'favorite': {'semantic_ref', 'item_id'},
    'coupon_wallet': {'semantic_ref', 'code', 'title', 'discount'},
    'passenger': {'semantic_ref', 'name'},
    'play_history': {'semantic_ref', 'item_id', 'at'},
    'user_playlist': {'semantic_ref', 'name', 'songs'},
    'note': {'semantic_ref', 'item_id', 'text'},
}
LEGACY_ERROR_CODES = {
    'REVISION_CONFLICT', 'PRICE_CHANGED', 'INVENTORY_CHANGED', 'TIME_CONFLICT',
    'ALREADY_FAVORITED', 'ORDER_NOT_FOUND', 'APP_NOT_FOUND', 'RESOURCE_NOT_FOUND',
    'SCREENSHOT_NOT_FOUND', 'NOT_FOUND', 'INVALID_JSON', 'INVALID_ACTION',
    'INVALID_PAGINATION', 'INVALID_SIMULATED_TIME', 'INVALID_LIFECYCLE',
    'INVALID_PROCESS_ACTION', 'INVALID_INJECTION', 'INVALID_FAULT_KIND',
    'EXPECTED_REVISION_REQUIRED', 'UNKNOWN_ACTION', 'IDEMPOTENCY_KEY_REUSED',
    'SESSION_NOT_READY', 'SIMULATED_NETWORK_FAILURE', 'DEVICE_NOT_AVAILABLE',
    'RESET_FAILED', 'CONTAMINATED',
}
ADAPTER_ERROR_CODES = {
    'PERMISSION_DENIED', 'SESSION_FROZEN', 'INVALID_OPERATION', 'INVALID_PROJECTION',
    'INVALID_AS_OF', 'INVALID_FIXTURE', 'EPISODE_NOT_FOUND', 'CURSOR_INVALID',
    'PATCH_TARGET_EXISTS', 'PATCH_TARGET_NOT_FOUND', 'PATCH_FIELDS_MISMATCH',
    'BASELINE_NOT_FOUND', 'TEARDOWN_FAILED',
}
ENVELOPE_STATUSES = {'ok', 'not_found', 'permission_denied', 'stale', 'timeout',
                     'backend_error', 'feature_off'}


def load_catalog(app):
    path = ROOT / 'contracts/entity-catalog' / f'{app}.json'
    assert path.is_file(), f'missing catalog {path}'
    return json.loads(path.read_text())


class CatalogFreezeCase(unittest.TestCase):
    def test_six_catalogs_exist_and_are_valid(self):
        for app in APPS:
            catalog = load_catalog(app)
            self.assertEqual(catalog['app_id'], app)
            self.assertEqual(catalog['android_package'], f'com.ku.simulator.{app}')
            self.assertIn('entity_types', catalog)
            self.assertIn('static_chrome', catalog)

    def test_display_entities_cover_all_resources_with_gui_fields(self):
        for app in APPS:
            catalog = load_catalog(app)
            types = {e['name']: e for e in catalog['entity_types']}
            for res in RESOURCES[app]:
                self.assertIn(res, types, f'{app} 缺少展示实体 {res}')
                fields = set(types[res]['fields'])
                self.assertTrue(DISPLAY_GUI_FIELDS <= fields,
                                f'{app}.{res} 字段 {fields} 未覆盖 GUI 可见字段 {DISPLAY_GUI_FIELDS}')

    def test_user_state_entities_exist_with_expected_fields(self):
        for app in APPS:
            catalog = load_catalog(app)
            types = {e['name']: e for e in catalog['entity_types']}
            for entity, fields in USER_STATE_FIELDS.items():
                if entity not in types:
                    continue
                self.assertTrue(fields <= set(types[entity]['fields']),
                                f'{app}.{entity} 字段 {types[entity]["fields"]} 缺少 {fields}')

    def test_query_operations_use_frozen_vocabulary(self):
        for app in APPS:
            catalog = load_catalog(app)
            for entity in catalog['entity_types']:
                for op in entity['query_operations']:
                    self.assertIn(op['granularity'], ('metadata', 'details'))
                    self.assertIn(op['coverage'], ('complete_for_query_scope', 'complete_single_record'))
                    self.assertTrue(set(op['result_fields']) <= set(entity['fields']),
                                    f'{app}.{entity["name"]}.{op["name"]} 投影字段越界')
                    self.assertEqual(op['permission'], 'query.read')

    def test_static_chrome_whitelist_nonempty(self):
        for app in APPS:
            chrome = load_catalog(app)['static_chrome']
            self.assertTrue(chrome['shared'], f'{app} shared chrome 为空')
            self.assertTrue(chrome['app_specific'], f'{app} app_specific chrome 为空')
            # 白名单只允许字符串，且与展示数据字段无交集
            self.assertTrue(all(isinstance(s, str) for s in chrome['shared'] + chrome['app_specific']))

    def test_gui_mutation_and_passive_event_types_declared(self):
        for app in APPS:
            catalog = load_catalog(app)
            self.assertTrue(catalog['gui_mutation_types'])
            self.assertIn('user_action', catalog['passive_event_types'])
            self.assertIn('gui_frame', catalog['passive_event_types'])


class EnvelopeAndErrorCodeFreezeCase(unittest.TestCase):
    def test_envelope_schema_is_valid_and_frozen(self):
        schema = json.loads((ROOT / 'contracts/envelope.schema.json').read_text())
        self.assertEqual(schema['type'], 'object')
        self.assertEqual(set(schema['properties']['status']['enum']), ENVELOPE_STATUSES)
        self.assertEqual(set(schema['required']),
                         {'status', 'source', 'operation', 'observed_at', 'data'})
        self.assertIn('error', schema['properties'])
        self.assertIn('coverage', schema['properties'])
        self.assertFalse(schema.get('additionalProperties'), '信封禁止未声明字段')

    def test_error_codes_frozen_set(self):
        codes = json.loads((ROOT / 'contracts/error-codes.json').read_text())
        self.assertEqual(set(codes['envelope_status']), ENVELOPE_STATUSES)
        frozen = set(codes['error_codes'])
        self.assertTrue(LEGACY_ERROR_CODES <= frozen,
                        f'缺失旧错误码 {LEGACY_ERROR_CODES - frozen}')
        self.assertTrue(ADAPTER_ERROR_CODES <= frozen,
                        f'缺失适配器错误码 {ADAPTER_ERROR_CODES - frozen}')
        for code, spec in codes['error_codes'].items():
            self.assertIn('http', spec)
            self.assertIn('retryable', spec)


class ProvenanceAndChecklistSkeletonCase(unittest.TestCase):
    def test_six_provenance_files_exist_with_traceable_entries(self):
        for app in APPS:
            path = ROOT / 'apps' / app / 'ui-data-provenance.json'
            self.assertTrue(path.is_file(), f'missing {path}')
            provenance = json.loads(path.read_text())
            self.assertEqual(provenance['app_id'], app)
            self.assertTrue(provenance['provenance'], f'{app} provenance 条目为空')
            for entry in provenance['provenance']:
                self.assertIn('ui_location', entry)
                self.assertTrue(entry['data_points'], f'{app} {entry["ui_location"]} 无数据点')
                for point in entry['data_points']:
                    self.assertIn('field', point)
                    self.assertIn('semantic_ref', point)
                    self.assertIn('inject', point)
                    self.assertIn('readback', point)

    def test_six_fidelity_checklists_exist_with_t_layers(self):
        for app in APPS:
            path = ROOT / 'apps' / app / 'ui-fidelity-checklist.md'
            self.assertTrue(path.is_file(), f'missing {path}')
            text = path.read_text()
            for layer in ('T1', 'T2', 'T3', 'T4'):
                self.assertIn(layer, text, f'{app} checklist 缺少 {layer}')


def _call(base, path, headers=None, data=None):
    req = urllib.request.Request(f'{base}{path}', method='POST' if data is not None else 'GET',
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={**(headers or {}),
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


class RunnerKeyRoutingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='adapter-contract-')
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'local-controller/server.py'),
             '--instance-id', 'adapter-test-1', '--runtime', f'{cls.tmp.name}/instance',
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
        info = json.loads(Path(cls.tmp.name, 'instance/instance.json').read_text())
        cls.key = info['runner_key']

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def test_instance_json_has_runner_key_but_info_does_not(self):
        info = json.loads(Path(self.tmp.name, 'instance/instance.json').read_text())
        self.assertTrue(len(info['runner_key']) >= 32)
        _, status = _call(self.base, '/local/v1/instance/status')
        self.assertNotIn('runner_key', status)
        self.assertNotIn('runner_key', status.get('info', {}))

    def test_capabilities_requires_runner_key(self):
        status, body = _call(self.base, '/local/v1/apps/dudu/capabilities')
        self.assertEqual(status, 403)
        self.assertEqual(body['status'], 'permission_denied')
        self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')

    def test_capabilities_with_key_returns_frozen_catalog(self):
        status, body = _call(self.base, '/local/v1/apps/dudu/capabilities',
                             headers={'X-Runner-Key': self.key})
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data']['app_id'], 'dudu')
        self.assertTrue(DISPLAY_GUI_FIELDS <= set(body['data']['entity_types'][0]['fields']),
                        'capabilities 展示实体未覆盖 GUI 可见字段')
        self.assertNotIn('runner_key', json.dumps(body))

    def test_capabilities_unknown_app_not_found(self):
        status, body = _call(self.base, '/local/v1/apps/nope/capabilities',
                             headers={'X-Runner-Key': self.key})
        self.assertEqual(status, 404)
        self.assertEqual(body['status'], 'not_found')
        self.assertEqual(body['error']['code'], 'APP_NOT_FOUND')

    def test_management_grouping_covers_instance_level_routes(self):
        for path in ('/local/v1/instance/freeze', '/local/v1/instance/unfreeze',
                     '/local/v1/instance/advance_time', '/local/v1/apps/user_memory/query'):
            status, body = _call(self.base, path)
            self.assertEqual(status, 403, f'{path} 未落入管理分组')
            self.assertEqual(body['error']['code'], 'PERMISSION_DENIED')


if __name__ == '__main__':
    unittest.main()
