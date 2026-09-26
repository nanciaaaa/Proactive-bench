import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pb_backend_plan as pb
import pb_gui_capture_gate as gate

CLUSTER = 'demo-001'
EPISODE = 'ep_0001'
PASSIVE = 'p001'
WORLD = 'w001'
OBSERVED = '2026-09-14T08:30:26+08:00'
DECISION = '2026-09-14T08:35:01+08:00'
APPLIED_AT = '2026-09-14T08:40:00+08:00'
WORLD_HASH = 'c' * 64
PLAN_SHA = 'd' * 64
PNG_BYTES = b'\x89PNG\r\n\x1a\nsynthetic-pb-original'


def shot_sha(packet_id):
    return hashlib.sha256(packet_id.encode('utf8')).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return path


def gui_packet(packet_id, sequence, source, declared=True):
    packet = {'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
              'observed_at': OBSERVED, 'delivered_at': OBSERVED, 'text_event': f'{source} view'}
    if declared:
        packet['screenshot_ref'] = f'passive_inputs/{PASSIVE}/gui/{packet_id}.png'
        packet['screenshot_sha256'] = shot_sha(packet_id)
    return packet


def build_gui_plan(root, packets=None, name='gui_plan.json', **overrides):
    if packets is None:
        packets = [gui_packet('p003', 5, 'calendar'),
                   gui_packet('p009', 12, 'mail'),
                   gui_packet('p013', 15, 'mail', declared=False)]
    plan = {'schema_version': 'pb-gui-bridge.plan.v1', 'generated_at': APPLIED_AT,
            'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
            'world_variant_id': WORLD, 'track': 'mixed_text', 'decision_point': DECISION,
            'screenshot_dir': f'passive_inputs/{PASSIVE}/gui',
            'counts': {'packets': len(packets)}, 'packets': packets}
    plan.update(overrides)
    return write_json(root / name, plan)


def receipt_record(order, source, semantic_ref):
    contract = gate.adapter_contract(source)
    return {'order': order, 'source': source, 'semantic_ref': semantic_ref,
            'adapter': {'module': contract['module'], 'seed': contract['seed'],
                        'read': contract['read']},
            'seed': {'at': OBSERVED, 'function': contract['seed'], 'result': {'seeded': True}},
            'readback': {'at': OBSERVED, 'function': contract['read'], 'result': {'verified': True}},
            'status': 'ok'}


def build_receipt(root, scope_overrides=None, records=None, name='receipt.json', **overrides):
    scope = {'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
             'world_variant_id': WORLD, 'track': 'mixed_text', 'decision_point': DECISION,
             'timezone': 'Asia/Shanghai', 'world_status': 'new_bundle_not_applied_to_android',
             'world_hash': WORLD_HASH, 'device_applied': False}
    if scope_overrides:
        scope.update(scope_overrides)
    if records is None:
        records = [receipt_record(1, 'calendar', 'calendar.event.cal_001'),
                   receipt_record(2, 'mail', 'mail.message.mail_001'),
                   receipt_record(3, 'clock', 'clock.alarm.alarm_101')]
    receipt = {'schema_version': 'pb-backend-apply-receipt.v1', 'status': 'ok', 'command': 'apply',
               'device_applied': True, 'allow_device_apply': True, 'applied_at': APPLIED_AT,
               'plan_binding': {'schema_version': 'pb-backend-plan.v1', 'plan_sha256': PLAN_SHA,
                                'status': 'planned', 'device_applied': False,
                                'episode_scope': scope, 'world_status': scope['world_status'],
                                'world_hash': scope['world_hash']},
               'device': {'serial': 'emulator-5554', 'timezone': 'Asia/Shanghai', 'adb': 'adb'},
               'ordering': ['calendar', 'mail', 'clock'], 'records': records,
               'checks': {'seed_records': len(records), 'seeds': len(records),
                          'readbacks': len(records), 'absence_guards': 3, 'failures': 0}}
    receipt.update(overrides)
    return write_json(root / name, receipt)


class GatePreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.image_dir = self.root / 'passive_inputs' / PASSIVE / 'gui'
        self.image_dir.mkdir(parents=True)
        (self.image_dir / 'p003.png').write_bytes(PNG_BYTES)
        self.gui = build_gui_plan(self.root)
        self.receipt = build_receipt(self.root)
        self.gate_path = self.root / 'gate.json'

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gate.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def preflight(self, gui=None, receipt=None, output=None):
        return self.run_cli(['preflight', '--gui-plan', str(gui or self.gui),
                             '--backend-receipt', str(receipt or self.receipt),
                             '--output', str(output or self.gate_path)])

    def read_gate(self):
        return json.loads(self.gate_path.read_text(encoding='utf8'))

    def test_preflight_success_writes_pending_capture_gate(self):
        code, out, stderr = self.preflight()
        self.assertEqual(code, 0, stderr)
        self.assertTrue(self.gate_path.is_file())
        document = self.read_gate()
        self.assertEqual(document['schema_version'], 'pb-gui-capture-gate.v1')
        self.assertEqual(document['status'], 'ready_for_device_capture')
        self.assertEqual(document['capture_state'], 'pending_device_capture')
        self.assertFalse(document['device_capture_started'])
        self.assertEqual(document['episode_scope']['episode_id'], EPISODE)
        self.assertEqual(document['episode_scope']['cluster_id'], CLUSTER)
        self.assertEqual(document['episode_scope']['passive_id'], PASSIVE)
        self.assertEqual(document['episode_scope']['world_variant_id'], WORLD)

        self.assertEqual([target['sequence'] for target in document['targets']], [5, 12, 15])
        calendar, mail, derived = document['targets']
        self.assertEqual(calendar['packet_id'], 'p003')
        self.assertEqual(calendar['source'], 'calendar')
        self.assertEqual(calendar['observed_at'], OBSERVED)
        self.assertEqual(calendar['screenshot_ref'], 'passive_inputs/p001/gui/p003.png')
        self.assertEqual(calendar['screenshot_sha256'], shot_sha('p003'))
        self.assertTrue(calendar['screenshot_declared'])
        self.assertEqual(calendar['capture_state'], 'pending_device_capture')
        self.assertEqual(calendar['adapter_binding'],
                         {'module': 'scripts/gui_calendar.py', 'seed': 'seed_event',
                          'read': 'read_event'})
        self.assertEqual(calendar['readback_binding']['semantic_refs'], ['calendar.event.cal_001'])
        self.assertTrue(calendar['readback_binding']['verified'])
        self.assertEqual(mail['source'], 'mail')
        self.assertEqual(mail['screenshot_sha256'], shot_sha('p009'))
        self.assertFalse(derived['screenshot_declared'])
        self.assertEqual(derived['screenshot_ref'], 'passive_inputs/p001/gui/p013.png')
        self.assertIsNone(derived['screenshot_sha256'])
        self.assertEqual(derived['capture_state'], 'pending_device_capture')

        self.assertEqual(document['bindings']['gui_plan']['sha256'],
                         hashlib.sha256(self.gui.read_bytes()).hexdigest())
        self.assertEqual(document['bindings']['backend_receipt']['sha256'],
                         hashlib.sha256(self.receipt.read_bytes()).hexdigest())
        self.assertTrue(document['bindings']['backend_receipt']['device_applied'])
        self.assertEqual(sorted(document['adapter_bindings']), ['calendar', 'mail'])
        self.assertEqual(document['adapter_bindings']['mail']['read_function'], 'read_mail')
        self.assertTrue(document['adapter_bindings']['calendar']['readback_verified'])
        self.assertEqual(document['counts'], {'targets': 3, 'declared_screenshot_targets': 2,
                                              'derived_screenshot_targets': 1,
                                              'by_source': {'calendar': 1, 'mail': 2}})
        summary = json.loads(out)
        self.assertEqual(summary['status'], 'ok')
        self.assertEqual(summary['gate_status'], 'ready_for_device_capture')
        self.assertEqual(summary['targets'], 3)

        text = json.dumps(document, ensure_ascii=False)
        self.assertNotIn('capture_complete', text)
        self.assertNotIn('oracle', text.lower())
        self.assertNotIn('SECRET', text)
        self.assertEqual(gate.private_paths(document), [])
        self.assertEqual(pb.absolute_value_paths(document), [])
        self.assertEqual(gate.private_paths(json.loads(out)), [])

        self.assertEqual((self.image_dir / 'p003.png').read_bytes(), PNG_BYTES)
        self.assertEqual(sorted(path.name for path in self.image_dir.iterdir()), ['p003.png'])

    def test_preflight_missing_receipt_fails_closed(self):
        missing = self.root / 'missing-receipt.json'
        code, _, stderr = self.preflight(receipt=missing)
        self.assertEqual(code, 1)
        self.assertIn('receipt', stderr)
        self.assertFalse(self.gate_path.exists())

    def test_preflight_episode_mismatch_fails_closed(self):
        cases = {'episode_id': 'ep_9999', 'cluster_id': 'other-cluster', 'passive_id': 'p999',
                 'world_variant_id': 'w999'}
        for field, value in cases.items():
            with self.subTest(field):
                receipt = build_receipt(self.root, scope_overrides={field: value})
                output = self.root / f'gate-{field}.json'
                code, _, stderr = self.preflight(receipt=receipt, output=output)
                self.assertEqual(code, 1)
                self.assertIn(field, stderr)
                self.assertFalse(output.exists())

    def test_preflight_unsupported_gui_source_fails_closed(self):
        gui = build_gui_plan(self.root, packets=[gui_packet('p003', 5, 'calendar'),
                                                 gui_packet('p020', 20, 'messages')])
        code, _, stderr = self.preflight(gui=gui)
        self.assertEqual(code, 1)
        self.assertIn('messages', stderr)
        self.assertIn('unsupported', stderr.lower())
        self.assertFalse(self.gate_path.exists())

    def test_preflight_source_without_readback_record_fails_closed(self):
        gui = build_gui_plan(self.root, packets=[gui_packet('p030', 30, 'clock')])
        receipt = build_receipt(self.root,
                                records=[receipt_record(1, 'calendar', 'calendar.event.cal_001')])
        code, _, stderr = self.preflight(gui=gui, receipt=receipt)
        self.assertEqual(code, 1)
        self.assertIn('clock', stderr)
        self.assertFalse(self.gate_path.exists())

    def test_preflight_rejects_unverified_records(self):
        broken = receipt_record(2, 'mail', 'mail.message.mail_001')
        broken['readback']['result'] = None
        gui = build_gui_plan(self.root, packets=[gui_packet('p009', 12, 'mail')])
        receipt = build_receipt(self.root,
                                records=[receipt_record(1, 'calendar', 'calendar.event.cal_001'),
                                         broken])
        code, _, stderr = self.preflight(gui=gui, receipt=receipt)
        self.assertEqual(code, 1)
        self.assertIn('readback', stderr)
        self.assertFalse(self.gate_path.exists())

    def test_preflight_device_applied_false_fails_closed(self):
        for overrides in ({'device_applied': False}, {'status': 'failed'},
                          {'schema_version': 'pb-backend-apply-receipt.v0'}):
            with self.subTest(overrides):
                receipt = build_receipt(self.root, **overrides)
                code, _, stderr = self.preflight(receipt=receipt)
                self.assertEqual(code, 1)
                self.assertTrue(stderr.strip())
                self.assertFalse(self.gate_path.exists())

    def test_preflight_rejects_private_fields_and_absolute_paths(self):
        leaked = build_gui_plan(self.root, name='gui-leaked.json')
        payload = json.loads(leaked.read_text(encoding='utf8'))
        payload['oracle'] = {'need': 'SECRET'}
        write_json(leaked, payload)
        code, _, stderr = self.preflight(gui=leaked)
        self.assertEqual(code, 1)
        self.assertIn('private', stderr)

        private_ref = build_gui_plan(self.root, name='gui-private-ref.json')
        payload = json.loads(private_ref.read_text(encoding='utf8'))
        payload['gt_ref'] = 'episodes/ep_0001/private_gt.json'
        write_json(private_ref, payload)
        code, _, stderr = self.preflight(gui=private_ref)
        self.assertEqual(code, 1)
        self.assertIn('gt_ref', stderr)

        absolute = build_gui_plan(self.root, name='gui-absolute.json')
        payload = json.loads(absolute.read_text(encoding='utf8'))
        payload['packets'][0]['screenshot_ref'] = '/home/user/p003.png'
        write_json(absolute, payload)
        code, _, stderr = self.preflight(gui=absolute)
        self.assertEqual(code, 1)
        self.assertIn('bundle-relative', stderr)
        self.assertFalse(self.gate_path.exists())

        tainted_receipt = build_receipt(self.root, name='receipt-tainted.json')
        payload = json.loads(tainted_receipt.read_text(encoding='utf8'))
        payload['plan_binding']['episode_scope']['gt_ref'] = 'episodes/ep_0001/private_gt.json'
        write_json(tainted_receipt, payload)
        code, _, stderr = self.preflight(receipt=tainted_receipt)
        self.assertEqual(code, 1)
        self.assertIn('private', stderr)
        self.assertFalse(self.gate_path.exists())


class GateValidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.gui = build_gui_plan(self.root)
        self.receipt = build_receipt(self.root)
        self.gate_path = self.root / 'gate.json'
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gate.main(['preflight', '--gui-plan', str(self.gui),
                              '--backend-receipt', str(self.receipt),
                              '--output', str(self.gate_path)])
        self.assertEqual(code, 0, stderr.getvalue())

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gate.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def validate(self, *extra):
        return self.run_cli(['validate', '--gate-plan', str(self.gate_path), *extra])

    def rebuild_gate(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gate.main(['preflight', '--gui-plan', str(self.gui),
                              '--backend-receipt', str(self.receipt),
                              '--output', str(self.gate_path)])
        self.assertEqual(code, 0, stderr.getvalue())

    def test_validate_reverifies_hashes_and_bindings(self):
        code, out, stderr = self.validate('--gui-plan', str(self.gui),
                                          '--backend-receipt', str(self.receipt))
        self.assertEqual(code, 0, stderr)
        summary = json.loads(out)
        self.assertEqual(summary['status'], 'ok')
        self.assertTrue(summary['hashes_verified'])
        self.assertEqual(summary['targets'], 3)
        self.assertEqual(pb.private_key_paths(summary), [])
        self.assertEqual(gate.private_paths(summary), [])

        code, out, _ = self.validate()
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)['hashes_verified'])

    def test_validate_detects_stale_gate_plan_and_inputs(self):
        document = json.loads(self.gate_path.read_text(encoding='utf8'))
        document['targets'][0]['screenshot_sha256'] = 'e' * 64
        write_json(self.gate_path, document)
        code, _, stderr = self.validate('--gui-plan', str(self.gui),
                                        '--backend-receipt', str(self.receipt))
        self.assertEqual(code, 1)
        self.assertIn('stale', stderr)

        self.rebuild_gate()
        receipt = json.loads(self.receipt.read_text(encoding='utf8'))
        receipt['applied_at'] = '2026-09-14T09:00:00+08:00'
        write_json(self.receipt, receipt)
        code, _, stderr = self.validate('--gui-plan', str(self.gui),
                                        '--backend-receipt', str(self.receipt))
        self.assertEqual(code, 1)
        self.assertTrue(stderr.strip())

    def test_validate_requires_both_inputs_or_neither(self):
        code, _, stderr = self.validate('--gui-plan', str(self.gui))
        self.assertEqual(code, 1)
        self.assertIn('--backend-receipt', stderr)

    def test_validate_rejects_private_fields_and_completion_claims(self):
        document = json.loads(self.gate_path.read_text(encoding='utf8'))
        document['oracle'] = {}
        write_json(self.gate_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('private', stderr)

        self.rebuild_gate()
        document = json.loads(self.gate_path.read_text(encoding='utf8'))
        document['device_capture_started'] = True
        write_json(self.gate_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('device capture', stderr)

        self.rebuild_gate()
        document = json.loads(self.gate_path.read_text(encoding='utf8'))
        document['targets'][0]['capture_state'] = 'captured'
        write_json(self.gate_path, document)
        code, _, stderr = self.validate()
        self.assertEqual(code, 1)
        self.assertIn('capture_state', stderr)


if __name__ == '__main__':
    unittest.main()
