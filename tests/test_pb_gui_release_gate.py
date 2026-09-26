import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gui_machine_qa as machine_qa
import gui_publish
import pb_backend_plan as pb
import pb_gui_capture_gate as capture_gate
import pb_gui_release_gate as release_gate

CLUSTER = 'demo-001'
EPISODE = 'ep_0001'
PASSIVE = 'p001'
WORLD = 'w001'
OBSERVED = '2026-09-14T08:30:26+08:00'
DECISION = '2026-09-14T08:35:01+08:00'
APPLIED_AT = '2026-09-14T08:40:00+08:00'
WORLD_HASH = 'c' * 64
BACKEND_PLAN_SHA = 'd' * 64
CAPTURE_RUN = 'run-1'
PRIVATE_TEXT = 'PRIVATE-EXPECTED-VISIBLE'
VIEWS = {'calendar': 'event_editor', 'mail': 'mail_detail'}


def shot_sha(packet_id):
    return hashlib.sha256(packet_id.encode('utf8')).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return path


def capture_block(source, frame_ids):
    return {'view_id': VIEWS[source], 'subject_ref': f'{source}-ref',
            'visible_assertions': ['subject'],
            'expected_visible': {'subject': PRIVATE_TEXT},
            'frames': [{'frame_id': frame_id} for frame_id in frame_ids]}


def gui_packet(packet_id, sequence, source, declared=True, frame_ids=('f1',)):
    packet = {'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
              'observed_at': OBSERVED, 'delivered_at': OBSERVED, 'text_event': f'{source} view',
              'capture': capture_block(source, frame_ids)}
    if declared:
        packet['screenshot_ref'] = f'passive_inputs/{PASSIVE}/gui/{packet_id}.png'
        packet['screenshot_sha256'] = shot_sha(packet_id)
    return packet


def text_packet(packet_id, sequence, source):
    return {'packet_id': packet_id, 'sequence': sequence, 'tag': 'text', 'source': source,
            'observed_at': OBSERVED, 'delivered_at': OBSERVED, 'text_event': 'text packet',
            'capture': {'frames': []}}


def build_plan(root, name='gui_plan.json'):
    packets = [gui_packet('p003', 5, 'calendar', declared=True, frame_ids=('f1',)),
               text_packet('p004', 6, 'messages'),
               gui_packet('p009', 12, 'mail', declared=True, frame_ids=('f1', 'f2')),
               gui_packet('p013', 15, 'mail', declared=False, frame_ids=('f1',)),
               gui_packet('p016', 17, 'mail', declared=True, frame_ids=('f1',))]
    plan = {'schema_version': 'pb-gui-bridge.plan.v1', 'generated_at': APPLIED_AT,
            'pb_root': str(root), 'cluster_id': CLUSTER, 'episode_id': EPISODE,
            'passive_id': PASSIVE, 'world_variant_id': WORLD, 'track': 'mixed_text',
            'decision_point': DECISION, 'screenshot_dir': f'passive_inputs/{PASSIVE}/gui',
            'source_assets': {'benchmark': {'path': 'benchmark.json', 'sha256': 'a' * 64}},
            'counts': {'packets': len(packets)}, 'packets': packets}
    return write_json(root / name, plan)


