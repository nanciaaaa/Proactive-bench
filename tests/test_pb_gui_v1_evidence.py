import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gui_machine_qa as machine_qa
import pb_gui_capture_gate as capture_gate
import pb_gui_image_host as host
import pb_gui_v1_evidence as evidence

CLUSTER = 'demo-001'
EPISODE = 'ep_0001'
PASSIVE = 'p001'
OBSERVED = '2026-09-14T08:30:26+08:00'
DECISION = '2026-09-14T08:35:01+08:00'
MODEL = machine_qa.OCR_MODELS['eng']
SCOPE = {'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
         'world_variant_id': 'w001', 'track': 'mixed_text', 'decision_point': DECISION,
         'timezone': 'Asia/Shanghai'}
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def fake_png(tag, width=1080, height=2400):
    return (PNG_MAGIC + b'\x00' * 8 + width.to_bytes(4, 'big') + height.to_bytes(4, 'big')
            + tag.encode('utf8') + b'\x00' * 16)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return path


class EvidenceFixture:
    """Synthetic V1 chain: capture -> QA -> backend -> release -> host -> replay -> bundle."""

    def __init__(self, root):
        self.root = Path(root)
        self.capture = self.root / 'pb-capture-ep0001'
        (self.capture / 'images').mkdir(parents=True, exist_ok=True)
        self.frames = []
        for packet_id, source in (('p002', 'calendar'), ('p004', 'mail')):
            raw = fake_png('device-' + packet_id)
            (self.capture / 'images' / f'{packet_id}.png').write_bytes(raw)
            self.frames.append({'packet_id': packet_id, 'frame_id': f'{packet_id}_f1',
                                'png': f'images/{packet_id}.png', 'sha256': sha(raw),
                                'bytes': len(raw), 'width': 1080, 'height': 2400,
                                'source': source, 'observed_at': OBSERVED})
        self.plan = write_json(self.capture / 'private-plan.json',
                               {'schema_version': 'pb-gui-private-capture-plan.v1',
                                'packets': [dict(frame) for frame in self.frames]})
        self.plan_sha = sha(self.plan.read_bytes())
        self.capture_run_id = 'run-0001'
        write_json(self.capture / 'receipt.json',
                   {'schema_version': '1.0', 'episode_id': EPISODE,
                    'capture_run_id': self.capture_run_id, 'plan_sha256': self.plan_sha,
                    'status': 'capture_complete', 'reset_verified': True,
                    'frames': [{'packet_id': frame['packet_id'],
                                'frame_id': frame['frame_id'], 'png': frame['png'],
                                'sha256': frame['sha256'], 'source': frame['source'],
                                'observed_at': frame['observed_at']}
                               for frame in self.frames]})
        self.qa = write_json(self.capture / 'qa-machine.json', {
            'schema_version': '1.0', 'rule_version': machine_qa.RULE, 'status': 'pass',
            'ocr_model_sha256': MODEL, 'ocr_models': {'eng': MODEL},
            'plan_sha256': self.plan_sha, 'capture_run_id': self.capture_run_id,
            'device_env': {'schema_version': '1.0', 'status': 'pass'},
            'frames': [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                        'png': frame['png'], 'sha256': frame['sha256'], 'status': 'pass',
                        'fields': [{'field': 'event_title', 'text': 'Project review',
                                    'confidence': 95, 'minimum_confidence': 80,
                                    'passed': True, 'language': 'eng',
                                    'expected': 'Project review'}]}
                       for frame in self.frames]})
        self.backend = write_json(self.root / 'pb-backend-receipt-ep0001.json', {
            'schema_version': 'pb-backend-apply-receipt.v1', 'status': 'ok',
            'device_applied': True, 'applied_at': '2026-09-26T00:00:00+00:00',
            'plan_binding': {'schema_version': 'pb-backend-plan.v1',
                             'episode_scope': dict(SCOPE)}})
        self.release = write_json(self.root / 'pb-gui-release-report-ep0001.json', {
            'schema_version': 'pb-gui-release-report.v1', 'status': 'release_ready',
            'release_ready': True, 'episode_scope': dict(SCOPE),
            'bindings': {
                'gate_plan': {'schema_version': 'pb-gui-capture-gate.v1', 'sha256': 'a' * 64,
                              'status': capture_gate.GATE_STATUS, 'targets': 2},
                'backend_receipt': {'schema_version': 'pb-backend-apply-receipt.v1',
                                    'sha256': sha(self.backend.read_bytes()), 'status': 'ok',
                                    'device_applied': True},
                'private_plan': {'schema_version': 'pb-gui-private-capture-plan.v1',
                                 'sha256': self.plan_sha},
                'capture_receipt': {'schema_version': '1.0',
                                    'sha256': sha((self.capture / 'receipt.json').read_bytes()),
                                    'status': 'capture_complete', 'reset_verified': True,
                                    'capture_run_id': self.capture_run_id, 'frames': 2},
                'machine_qa': {'schema_version': '1.0', 'status': 'pass',
                               'rule_version': machine_qa.RULE, 'ocr_model_sha256': MODEL,
                               'frames': 2, 'stored_reports': ['qa-machine.json']}},
            'machine_qa': {'rule_version': machine_qa.RULE, 'ocr_model_sha256': MODEL,
                           'ocr_models': {'eng': MODEL}},
            'counts': {'targets': 2, 'gate_targets': 2, 'unsupported_targets': 0, 'frames': 2,
                       'pass_frames': 2, 'declared_screenshot_targets': 2,
                       'derived_screenshot_targets': 0, 'by_source': {'calendar': 1, 'mail': 1}},
            'frames': [dict(frame, status='pass') for frame in self.frames],
            'checks': {'private_fields_emitted': 0, 'absolute_paths_emitted': 0, 'images_copied': 0,
                       'images_rewritten': 0, 'device_writes': 0, 'gold_generated': 0,
                       'publish_invoked': 0}})
        self.bundle_digest = 'c' * 64
        self.device_bundle = {'sha256': self.bundle_digest, 'mode': 'image', 'frames': 2}
        self.host = write_json(self.root / 'pb-gui-image-host-receipt-ep0001.json', {
            'schema_version': host.HOST_SCHEMA, 'status': host.HOST_STATUS,
            'episode_scope': dict(SCOPE),
            'bindings': {'release_report': {'schema_version': 'pb-gui-release-report.v1',
                                            'sha256': sha(self.release.read_bytes())},
                         'capture_receipt': {
                             'schema_version': '1.0',
                             'sha256': sha((self.capture / 'receipt.json').read_bytes())},
                         'capture_plan': {'schema_version': 'pb-gui-private-capture-plan.v1',
                                          'sha256': self.plan_sha},
                         'pb': {'stream_sha256': 'd' * 64, 'manifest_sha256': 'e' * 64},
                         'device_bundle': dict(self.device_bundle)},
            'pb_invariants': {'max_images_per_window': 2},
            'frames': [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                        'source': frame['source'], 'device_sha256': frame['sha256'],
                        'bytes': frame['bytes'], 'width': 1080, 'height': 2400,
                        'pb_declared_sha256': '9' * 64,
                        'staged_ref': f'passive_inputs/{PASSIVE}/gui/{frame["packet_id"]}.png'}
                       for frame in self.frames],
            'checks': {'pb_writes': 0}, 'claims': {'model_called': False, 'gold': False}})
        self.replay = write_json(self.root / 'pb-gui-image-replay-receipt-ep0001.json', {
            'schema_version': host.REPLAY_SCHEMA, 'status': host.REPLAY_STATUS, 'mode': 'image',
            'episode_scope': dict(SCOPE),
            'bindings': {'host_receipt': {'schema_version': host.HOST_SCHEMA,
                                          'sha256': sha(self.host.read_bytes())},
                         'bundle': dict(self.device_bundle),
                         'stage_manifest_sha256': 'f' * 64},
            'summary_fallbacks': 0, 'image_attachments': 3,
            'checks': {'all_bound_frames_attached': True}})
        # The private image write-back bundle carries the rewritten PB image
        # references (with the PB observed/delivered times) that the evidence
        # bundle binds by hash.
        self.image_bundle_dir = self.root / 'pb-gui-passive-image-bundle-demo-001-ep_0001-p001-v1'
        self.image_bundle_dir.mkdir(parents=True, exist_ok=True)
        self.image_refs = self.image_bundle_dir / 'image-refs.jsonl'
        self.image_refs.write_text(''.join(json.dumps({
            'packet_id': frame['packet_id'], 'sequence': index,
            'tag': 'gui', 'source': frame['source'],
            'observed_at': frame['observed_at'],
            'delivered_at': frame['observed_at'],
            'screenshot_ref': f'passive_inputs/{PASSIVE}/gui/{frame["packet_id"]}.png',
            'screenshot_sha256': frame['sha256']}, ensure_ascii=False) + '\n'
            for index, frame in enumerate(self.frames, start=1)), encoding='utf8')
        self.image_bundle = write_json(self.image_bundle_dir / 'manifest.json', {
            'schema_version': host.BUNDLE_SCHEMA, 'status': host.BUNDLE_STATUS,
            'episode_scope': dict(SCOPE),
            'writeback': {'mode': 'extension_private_bundle', 'pb_tree_modified': False,
                          'pb_writes': 0, 'device_frames': 2, 'image_refs': 2},
            'bindings': {'host_receipt': {'schema_version': host.HOST_SCHEMA,
                                          'sha256': sha(self.host.read_bytes())},
                         'replay_receipt': {'schema_version': host.REPLAY_SCHEMA,
                                            'sha256': sha(self.replay.read_bytes())}},
            'frames': [{'packet_id': frame['packet_id'], 'sha256': frame['sha256'],
                        'screenshot_ref': f'passive_inputs/{PASSIVE}/gui/{frame["packet_id"]}.png',
                        'bundle_path': f'gui/{frame["packet_id"]}.png'}
                       for frame in self.frames],
            'files': [{'path': 'image-refs.jsonl', 'sha256': sha(self.image_refs.read_bytes()),
                       'bytes': self.image_refs.stat().st_size}],
            'checks': {'pb_writes': 0}, 'claims': {'model_called': False, 'gold': False}})

    def episode_spec(self, name='ep_0001'):
        return [name, str(self.release), str(self.capture), str(self.backend), str(self.host),
                str(self.replay), str(self.image_bundle)]


class EvidenceBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = EvidenceFixture(self.tmp.name)
        self.bundle = Path(self.tmp.name) / 'bundle'

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = evidence.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def build(self, *extra):
        return self.run_cli(['build', '--bundle', str(self.bundle),
                             '--episode', *self.fixture.episode_spec(), *extra])

    def verify(self):
        return self.run_cli(['verify', '--bundle', str(self.bundle)])

    def checklist(self, *extra):
        return self.run_cli(['checklist', '--bundle', str(self.bundle), *extra])

    def refs(self):
        return json.loads((self.bundle / 'ep_0001' / 'pb-image-refs-ep_0001.json')
                          .read_text(encoding='utf8'))

    def manifest(self):
        return json.loads((self.bundle / 'ep_0001' / 'episode-evidence.json')
                          .read_text(encoding='utf8'))

    def test_build_and_verify_round_trip(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        code, out, err = self.verify()
        self.assertEqual(code, 0, err)
        self.assertIn('evidence_verified', out)
        manifest = self.manifest()
        self.assertEqual(manifest['schema_version'], evidence.EPISODE_SCHEMA)
        self.assertEqual(manifest['status'], 'evidence_bound')
        self.assertEqual(manifest['counts']['frames'], 2)
        self.assertEqual(manifest['checks']['machine_qa_pass_frames'], 2)
        self.assertEqual(manifest['checks']['image_bundle_pb_writes'], 0)
        self.assertFalse(manifest['checks']['raw_pngs_stored_in_repo'])
        self.assertEqual([frame['packet_id'] for frame in manifest['frames']], ['p002', 'p004'])
        for frame in manifest['frames']:
            self.assertEqual(frame['sha256'], self.fixture.frames[0]['sha256']
                             if frame['packet_id'] == 'p002' else self.fixture.frames[1]['sha256'])
            self.assertEqual(frame['qa_min_confidence'], 95)
        # Raw device PNGs stay out of the repository; the QA summary drops the
        # plan-side ``expected`` text that the private scanner treats as private.
        for path in self.bundle.rglob('*'):
            self.assertNotEqual(path.suffix, '.png')
        summary = (self.bundle / 'ep_0001' / 'pb-machine-qa-summary-ep_0001.json').read_text(
            encoding='utf8')
        self.assertNotIn('expected', summary)
        self.assertNotIn('/tmp', json.dumps(manifest))
        # The coverage audit --evidence-dir scan reads JSON only one level down:
        # the bundle top level must stay free of JSON files.
        self.assertEqual(sorted(path.name for path in self.bundle.iterdir()),
                         ['README.md', 'ep_0001'])

    def test_build_refuses_private_gt_and_local_paths(self):
        backend = json.loads(self.fixture.backend.read_text(encoding='utf8'))
        backend['expected_need_set'] = ['secret']
        write_json(self.fixture.backend, backend)
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('private GT/Need fields', err)
        backend.pop('expected_need_set')
        backend['note'] = '/tmp/pb-capture-ep0001/images/p002.png'
        write_json(self.fixture.backend, backend)
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('local absolute paths', err)

    def test_build_refuses_scope_drift_and_unbound_replay(self):
        backend = json.loads(self.fixture.backend.read_text(encoding='utf8'))
        backend['plan_binding']['episode_scope']['passive_id'] = 'p999'
        write_json(self.fixture.backend, backend)
        release = json.loads(self.fixture.release.read_text(encoding='utf8'))
        release['bindings']['backend_receipt']['sha256'] = sha(self.fixture.backend.read_bytes())
        write_json(self.fixture.release, release)
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('backend receipt passive_id differs', err)
        # A replay receipt bound to other host bytes must stop the write-back gate.
        self.fixture = EvidenceFixture(self.tmp.name)
        replay = json.loads(self.fixture.replay.read_text(encoding='utf8'))
        replay['bindings']['host_receipt']['sha256'] = 'f' * 64
        write_json(self.fixture.replay, replay)
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('replay receipt does not bind the host receipt bytes', err)
        self.assertFalse(self.bundle.exists() and any(self.bundle.iterdir()))

    def test_build_requires_a_safe_episode_name_and_empty_bundle(self):
        code, out, err = self.run_cli(['build', '--bundle', str(self.bundle),
                                       '--episode', *self.fixture.episode_spec('../evil')])
        self.assertEqual(code, 2)
        self.assertIn('single safe path segment', err)
        self.assertFalse(self.bundle.exists())
        self.bundle.mkdir(parents=True)
        (self.bundle / 'stale.txt').write_text('x', encoding='utf8')
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('refusing to overwrite', err)

    def test_verify_catches_file_drift_and_missing_evidence(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        release = self.bundle / 'ep_0001' / 'pb-gui-release-report-ep_0001.json'
        release.write_bytes(release.read_bytes().replace(b'ep_0001', b'ep_0002'))
        code, out, err = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('evidence file drifted', err)
        shutil.rmtree(self.bundle / 'ep_0001')
        code, out, err = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('episode-evidence.json', err)

    def test_verify_rejects_a_bundle_with_top_level_json(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        (self.bundle / 'bundle-manifest.json').write_text('{}\n', encoding='utf8')
        code, out, err = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('top level must not carry JSON files', err)

    def test_verify_rejects_a_tampered_binding_value(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        manifest_path = self.bundle / 'ep_0001' / 'episode-evidence.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        manifest['bindings']['capture_run_id'] = 'forged-run'
        write_json(manifest_path, manifest)
        code, out, err = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('evidence bindings drifted', err)

    def test_build_binds_every_packet_observed_and_delivered_time(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        refs = self.refs()
        self.assertEqual(sorted(refs['pb_packet_times']), ['p002', 'p004'])
        for frame in refs['frames']:
            times = refs['pb_packet_times'][frame['packet_id']]
            self.assertEqual(times['observed_at'], OBSERVED)
            self.assertEqual(times['delivered_at'], OBSERVED)
            self.assertEqual(frame['observed_at'], OBSERVED)
            self.assertEqual(frame['delivered_at'], OBSERVED)
        self.assertEqual(refs['pb_time_binding']['image_refs_path'], 'image-refs.jsonl')
        self.assertEqual(refs['pb_time_binding']['image_refs_sha256'],
                         sha(self.fixture.image_refs.read_bytes()))
        self.assertEqual(refs['pb_time_binding']['pb_stream_sha256'], 'd' * 64)
        manifest = self.manifest()
        self.assertEqual(manifest['checks']['pb_packet_times_bound'], 2)

    def test_build_refuses_unbound_or_drifted_image_refs(self):
        private_refs = self.fixture.image_refs
        raw = private_refs.read_bytes()
        private_refs.unlink()
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('write-back bundle has no image-refs.jsonl', err)
        private_refs.write_bytes(raw)
        private_refs.write_bytes(raw.replace(b'p002', b'p003'))
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('bytes do not match the image bundle manifest', err)
        private_refs.write_bytes(raw)
        bundle = json.loads(self.fixture.image_bundle.read_text(encoding='utf8'))
        bundle['files'] = []
        write_json(self.fixture.image_bundle, bundle)
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('must record image-refs.jsonl', err)

    def test_build_refuses_delivered_at_loss_or_time_drift(self):
        private_refs = self.fixture.image_refs
        lines = [json.loads(line) for line in private_refs.read_text(encoding='utf8').splitlines()]
        del lines[0]['delivered_at']
        private_refs.write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf8')
        self.rebind_private_refs()
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('delivered_at must be an ISO-8601 string', err)
        lines[0]['delivered_at'] = lines[0]['observed_at']
        lines[0]['observed_at'] = '2026-09-14T08:31:00+08:00'
        private_refs.write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf8')
        self.rebind_private_refs()
        code, out, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn('PB observed_at drifted from the capture receipt', err)

    def rebind_private_refs(self):
        bundle = json.loads(self.fixture.image_bundle.read_text(encoding='utf8'))
        bundle['files'] = [{'path': 'image-refs.jsonl',
                            'sha256': sha(self.fixture.image_refs.read_bytes()),
                            'bytes': self.fixture.image_refs.stat().st_size}]
        write_json(self.fixture.image_bundle, bundle)

    def test_verify_rejects_time_binding_drift(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        refs_path = self.bundle / 'ep_0001' / 'pb-image-refs-ep_0001.json'
        refs = json.loads(refs_path.read_text(encoding='utf8'))
        refs['pb_time_binding']['image_refs_sha256'] = '9' * 64
        write_json(refs_path, refs)
        manifest_path = self.bundle / 'ep_0001' / 'episode-evidence.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        for item in manifest['files']:
            if item['path'] == 'pb-image-refs-ep_0001.json':
                item['sha256'] = sha(refs_path.read_bytes())
                item['bytes'] = refs_path.stat().st_size
        write_json(manifest_path, manifest)
        code, out, err = self.verify()
        self.assertEqual(code, 2)
        self.assertIn('image-refs.jsonl sha256 drifted from the image bundle manifest', err)

    def test_checklist_writes_the_single_v1_acceptance_entry(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        code, out, err = self.checklist()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload['status'], 'checklist_written')
        self.assertEqual(payload['verdict'], 'satisfied')
        self.assertEqual(payload['steps'], '10/10')
        self.assertEqual(payload['frozen_fields'], '13/13')
        text = (self.bundle / evidence.CHECKLIST_NAME).read_text(encoding='utf8')
        self.assertIn('判定: **satisfied**', text)
        for step, title, _ in evidence.V1_STEPS:
            self.assertIn(f'| {step} | {title} | satisfied |', text)
        for index, (field, _) in enumerate(evidence.V1_FIELDS, start=1):
            self.assertIn(f'| {index} | `{field}` | bound |', text)
        self.assertEqual(sum(1 for line in text.splitlines()
                              if line.startswith('- `not-applicable (V2)`: ')),
                         len(evidence.V2_ITEMS))
        self.assertNotIn('/tmp', text)
        for item in ('pb-backend-apply-receipt-ep_0001.json',
                     'pb-capture-receipt-ep_0001.json',
                     'pb-passive-image-bundle-ep_0001.json',
                     'pb-image-refs-ep_0001.json'):
            self.assertIn(item, text)
        # Re-running refreshes the derived checklist and keeps verify green.
        code, out, err = self.checklist()
        self.assertEqual(code, 0, err)
        code, out, err = self.verify()
        self.assertEqual(code, 0, err)

    def test_checklist_fails_closed_and_keeps_its_output_inside_the_bundle(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        code, out, err = self.checklist('--output', str(Path(self.tmp.name) / 'outside.md'))
        self.assertEqual(code, 2)
        self.assertIn('must stay inside the evidence bundle', err)
        release = self.bundle / 'ep_0001' / 'pb-gui-release-report-ep_0001.json'
        release.write_bytes(release.read_bytes().replace(b'ep_0001', b'ep_0002'))
        code, out, err = self.checklist()
        self.assertEqual(code, 2)
        self.assertIn('evidence file drifted', err)
        self.assertFalse((self.bundle / evidence.CHECKLIST_NAME).exists())


if __name__ == '__main__':
    unittest.main()