def build_capture_plan(root, gui_plan_path, gate_path, receipt_path, name='capture-plan.json'):
    """Compiled ``pb-gui-private-capture-plan.v1`` as pb_gui_capture_compile emits it."""
    gui_plan = json.loads(gui_plan_path.read_text(encoding='utf8'))
    by_id = {packet['packet_id']: packet for packet in gui_plan['packets']}
    executable, unsupported = [], []
    for packet_id in ('p003', 'p009', 'p013'):
        packet = by_id[packet_id]
        executable.append({field: packet[field] for field in
                           ('packet_id', 'sequence', 'tag', 'source', 'observed_at',
                            'delivered_at', 'text_event')} | {'capture': packet['capture']})
    packet = by_id['p016']
    unsupported.append({'packet_id': 'p016', 'sequence': packet['sequence'],
                        'source': packet['source'], 'surface': 'mail_inbox',
                        'reason': 'no faithful navigation path'})
    plan = {
        'schema_version': 'pb-gui-private-capture-plan.v1', 'generated_at': APPLIED_AT,
        'command': 'compile', 'status': 'compiled',
        'episode_scope': {'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
                          'world_variant_id': WORLD, 'track': 'mixed_text',
                          'decision_point': DECISION, 'timezone': 'Asia/Shanghai'},
        'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
        'world_variant_id': WORLD, 'baseline_ref': 'init_state',
        'device_timezone': 'Asia/Shanghai', 'eof_at': DECISION,
        'source_assets': {
            'gui_plan': {'schema_version': 'pb-gui-bridge.plan.v1',
                         'sha256': hashlib.sha256(gui_plan_path.read_bytes()).hexdigest()},
            'backend_plan': {'schema_version': 'pb-backend-plan.v1', 'sha256': BACKEND_PLAN_SHA},
            'backend_receipt': {'schema_version': 'pb-backend-apply-receipt.v1',
                                'sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()},
            'gate_plan': {'schema_version': 'pb-gui-capture-gate.v1',
                          'sha256': hashlib.sha256(gate_path.read_bytes()).hexdigest()},
        },
        'fixture': {'episode_scope': EPISODE, 'common_records': [], 'world_deltas': [],
                    'ku_records': []},
        'packets': executable, 'unsupported_packets': unsupported,
        'counts': {'packets': len(executable), 'unsupported_packets': len(unsupported),
                   'fixture_records': 0, 'frames': 4, 'history_replays': 0},
        'checks': {'images_read': 0, 'images_copied': 0, 'device_writes': 0},
    }
    return write_json(root / name, plan)


def receipt_record(order, source, semantic_ref):
    contract = capture_gate.adapter_contract(source)
    return {'order': order, 'source': source, 'semantic_ref': semantic_ref,
            'adapter': {'module': contract['module'], 'seed': contract['seed'],
                        'read': contract['read']},
            'seed': {'at': OBSERVED, 'function': contract['seed'], 'result': {'seeded': True}},
            'readback': {'at': OBSERVED, 'function': contract['read'], 'result': {'verified': True}},
            'status': 'ok'}


def build_receipt(root, scope_overrides=None, records=None, name='backend-receipt.json',
                  **overrides):
    scope = {'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
             'world_variant_id': WORLD, 'track': 'mixed_text', 'decision_point': DECISION,
             'timezone': 'Asia/Shanghai', 'world_status': 'new_bundle_not_applied_to_android',
             'world_hash': WORLD_HASH, 'device_applied': False}
    if scope_overrides:
        scope.update(scope_overrides)
    if records is None:
        records = [receipt_record(1, 'calendar', 'calendar.event.cal_001'),
                   receipt_record(2, 'mail', 'mail.message.mail_001')]
    receipt = {'schema_version': 'pb-backend-apply-receipt.v1', 'status': 'ok', 'command': 'apply',
               'device_applied': True, 'allow_device_apply': True, 'applied_at': APPLIED_AT,
               'plan_binding': {'schema_version': 'pb-backend-plan.v1',
                                'plan_sha256': BACKEND_PLAN_SHA, 'status': 'planned',
                                'device_applied': False, 'episode_scope': scope,
                                'world_status': scope['world_status'],
                                'world_hash': scope['world_hash']},
               'device': {'serial': 'emulator-5554', 'timezone': 'Asia/Shanghai', 'adb': 'adb'},
               'ordering': ['calendar', 'mail'], 'records': records,
               'checks': {'seed_records': len(records), 'seeds': len(records),
                          'readbacks': len(records), 'absence_guards': 2, 'failures': 0}}
    receipt.update(overrides)
    return write_json(root / name, receipt)


def build_capture(root, plan_path, name='capture'):
    capture = root / name
    capture.mkdir(parents=True, exist_ok=True)
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    (capture / 'private-plan.json').write_bytes(plan_bytes)
    frames = []
    for packet in plan['packets']:
        for item in packet['capture']['frames']:
            raw = f"{packet['packet_id']}-{item['frame_id']}-device-png".encode()
            png = f"images/{packet['packet_id']}-{item['frame_id']}.png"
            path = capture / png
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
            frames.append({'packet_id': packet['packet_id'], 'frame_id': item['frame_id'],
                           'png': png, 'sha256': hashlib.sha256(raw).hexdigest(),
                           'width': 1080, 'height': 2400, 'package': 'com.example.app',
                           'activity': 'com.example.app/.MainActivity', 'android_time': OBSERVED,
                           'clock_synced_at': OBSERVED,
                           'device_env': {'resolution': 'Physical size: 1080x2400',
                                          'density': 'Physical density: 420',
                                          'font_scale': 'default', 'locale': 'en-US',
                                          'theme': 'Night mode: no',
                                          'timezone': 'Asia/Shanghai'}})
    receipt = {'schema_version': '1.0', 'episode_id': plan['episode_id'],
               'capture_run_id': CAPTURE_RUN,
               'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
               'source_assets': plan['source_assets'], 'status': 'capture_complete',
               'reset_verified': True, 'frames': frames,
               'device': {'baseline_version': 'sha256:' + 'b' * 64, 'dataset_version': 'v1',
                          'resolution': 'Physical size: 1080x2400',
                          'density': 'Physical density: 420', 'font_scale': 'default',
                          'locale': 'en-US', 'theme': 'Night mode: no',
                          'timezone': 'Asia/Shanghai'}}
    write_json(capture / 'receipt.json', receipt)
    return capture, receipt


def audit_report(plan_sha, frames):
    report = {'schema_version': '1.0', 'rule_version': machine_qa.RULE,
              'ocr_model_sha256': machine_qa.MODEL_SHA256,
              'ocr_models': dict(machine_qa.OCR_MODELS), 'plan_sha256': plan_sha,
              'capture_run_id': CAPTURE_RUN, 'device_env': {'status': 'pass'}, 'status': 'pass',
              'frames': [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                          'png': frame['png'], 'sha256': frame['sha256'], 'status': 'pass',
                          'fields': [{'field': 'subject', 'expected': PRIVATE_TEXT,
                                      'passed': True}]} for frame in frames]}
    return report


def tree_hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*')) if path.is_file()}


class ReleaseGateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.gui_plan_path = build_plan(self.root)
        self.receipt_path = build_receipt(self.root)
        self.gate_path = self.root / 'gate.json'
        write_json(self.gate_path,
                   capture_gate.build_gate_plan(self.gui_plan_path, self.receipt_path))
        self.capture_plan_path = build_capture_plan(self.root, self.gui_plan_path, self.gate_path,
                                                    self.receipt_path)
        self.capture, self.capture_receipt = build_capture(self.root, self.capture_plan_path)
        self.report_path = self.root / 'release-report.json'
        self.plan_sha = hashlib.sha256((self.capture / 'private-plan.json').read_bytes()).hexdigest()

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = release_gate.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def preflight(self, capture=None, gate=None, receipt=None, output=None, audit=None):
        argv = ['preflight', '--capture-dir', str(capture or self.capture),
                '--gate-plan', str(gate or self.gate_path),
                '--backend-receipt', str(receipt or self.receipt_path),
                '--output', str(output or self.report_path)]
        if audit is None:
            return self.run_cli(argv)
        with unittest.mock.patch.object(machine_qa, 'audit_capture', audit):
            return self.run_cli(argv)

    def validate(self, *extra, audit=None):
        argv = ['validate', '--report', str(self.report_path), *extra]
        if audit is None:
            return self.run_cli(argv)
        with unittest.mock.patch.object(machine_qa, 'audit_capture', audit):
            return self.run_cli(argv)

    def audit_ok(self, frames=None, **overrides):
        report = audit_report(self.plan_sha, frames if frames is not None
                              else self.capture_receipt['frames'])
        report.update(overrides)
        return report

    def rewrite_capture_receipt(self, **overrides):
        receipt = copy.deepcopy(self.capture_receipt)
        receipt.update(overrides)
        write_json(self.capture / 'receipt.json', receipt)
        return receipt

    def rewrite_capture_plan(self, mutate):
        plan_path = self.capture / 'private-plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        mutate(plan)
        write_json(plan_path, plan)
        plan_bytes = plan_path.read_bytes()
        self.plan_sha = hashlib.sha256(plan_bytes).hexdigest()
        receipt = copy.deepcopy(self.capture_receipt)
        receipt['plan_sha256'] = self.plan_sha
        receipt['source_assets'] = plan['source_assets']
        write_json(self.capture / 'receipt.json', receipt)
        return plan

    def read_report(self):
        return json.loads(self.report_path.read_text(encoding='utf8'))


class ReleaseGatePreflightTest(ReleaseGateTestBase):
    def test_preflight_success_writes_private_release_ready_report(self):
        audit = self.audit_ok()
        write_json(self.capture / 'qa-machine.json', audit)
        before = tree_hashes(self.capture)
        with unittest.mock.patch.object(gui_publish, 'publish',
                                        side_effect=AssertionError('gui_publish must not be called')):
            code, out, stderr = self.preflight(audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 0, stderr)
        document = self.read_report()
        self.assertEqual(document['schema_version'], 'pb-gui-release-report.v1')
        self.assertEqual(document['status'], 'release_ready')
        self.assertTrue(document['release_ready'])
        self.assertEqual(document['episode_scope'], {'cluster_id': CLUSTER, 'episode_id': EPISODE,
                                                     'passive_id': PASSIVE,
                                                     'world_variant_id': WORLD,
                                                     'track': 'mixed_text',
                                                     'decision_point': DECISION,
                                                     'timezone': 'Asia/Shanghai'})
        self.assertEqual(document['counts'], {'targets': 3, 'gate_targets': 4,
                                              'unsupported_targets': 1, 'frames': 4,
                                              'pass_frames': 4,
                                              'declared_screenshot_targets': 2,
                                              'derived_screenshot_targets': 1,
                                              'by_source': {'calendar': 1, 'mail': 2}})
        self.assertEqual([frame['packet_id'] for frame in document['frames']],
                         ['p003', 'p009', 'p009', 'p013'])
        self.assertTrue(all(frame['status'] == 'pass' for frame in document['frames']))
        self.assertEqual(document['frames'][0]['png'], 'images/p003-f1.png')
        self.assertEqual(document['machine_qa']['rule_version'], machine_qa.RULE)
        self.assertEqual(document['machine_qa']['ocr_model_sha256'], machine_qa.MODEL_SHA256)
        self.assertEqual(document['machine_qa']['ocr_models'], machine_qa.OCR_MODELS)
        bindings = document['bindings']
        self.assertEqual(bindings['gate_plan']['sha256'],
                         hashlib.sha256(self.gate_path.read_bytes()).hexdigest())
        self.assertEqual(bindings['backend_receipt']['sha256'],
                         hashlib.sha256(self.receipt_path.read_bytes()).hexdigest())
        self.assertTrue(bindings['backend_receipt']['device_applied'])
        self.assertEqual(bindings['private_plan']['sha256'], self.plan_sha)
        self.assertEqual(bindings['private_plan']['schema_version'],
                         'pb-gui-private-capture-plan.v1')
        self.assertEqual(bindings['capture_receipt']['sha256'],
                         hashlib.sha256((self.capture / 'receipt.json').read_bytes()).hexdigest())
        self.assertTrue(bindings['capture_receipt']['reset_verified'])
        self.assertEqual(bindings['machine_qa']['status'], 'pass')
        self.assertEqual(bindings['machine_qa']['stored_reports'], ['qa-machine.json'])
        self.assertEqual(document['checks']['images_copied'], 0)
        self.assertEqual(document['checks']['publish_invoked'], 0)
        self.assertEqual(document['checks']['gold_generated'], 0)
        text = json.dumps(document, ensure_ascii=False)
        self.assertNotIn('PRIVATE-EXPECTED-VISIBLE', text)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(capture_gate.private_paths(document), [])
        self.assertEqual(pb.absolute_value_paths(document), [])
        summary = json.loads(out)
        self.assertTrue(summary['release_ready'])
        self.assertEqual(summary['targets'], 3)
        self.assertEqual(summary['frames'], 4)
        self.assertEqual(tree_hashes(self.capture), before)
        self.assertFalse((self.root / 'release').exists())

    def test_preflight_rejects_incomplete_capture_receipt(self):
        cases = ({'status': 'capture_failed'}, {'status': 'capture_complete',
                                                'reset_verified': False},
                 {'status': 'capture_complete', 'reset_verified': None})
        for overrides in cases:
            with self.subTest(overrides):
                self.rewrite_capture_receipt(**overrides)
                code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
                self.assertEqual(code, 1)
                self.assertTrue('capture' in stderr or 'reset' in stderr)
                self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_machine_qa_failure(self):
        rejected = self.audit_ok(**{'status': 'reject'})
        rejected['frames'][0]['status'] = 'reject'
        code, _, stderr = self.preflight(audit=lambda capture_dir: rejected)
        self.assertEqual(code, 1)
        self.assertIn('machine QA', stderr)
        self.assertFalse(self.report_path.exists())

        inconsistent = self.audit_ok()
        inconsistent['frames'][0]['status'] = 'reject'
        code, _, stderr = self.preflight(audit=lambda capture_dir: inconsistent)
        self.assertEqual(code, 1)
        self.assertIn('machine QA', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_incomplete_frames(self):
        receipt = copy.deepcopy(self.capture_receipt)
        receipt['frames'] = [frame for frame in receipt['frames']
                             if (frame['packet_id'], frame['frame_id']) != ('p009', 'f2')]
        write_json(self.capture / 'receipt.json', receipt)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok(frames=receipt['frames']))
        self.assertEqual(code, 1)
        self.assertIn('frame', stderr)
        self.assertFalse(self.report_path.exists())

        write_json(self.capture / 'receipt.json', self.capture_receipt)
        short = [frame for frame in self.capture_receipt['frames']
                 if (frame['packet_id'], frame['frame_id']) != ('p003', 'f1')]
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok(frames=short))
        self.assertEqual(code, 1)
        self.assertIn('machine QA', stderr)
        self.assertFalse(self.report_path.exists())

        write_json(self.capture / 'receipt.json', self.capture_receipt)
        self.capture_receipt['frames'][2]['sha256'] = 'e' * 64
        write_json(self.capture / 'receipt.json', self.capture_receipt)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('hash', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_scope_mismatch(self):
        for field, value in {'passive_id': 'p999', 'world_variant_id': 'w999',
                             'world_hash': 'e' * 64, 'episode_id': 'ep_9999'}.items():
            with self.subTest(field):
                receipt = build_receipt(self.root, scope_overrides={field: value},
                                        name=f'receipt-{field}.json')
                code, _, stderr = self.preflight(receipt=receipt,
                                                 audit=lambda capture_dir: self.audit_ok())
                self.assertEqual(code, 1)
                self.assertIn(field, stderr)
                self.assertFalse(self.report_path.exists())
        tampered = json.loads(self.gate_path.read_text(encoding='utf8'))
        tampered['episode_scope']['passive_id'] = 'p999'
        write_json(self.gate_path, tampered)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('passive_id', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_invalid_gate_and_backend_receipt(self):
        gate = json.loads(self.gate_path.read_text(encoding='utf8'))
        gate['status'] = 'captured'
        write_json(self.gate_path, gate)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('status', stderr)
        self.assertFalse(self.report_path.exists())

        gate['status'] = 'ready_for_device_capture'
        gate['targets'][0]['capture_state'] = 'captured'
        write_json(self.gate_path, gate)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('capture_state', stderr)
        self.assertFalse(self.report_path.exists())

        write_json(self.gate_path,
                   capture_gate.build_gate_plan(self.gui_plan_path, self.receipt_path))
        for overrides in ({'schema_version': 'pb-backend-apply-receipt.v0'},
                          {'status': 'failed'}, {'device_applied': False}):
            with self.subTest(overrides):
                receipt = build_receipt(self.root, name='receipt-broken.json', **overrides)
                code, _, stderr = self.preflight(receipt=receipt,
                                                 audit=lambda capture_dir: self.audit_ok())
                self.assertEqual(code, 1)
                self.assertTrue(stderr.strip())
                self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_stale_gate_and_capture_plan(self):
        plan_path = self.capture / 'private-plan.json'
        original = plan_path.read_bytes()
        plan_path.write_bytes(original + b'\n')
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertTrue('private-plan' in stderr or 'sha256' in stderr)
        self.assertFalse(self.report_path.exists())
        plan_path.write_bytes(original)

        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        receipt['applied_at'] = '2026-09-14T09:00:00+08:00'
        write_json(self.receipt_path, receipt)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('sha256', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_missing_capture_files(self):
        receipt_path = self.capture / 'receipt.json'
        raw = receipt_path.read_bytes()
        receipt_path.unlink()
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('receipt.json', stderr)
        self.assertFalse(self.report_path.exists())
        receipt_path.write_bytes(raw)

        plan_path = self.capture / 'private-plan.json'
        raw = plan_path.read_bytes()
        plan_path.unlink()
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('private-plan.json', stderr)
        self.assertFalse(self.report_path.exists())
        plan_path.write_bytes(raw)

    def test_preflight_rejects_stored_machine_qa_mismatch(self):
        audit = self.audit_ok()
        stored = copy.deepcopy(audit)
        stored['frames'][0]['fields'][0]['passed'] = False
        write_json(self.capture / 'machine-qa.json', stored)
        code, _, stderr = self.preflight(audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 1)
        self.assertIn('machine-qa.json', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_republished_original_screenshot(self):
        receipt = copy.deepcopy(self.capture_receipt)
        raw = b'p003'
        self.assertEqual(hashlib.sha256(raw).hexdigest(), shot_sha('p003'))
        (self.capture / 'images' / 'p003-f1.png').write_bytes(raw)
        receipt['frames'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
        write_json(self.capture / 'receipt.json', receipt)
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok(frames=receipt['frames']))
        self.assertEqual(code, 1)
        self.assertIn('pre-existing PB screenshot', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_binds_compiled_capture_plan_source_assets(self):
        code, out, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 0, stderr)
        document = self.read_report()
        self.assertEqual(document['bindings']['private_plan']['schema_version'],
                         'pb-gui-private-capture-plan.v1')
        self.assertEqual(document['bindings']['private_plan']['sha256'], self.plan_sha)
        self.assertNotEqual(self.plan_sha,
                            hashlib.sha256(self.gui_plan_path.read_bytes()).hexdigest())
        self.assertEqual(document['counts']['targets'], 3)
        self.assertEqual(document['counts']['gate_targets'], 4)
        self.assertEqual(document['counts']['unsupported_targets'], 1)
        summary = json.loads(out)
        self.assertEqual(summary['gate_targets'], 4)
        self.assertEqual(summary['unsupported_targets'], 1)

    def test_preflight_rejects_stale_capture_plan_source_assets(self):
        for name in ('gui_plan', 'gate_plan', 'backend_receipt', 'backend_plan'):
            with self.subTest(name):
                self.setUp()
                self.rewrite_capture_plan(lambda plan, name=name:
                                          plan['source_assets'][name].update({'sha256': 'f' * 64}))
                code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
                self.assertEqual(code, 1)
                self.assertIn(name, stderr)
                self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_unaccounted_gate_target(self):
        self.rewrite_capture_plan(
            lambda plan: plan.update({'unsupported_packets': []}))
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('neither captured nor declared unsupported', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_frame_for_unsupported_target(self):
        raw = b'p016-f1-device-png'
        png = 'images/p016-f1.png'
        (self.capture / png).write_bytes(raw)
        receipt = copy.deepcopy(self.capture_receipt)
        receipt['frames'].append({'packet_id': 'p016', 'frame_id': 'f1', 'png': png,
                                  'sha256': hashlib.sha256(raw).hexdigest(), 'width': 1080,
                                  'height': 2400, 'package': 'com.example.app',
                                  'activity': 'com.example.app/.MainActivity',
                                  'android_time': OBSERVED, 'clock_synced_at': OBSERVED,
                                  'device_env': dict(self.capture_receipt['frames'][0]['device_env'])})
        write_json(self.capture / 'receipt.json', receipt)
        code, _, stderr = self.preflight(
            audit=lambda capture_dir: self.audit_ok(frames=receipt['frames']))
        self.assertEqual(code, 1)
        self.assertIn('frame', stderr)
        self.assertFalse(self.report_path.exists())

    def test_preflight_rejects_capture_plan_scope_drift(self):
        self.rewrite_capture_plan(
            lambda plan: plan['episode_scope'].update({'world_variant_id': 'w999'}))
        code, _, stderr = self.preflight(audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('world_variant_id', stderr)
        self.assertFalse(self.report_path.exists())


class ReleaseGateValidateTest(ReleaseGateTestBase):
    def build_report(self):
        audit = self.audit_ok()
        code, _, stderr = self.preflight(audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 0, stderr)
        return audit

    def test_validate_rechecks_structure_and_rebuilds_hashes(self):
        audit = self.build_report()
        code, out, stderr = self.validate()
        self.assertEqual(code, 0, stderr)
        summary = json.loads(out)
        self.assertEqual(summary['status'], 'ok')
        self.assertFalse(summary['hashes_verified'])
        self.assertEqual(summary['targets'], 3)
        self.assertEqual(capture_gate.private_paths(summary), [])

        code, out, stderr = self.validate('--capture-dir', str(self.capture),
                                          '--gate-plan', str(self.gate_path),
                                          '--backend-receipt', str(self.receipt_path),
                                          audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 0, stderr)
        summary = json.loads(out)
        self.assertTrue(summary['hashes_verified'])
        self.assertEqual(summary['private_plan_sha256'], self.plan_sha)

        code, _, stderr = self.validate('--capture-dir', str(self.capture),
                                        '--gate-plan', str(self.gate_path),
                                        audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 1)
        self.assertIn('--backend-receipt', stderr)

    def test_validate_detects_stale_inputs(self):
        audit = self.build_report()
        receipt = copy.deepcopy(self.capture_receipt)
        receipt['status'] = 'capture_failed'
        write_json(self.capture / 'receipt.json', receipt)
        code, _, stderr = self.validate('--capture-dir', str(self.capture),
                                        '--gate-plan', str(self.gate_path),
                                        '--backend-receipt', str(self.receipt_path),
                                        audit=lambda capture_dir: copy.deepcopy(audit))
        self.assertEqual(code, 1)
        self.assertTrue(stderr.strip())

    def test_validate_rejects_private_fields_and_absolute_paths(self):
        self.build_report()
        document = self.read_report()
        document['oracle'] = {'need': 'SECRET'}
        write_json(self.report_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('private', stderr)

        document = self.read_report()
        document.pop('oracle')
        document['frames'][0]['png'] = '/home/user/p003-f1.png'
        write_json(self.report_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('absolute', stderr)

        document = self.read_report()
        document['frames'][0]['png'] = 'images/p003-f1.png'
        document['bindings']['backend_receipt']['sha256'] = 'f' * 64
        write_json(self.report_path, document)
        code, _, stderr = self.validate('--capture-dir', str(self.capture),
                                        '--gate-plan', str(self.gate_path),
                                        '--backend-receipt', str(self.receipt_path),
                                        audit=lambda capture_dir: self.audit_ok())
        self.assertEqual(code, 1)
        self.assertIn('stale', stderr)

    def test_validate_rejects_tampered_report_metadata(self):
        self.build_report()
        document = self.read_report()
        document['release_ready'] = False
        write_json(self.report_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('release_ready', stderr)

        document['release_ready'] = True
        document['bindings']['machine_qa']['rule_version'] = 'uncalibrated-rule'
        write_json(self.report_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('rule_version', stderr)


if __name__ == '__main__':
    unittest.main()
