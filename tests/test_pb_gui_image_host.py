import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pb_gui_image_host as host

CLUSTER = 'demo-001'
EPISODE = 'ep_0001'
PASSIVE = 'p001'
OBSERVED = '2026-09-14T08:30:26+08:00'
DECISION = '2026-09-14T08:35:01+08:00'
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


def gui_packet(packet_id, sequence, source):
    raw = fake_png('pb-original-' + packet_id)
    return {'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
            'observed_at': OBSERVED, 'delivered_at': OBSERVED,
            'text_event': f'{source} view', 'author_provenance': {'kind': 'observed'},
            'screenshot_ref': f'passive_inputs/{PASSIVE}/gui/{packet_id}.png',
            'screenshot_sha256': sha(raw)}, raw


def text_packet(packet_id, sequence, source):
    return {'packet_id': packet_id, 'sequence': sequence, 'tag': 'app_switch', 'source': source,
            'observed_at': OBSERVED, 'delivered_at': OBSERVED, 'text_event': 'switch',
            'author_provenance': {'kind': 'observed'}}


class ImageHostFixture:
    """Synthetic PB-like root + capture + release report; no device, no model."""

    def __init__(self, root):
        self.root = Path(root)
        self.pb_root = self.root / 'pb'
        self.cluster_dir = self.build_pb_root()
        self.capture = self.root / 'capture'
        self.report_path = self.root / 'release-report.json'
        self.frame_shas = {}
        self.build_capture_and_report()

    def build_pb_root(self):
        pb_root = self.pb_root
        write_json(pb_root / 'benchmark.json', {
            'layout_version': 'probe', 'default_cluster': CLUSTER,
            'clusters': {CLUSTER: {'path': f'single-scenarios/{CLUSTER}'}},
            'prompts': {'contract': 'prompts/agent_contract.json', 'policy': 'prompts/policy.md',
                        'format_guard': 'prompts/format_guard.md'}})
        write_json(pb_root / 'prompts' / 'agent_contract.json',
                   {'search_budget': 6, 'passive_input': {'window': {'size': 3}}})
        (pb_root / 'prompts' / 'policy.md').write_text('policy\n', encoding='utf8')
        (pb_root / 'prompts' / 'format_guard.md').write_text('guard\n', encoding='utf8')
        write_json(pb_root / 'shared' / 'source_capabilities.json', {'schema_version': 'x', 'sources': []})
        write_json(pb_root / 'shared' / 'baselines' / 'mobileworld_init_v1' / 'world_base.json', {})
        cluster = pb_root / 'single-scenarios' / CLUSTER
        for relative in ('active_world/cluster_base.patch.json',
                         'active_world/variants/w001.patch.json',
                         'active_world/access/w001/evidence_tree.json',
                         'active_world/access/w001/bindings.json',
                         'active_world/history/w001.json'):
            write_json(cluster / relative, {})
        write_json(cluster / 'cluster.json', {
            'cluster_id': CLUSTER, 'global_base_ref': 'shared://baselines/mobileworld_init_v1/world_base.json',
            'cluster_base_ref': 'active_world/cluster_base.patch.json',
            'source_registry_ref': 'shared://source_capabilities.json',
            'world_variants': [{'world_variant_id': 'w001',
                                'patch_ref': 'active_world/variants/w001.patch.json',
                                'access_ref': 'active_world/access/w001/evidence_tree.json',
                                'binding_ref': 'active_world/access/w001/bindings.json',
                                'history_ref': 'active_world/history/w001.json'}],
            'passive_variants': [{'passive_id': PASSIVE,
                                  'stream_ref': f'passive_inputs/{PASSIVE}/stream.jsonl',
                                  'track': 'mixed_text', 'provenance': 'hybrid_probe',
                                  'release_status': 'development_only_probe'}],
            'episode_ids': [EPISODE]})
        write_json(cluster / 'episodes' / EPISODE / 'manifest.json', {
            'episode_id': EPISODE, 'world_variant_id': 'w001', 'passive_id': PASSIVE,
            'passive_ref': f'passive_inputs/{PASSIVE}/stream.jsonl',
            'access_ref': 'active_world/access/w001/evidence_tree.json',
            'binding_ref': 'active_world/access/w001/bindings.json',
            'history_ref': 'active_world/history/w001.json',
            'track': 'mixed_text', 'decision_point': DECISION, 'timezone': 'Asia/Shanghai'})
        gui_dir = cluster / 'passive_inputs' / PASSIVE / 'gui'
        packets = [text_packet('p001', 1, 'launcher'), 'gui-p002']
        p002, raw002 = gui_packet('p002', 2, 'calendar')
        p004, raw004 = gui_packet('p004', 4, 'mail')
        packets = [text_packet('p001', 1, 'launcher'), p002, text_packet('p003', 3, 'launcher'),
                   p004, text_packet('p005', 5, 'launcher')]
        write_json(gui_dir / 'p002.png', {})
        write_json(gui_dir / 'p004.png', {})
        (gui_dir / 'p002.png').write_bytes(raw002)
        (gui_dir / 'p004.png').write_bytes(raw004)
        stream = cluster / 'passive_inputs' / PASSIVE / 'stream.jsonl'
        stream.write_text(''.join(json.dumps(packet, ensure_ascii=False) + '\n' for packet in packets),
                          encoding='utf8')
        return cluster

    def build_capture_and_report(self):
        images = self.capture / 'images'
        images.mkdir(parents=True)
        frames = []
        for packet_id in ('p002', 'p004'):
            raw = fake_png('device-frame-' + packet_id)
            path = images / f'{packet_id}.png'
            path.write_bytes(raw)
            self.frame_shas[packet_id] = sha(raw)
            frames.append({'packet_id': packet_id, 'frame_id': f'{packet_id}_f1',
                           'png': f'images/{packet_id}.png', 'sha256': sha(raw),
                           'width': 1080, 'height': 2400,
                           'source': 'calendar' if packet_id == 'p002' else 'mail',
                           'observed_at': OBSERVED})
        plan = write_json(self.capture / 'private-plan.json',
                          {'schema_version': 'pb-gui-private-capture-plan.v1', 'packets': frames})
        receipt = {'schema_version': '1.0', 'episode_id': EPISODE, 'capture_run_id': 'probe-run',
                   'plan_sha256': sha(plan.read_bytes()), 'status': 'capture_complete',
                   'reset_verified': True, 'frames': frames}
        write_json(self.capture / 'receipt.json', receipt)
        report = {'schema_version': 'pb-gui-release-report.v1', 'status': 'release_ready',
                  'release_ready': True, 'episode_scope': {
                      'cluster_id': CLUSTER, 'episode_id': EPISODE, 'passive_id': PASSIVE,
                      'world_variant_id': 'w001', 'track': 'mixed_text',
                      'decision_point': DECISION, 'timezone': 'Asia/Shanghai'},
                  'bindings': {'private_plan': {'sha256': sha(plan.read_bytes())},
                               'capture_receipt': {'sha256': sha((self.capture / 'receipt.json').read_bytes())}},
                  'counts': {'frames': len(frames), 'pass_frames': len(frames)},
                  'frames': [dict(frame, status='pass') for frame in frames]}
        write_json(self.report_path, report)
        return report

    def preflight_args(self, *extra):
        return ['preflight', '--release-report', str(self.report_path),
                '--capture-dir', str(self.capture), '--pb-root', str(self.pb_root),
                '--stage-dir', str(self.root / 'stage'),
                '--output', str(self.root / 'host-receipt.json'), '--endpoint', '', *extra]


class ImageHostPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = ImageHostFixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = host.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def read_receipt(self):
        return json.loads((Path(self.tmp.name) / 'host-receipt.json').read_text(encoding='utf8'))

    def test_preflight_binds_device_bundle_and_stages_input_view(self):
        code, out, err = self.run_cli(self.fixture.preflight_args())
        self.assertEqual(code, 0, err)
        receipt = self.read_receipt()
        self.assertEqual(receipt['status'], 'bound')
        self.assertEqual(receipt['pb_invariants']['max_images_per_window'], 2)
        self.assertEqual(receipt['pb_invariants']['window_image_counts'], [1, 2, 1])
        self.assertEqual(receipt['live_readiness']['status'], 'blocked')
        self.assertIn('missing_capability_declaration', receipt['gaps'])
        self.assertEqual(receipt['claims']['model_called'], False)
        staged = Path(self.tmp.name) / 'stage' / 'single-scenarios' / CLUSTER / \
            'passive_inputs' / PASSIVE / 'gui'
        for packet_id, frame_sha in self.fixture.frame_shas.items():
            self.assertEqual(sha((staged / f'{packet_id}.png').read_bytes()), frame_sha)
        stream = (Path(self.tmp.name) / 'stage' / 'single-scenarios' / CLUSTER /
                  'passive_inputs' / PASSIVE / 'stream.jsonl').read_text(encoding='utf8')
        for packet_id, frame_sha in self.fixture.frame_shas.items():
            packet = next(json.loads(line) for line in stream.splitlines()
                          if json.loads(line)['packet_id'] == packet_id)
            self.assertEqual(packet['screenshot_sha256'], frame_sha)

    def test_preflight_rejects_republished_pb_original(self):
        report = json.loads(self.fixture.report_path.read_text(encoding='utf8'))
        original = (self.fixture.cluster_dir / 'passive_inputs' / PASSIVE / 'gui' / 'p002.png').read_bytes()
        png = self.fixture.capture / 'images' / 'p002.png'
        png.write_bytes(original)
        for frame in report['frames']:
            if frame['packet_id'] == 'p002':
                frame['sha256'] = sha(original)
        receipt = json.loads((self.fixture.capture / 'receipt.json').read_text(encoding='utf8'))
        for frame in receipt['frames']:
            if frame['packet_id'] == 'p002':
                frame['sha256'] = sha(original)
        write_json(self.fixture.capture / 'receipt.json', receipt)
        report['bindings']['capture_receipt']['sha256'] = sha(
            (self.fixture.capture / 'receipt.json').read_bytes())
        write_json(self.fixture.report_path, report)
        code, out, err = self.run_cli(self.fixture.preflight_args())
        self.assertEqual(code, 1)
        self.assertIn('republished PB original', err)

    def test_preflight_rejects_frame_set_mismatch(self):
        report = json.loads(self.fixture.report_path.read_text(encoding='utf8'))
        report['frames'] = [frame for frame in report['frames'] if frame['packet_id'] != 'p004']
        report['counts'] = {'frames': 1, 'pass_frames': 1}
        write_json(self.fixture.report_path, report)
        code, out, err = self.run_cli(self.fixture.preflight_args())
        self.assertEqual(code, 1)
        self.assertIn('do not equal current PB GUI packets', err)

    def test_preflight_rejects_tampered_capture_png(self):
        png = self.fixture.capture / 'images' / 'p004.png'
        png.write_bytes(png.read_bytes() + b'x')
        code, out, err = self.run_cli(self.fixture.preflight_args())
        self.assertEqual(code, 1)
        self.assertIn('do not match the release report sha256', err)

    def test_preflight_require_live_fails_closed_with_gap(self):
        code, out, err = self.run_cli(self.fixture.preflight_args('--require-live'))
        self.assertEqual(code, 2)
        receipt = self.read_receipt()
        self.assertEqual(receipt['live_readiness']['status'], 'blocked')
        self.assertEqual(receipt['claims']['model_called'], False)
        self.assertIn('live run blocked (fail closed)', err)

    def test_preflight_anthropic_capability_dry_run_stays_blocked(self):
        caps = write_json(Path(self.tmp.name) / 'caps-anthropic.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'provider': 'anthropic',
            'model': 'qwen27b-local', 'input_modalities': ['text', 'image'],
            'max_images_per_request': 1})
        code, out, err = self.run_cli(self.fixture.preflight_args(
            '--capabilities', str(caps)))
        self.assertEqual(code, 0, err)
        receipt = self.read_receipt()
        self.assertEqual(receipt['adapter_preflight']['status'], 'pass')
        self.assertEqual(receipt['adapter_preflight']['provider'], 'anthropic')
        self.assertEqual(receipt['adapter_preflight']['model_called'], False)
        self.assertEqual(receipt['adapter_preflight']['network_calls'], 0)
        self.assertEqual(receipt['live_readiness']['provider'], 'anthropic')
        self.assertEqual(receipt['live_readiness']['status'], 'blocked')
        self.assertIn('missing_endpoint', receipt['gaps'])
        self.assertIn('insufficient_image_capacity: declared 1 < required 2',
                      receipt['gaps'])
        self.assertTrue(receipt['checks']['adapter_preflight_pass'])
        self.assertEqual(receipt['claims']['model_called'], False)

    def test_preflight_legacy_capability_without_provider_is_blocked(self):
        caps = write_json(Path(self.tmp.name) / 'caps-legacy.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'model': 'qwen27b-local',
            'input_modalities': ['text', 'image'], 'max_images_per_request': 1})
        code, out, err = self.run_cli(self.fixture.preflight_args(
            '--capabilities', str(caps)))
        self.assertEqual(code, 0, err)
        receipt = self.read_receipt()
        self.assertIn('missing_provider_declaration', receipt['gaps'])
        self.assertEqual(receipt['live_readiness']['status'], 'blocked')
        self.assertEqual(receipt['adapter_preflight']['status'], 'not_applicable')
        self.assertFalse(receipt['checks']['adapter_preflight_pass'])

    def test_replay_verifies_bound_window_images(self):
        self.run_cli(self.fixture.preflight_args())
        receipt_path = Path(self.tmp.name) / 'host-receipt.json'
        stage = Path(self.tmp.name) / 'stage'
        receipt = json.loads(receipt_path.read_text(encoding='utf8'))
        windows = []
        for counts in ([('p002',)], [('p002',), ('p004',)], [('p004',)]):
            images = [{'packet_id': pid, 'sha256': self.fixture.frame_shas[pid]}
                      for (pid,) in counts]
            windows.append({'state': {'delivered_count': 3, 'eof': False, 'phase': 'streaming',
                                      'current_time': OBSERVED},
                            'gui_packet_ids': [image['packet_id'] for image in images],
                            'images': images, 'request_sha256': 'a' * 64})
        fake = {'runtime_file': 'probe', 'runtime_sha256': 'b' * 64, 'window_size': 3,
                'search_budget': 6, 'windows': windows}
        argv = ['replay', '--host-receipt', str(receipt_path), '--stage-dir', str(stage),
                '--output', str(Path(self.tmp.name) / 'replay.json'), '--image-capacity', '2',
                '--pb-root', str(self.fixture.pb_root)]
        with unittest.mock.patch.object(host, 'inspect_session', return_value=fake):
            code, out, err = self.run_cli(argv)
        self.assertEqual(code, 0, err)
        replay = json.loads((Path(self.tmp.name) / 'replay.json').read_text(encoding='utf8'))
        self.assertEqual(replay['status'], 'replay_verified')
        self.assertEqual(replay['distinct_frames'], 2)
        self.assertEqual(replay['image_attachments'], 4)
        self.assertEqual(replay['claims']['model_called'], False)
        self.assertTrue(replay['checks']['summary_fallback_refused_without_capacity'])
        self.assertTrue(replay['checks']['all_bound_frames_attached'])
        self.assertEqual(len(receipt['bindings']['pb']['entry']), len(
            json.loads(receipt_path.read_text(encoding='utf8'))['bindings']['pb']['entry']))

    def test_replay_rejects_unbound_image_bytes(self):
        self.run_cli(self.fixture.preflight_args())
        windows = [{'state': {'delivered_count': 3, 'eof': False, 'phase': 'streaming',
                              'current_time': OBSERVED},
                    'gui_packet_ids': ['p002'],
                    'images': [{'packet_id': 'p002', 'sha256': 'f' * 64}],
                    'request_sha256': 'a' * 64},
                   {'state': {'delivered_count': 5, 'eof': True, 'phase': 'final',
                              'current_time': DECISION},
                    'gui_packet_ids': ['p004'],
                    'images': [{'packet_id': 'p004', 'sha256': self.fixture.frame_shas['p004']}],
                    'request_sha256': 'b' * 64}]
        fake = {'runtime_file': 'probe', 'runtime_sha256': 'b' * 64, 'window_size': 3,
                'search_budget': 6, 'windows': windows}
        argv = ['replay', '--host-receipt', str(Path(self.tmp.name) / 'host-receipt.json'),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(Path(self.tmp.name) / 'replay.json'), '--image-capacity', '2',
                '--pb-root', str(self.fixture.pb_root)]
        with unittest.mock.patch.object(host, 'inspect_session', return_value=fake):
            code, out, err = self.run_cli(argv)
        self.assertEqual(code, 1)
        self.assertIn('replay image binding failed', err)


class ImageHostWritebackTest(unittest.TestCase):
    """The write-back bundle is a persistent private artifact; PB stays read-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = ImageHostFixture(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.receipt_path = Path(self.tmp.name) / 'host-receipt.json'
        self.stage_dir = Path(self.tmp.name) / 'stage'
        self.replay_path = Path(self.tmp.name) / 'replay.json'
        self.bundle_dir = Path(self.tmp.name) / 'image-bundle'

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = host.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def replay_windows(self):
        windows = []
        for counts in ([('p002',)], [('p002',), ('p004',)], [('p004',)]):
            images = [{'packet_id': packet_id, 'sha256': self.fixture.frame_shas[packet_id]}
                      for (packet_id,) in counts]
            windows.append({'state': {'delivered_count': 3, 'eof': False, 'phase': 'streaming',
                                      'current_time': OBSERVED},
                            'gui_packet_ids': [image['packet_id'] for image in images],
                            'images': images, 'request_sha256': 'a' * 64})
        return {'runtime_file': 'probe', 'runtime_sha256': 'b' * 64, 'window_size': 3,
                'search_budget': 6, 'windows': windows}

    def prepare_replayed(self, stage_dir=None, resolve_default=False):
        stage = Path(stage_dir or self.stage_dir)
        code, out, err = self.run_cli(self.fixture.preflight_args('--stage-dir', str(stage)))
        self.assertEqual(code, 0, err)
        argv = ['replay', '--host-receipt', str(self.receipt_path),
                '--output', str(self.replay_path), '--image-capacity', '2',
                '--pb-root', str(self.fixture.pb_root)]
        if not resolve_default:
            argv += ['--stage-dir', str(stage)]
        with unittest.mock.patch.object(host, 'inspect_session',
                                        return_value=self.replay_windows()):
            code, out, err = self.run_cli(argv)
        self.assertEqual(code, 0, err)

    def writeback_args(self, *extra):
        return ['writeback', '--host-receipt', str(self.receipt_path),
                '--replay-receipt', str(self.replay_path),
                '--stage-dir', str(self.stage_dir),
                '--bundle-dir', str(self.bundle_dir),
                '--pb-root', str(self.fixture.pb_root), *extra]

    def test_writeback_materializes_persistent_private_bundle(self):
        self.prepare_replayed()
        before = host.sha256_tree(self.fixture.pb_root)
        code, out, err = self.run_cli(self.writeback_args())
        self.assertEqual(code, 0, err)
        self.assertEqual(host.sha256_tree(self.fixture.pb_root), before)
        manifest = json.loads((self.bundle_dir / 'manifest.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['schema_version'], host.BUNDLE_SCHEMA)
        self.assertEqual(manifest['status'], host.BUNDLE_STATUS)
        self.assertEqual(manifest['checks']['pb_writes'], 0)
        self.assertFalse(manifest['writeback']['pb_tree_modified'])
        self.assertTrue(manifest['checks']['device_frames_differ_from_pb'])
        self.assertEqual(manifest['bindings']['replay_receipt']['status'], 'replay_verified')
        self.assertEqual(manifest['counts']['frames'], 2)
        self.assertEqual(manifest['counts']['image_refs'], 2)
        self.assertEqual(manifest['counts']['files'], 3)
        self.assertGreater(manifest['counts']['bytes'], 0)
        for packet_id, frame_sha in self.fixture.frame_shas.items():
            raw = (self.bundle_dir / 'gui' / f'{packet_id}.png').read_bytes()
            self.assertEqual(sha(raw), frame_sha)
        refs = [json.loads(line) for line in
                (self.bundle_dir / 'image-refs.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertEqual(sorted(ref['packet_id'] for ref in refs), ['p002', 'p004'])
        for ref in refs:
            self.assertEqual(ref['screenshot_sha256'], self.fixture.frame_shas[ref['packet_id']])
            self.assertEqual(ref['screenshot_ref'],
                             f'passive_inputs/{PASSIVE}/gui/{ref["packet_id"]}.png')
        self.assertNotIn(str(self.tmp.name), json.dumps(manifest))

    def test_writeback_refuses_a_nonempty_bundle_dir(self):
        self.prepare_replayed()
        self.bundle_dir.mkdir(parents=True)
        (self.bundle_dir / 'stale.txt').write_text('x', encoding='utf8')
        code, out, err = self.run_cli(self.writeback_args())
        self.assertEqual(code, 1)
        self.assertIn('refusing to overwrite', err)

    def test_writeback_refuses_a_replay_receipt_bound_to_other_bytes(self):
        self.prepare_replayed()
        replay = json.loads(self.replay_path.read_text(encoding='utf8'))
        replay['bindings']['host_receipt']['sha256'] = 'f' * 64
        write_json(self.replay_path, replay)
        code, out, err = self.run_cli(self.writeback_args())
        self.assertEqual(code, 1)
        self.assertIn('does not bind these host receipt bytes', err)
        self.assertFalse((self.bundle_dir / 'manifest.json').exists())

    def test_writeback_refuses_pb_and_stage_drift(self):
        self.prepare_replayed()
        policy = self.fixture.pb_root / 'prompts' / 'policy.md'
        policy.write_text('changed\n', encoding='utf8')
        code, out, err = self.run_cli(self.writeback_args())
        self.assertEqual(code, 1)
        self.assertIn('PB code/prompt bytes drifted', err)
        policy.write_text('policy\n', encoding='utf8')
        staged = (self.stage_dir / 'single-scenarios' / CLUSTER / 'passive_inputs'
                  / PASSIVE / 'gui' / 'p002.png')
        staged.write_bytes(staged.read_bytes() + b'x')
        code, out, err = self.run_cli(self.writeback_args())
        self.assertEqual(code, 1)
        self.assertIn('drifted', err)

    def test_writeback_refuses_a_bundle_inside_the_pb_root(self):
        self.prepare_replayed()
        argv = ['writeback', '--host-receipt', str(self.receipt_path),
                '--replay-receipt', str(self.replay_path), '--stage-dir', str(self.stage_dir),
                '--bundle-dir', str(self.fixture.pb_root / 'bundle'),
                '--pb-root', str(self.fixture.pb_root)]
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 1)
        self.assertIn('must not be written inside --pb-root', err)

    def test_writeback_resolves_a_project_relative_stage_ref(self):
        stage_dir = Path(tempfile.mkdtemp(dir=ROOT / 'runtime', prefix='test-stage-writeback-'))
        self.addCleanup(shutil.rmtree, stage_dir, ignore_errors=True)
        bundle_dir = Path(tempfile.mkdtemp(dir=ROOT / 'runtime', prefix='test-bundle-writeback-'))
        shutil.rmtree(bundle_dir)
        self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
        self.prepare_replayed(stage_dir=stage_dir, resolve_default=True)
        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        self.assertTrue(receipt['stage']['ref'].startswith('runtime/test-stage-writeback-'))
        argv = ['writeback', '--host-receipt', str(self.receipt_path),
                '--replay-receipt', str(self.replay_path),
                '--bundle-dir', str(bundle_dir), '--pb-root', str(self.fixture.pb_root)]
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 0, err)
        self.assertTrue((bundle_dir / 'manifest.json').is_file())


class ImageHostLiveGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = ImageHostFixture(self.tmp.name)
        self.live_root = Path(tempfile.mkdtemp(dir=ROOT / 'runtime', prefix='test-live-host-'))
        self.addCleanup(shutil.rmtree, self.live_root, ignore_errors=True)
        self.capabilities = write_json(Path(self.tmp.name) / 'caps.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'provider': 'openai',
            'model': 'probe-model',
            'input_modalities': ['text', 'image'], 'max_images_per_request': 2})

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = host.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def prepare_receipt(self, *, ready=True):
        code, out, err = self.run_cli(self.fixture.preflight_args())
        self.assertEqual(code, 0, err)
        path = Path(self.tmp.name) / 'host-receipt.json'
        receipt = json.loads(path.read_text(encoding='utf8'))
        if ready:
            receipt['live_readiness']['status'] = 'ready'
            receipt['gaps'] = []
        write_json(path, receipt)
        return path

    def test_live_requires_explicit_confirmation(self):
        receipt = self.prepare_receipt()
        argv = ['live', '--host-receipt', str(receipt),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(self.live_root / 'live'), '--pb-root', str(self.fixture.pb_root),
                '--capabilities', str(self.capabilities), '--model', 'probe-model']
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 1)
        self.assertIn('--confirm-live', err)
        self.assertFalse((self.live_root / 'live').exists())

    def test_live_blocks_without_endpoint_without_calling_model(self):
        receipt = self.prepare_receipt()
        output = self.live_root / 'live'
        argv = ['live', '--host-receipt', str(receipt),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(output), '--pb-root', str(self.fixture.pb_root),
                '--capabilities', str(self.capabilities), '--model', 'probe-model',
                '--endpoint', 'http://127.0.0.1:9', '--timeout', '1', '--confirm-live']
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 2)
        document = json.loads((output / 'live-receipt.json').read_text(encoding='utf8'))
        self.assertEqual(document['status'], 'live_blocked')
        self.assertEqual(document['reason'], 'no_reachable_model_endpoint')
        self.assertEqual(document['provider'], 'openai')
        self.assertEqual(document['model_called'], False)
        self.assertTrue((output / 'connection_failure.json').is_file())
        self.assertFalse((output / 'source_snapshot').exists())

    def test_live_requires_explicit_provider_before_any_call(self):
        receipt = self.prepare_receipt()
        legacy = write_json(Path(self.tmp.name) / 'caps-legacy.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'model': 'probe-model',
            'input_modalities': ['text', 'image'], 'max_images_per_request': 2})
        output = self.live_root / 'live-legacy'
        argv = ['live', '--host-receipt', str(receipt),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(output), '--pb-root', str(self.fixture.pb_root),
                '--capabilities', str(legacy), '--model', 'probe-model',
                '--endpoint', 'http://127.0.0.1:9', '--timeout', '1', '--confirm-live']
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 1)
        self.assertIn('provider', err)
        self.assertFalse(output.exists())

    def test_live_anthropic_seed_refused_without_model_call(self):
        receipt = self.prepare_receipt()
        caps = write_json(Path(self.tmp.name) / 'caps-anthropic.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'provider': 'anthropic',
            'model': 'probe-model', 'input_modalities': ['text', 'image'],
            'max_images_per_request': 2})
        output = self.live_root / 'live-seed'
        argv = ['live', '--host-receipt', str(receipt),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(output), '--pb-root', str(self.fixture.pb_root),
                '--capabilities', str(caps), '--model', 'probe-model',
                '--endpoint', 'http://127.0.0.1:9', '--timeout', '1', '--seed', '7',
                '--confirm-live']
        with unittest.mock.patch.object(host, 'request_model',
                                        side_effect=AssertionError('model called')):
            code, out, err = self.run_cli(argv)
        self.assertEqual(code, 1)
        self.assertIn('seed', err)
        self.assertFalse(output.exists())

    def test_live_anthropic_missing_key_blocks_before_model_call(self):
        receipt = self.prepare_receipt()
        caps = write_json(Path(self.tmp.name) / 'caps-anthropic.json', {
            'schema_version': 'pb-gui-image-capabilities.v1', 'provider': 'anthropic',
            'model': 'probe-model', 'input_modalities': ['text', 'image'],
            'max_images_per_request': 2})
        output = self.live_root / 'live-anthropic'
        probe = {'endpoint': 'http://127.0.0.1:45691', 'provider': 'anthropic',
                 'auth_header': 'x-api-key', 'request_path': '/v1/messages', 'requests': [],
                 'credential_env': None, 'credential_present': False, 'health_status': 'ok',
                 'model_advertised': True, 'models_advertised': 1, 'auth_rejected': False,
                 'error': None}
        argv = ['live', '--host-receipt', str(receipt),
                '--stage-dir', str(Path(self.tmp.name) / 'stage'),
                '--output', str(output), '--pb-root', str(self.fixture.pb_root),
                '--capabilities', str(caps), '--model', 'probe-model',
                '--endpoint', 'http://127.0.0.1:45691', '--timeout', '1', '--confirm-live']
        with unittest.mock.patch.object(host, 'probe_endpoint', return_value=probe), \
                unittest.mock.patch.object(host, 'request_model',
                                           side_effect=AssertionError('model called')):
            code, out, err = self.run_cli(argv)
        self.assertEqual(code, 2)
        document = json.loads((output / 'live-receipt.json').read_text(encoding='utf8'))
        self.assertEqual(document['status'], 'live_blocked')
        self.assertEqual(document['reason'], 'missing_api_key_for_anthropic')
        self.assertEqual(document['model_called'], False)
        self.assertEqual(document['adapter_preflight']['status'], 'pass')
        self.assertFalse((output / 'source_snapshot').exists())


class ImageHostProviderAdapterTest(unittest.TestCase):
    """Hermetic provider adapter checks: no endpoint, no key value, no model call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def caps(self, name, payload):
        return write_json(self.root / name, {
            'schema_version': 'pb-gui-image-capabilities.v1', **payload})

    def test_capability_provider_is_never_guessed(self):
        legacy = host.load_capabilities(self.caps('legacy.json', {
            'model': 'probe-model', 'input_modalities': ['text', 'image'],
            'max_images_per_request': 5}))
        self.assertIsNone(legacy['provider'])
        self.assertFalse(legacy['provider_declared'])
        gaps = host.live_gaps(legacy, None, 5, 'probe-model')
        self.assertIn('missing_provider_declaration', gaps)
        self.assertIn('missing_endpoint', gaps)
        self.assertEqual(host.adapter_dry_run(legacy)['status'], 'not_applicable')
        unknown = self.caps('unknown.json', {
            'provider': 'mystery', 'model': 'probe-model',
            'input_modalities': ['text', 'image'], 'max_images_per_request': 5})
        with self.assertRaises(host.HostError):
            host.load_capabilities(unknown)

    def test_dry_run_passes_for_both_providers_without_network(self):
        for provider in (host.PROVIDER_OPENAI, host.PROVIDER_ANTHROPIC):
            caps = host.load_capabilities(self.caps(f'{provider}.json', {
                'provider': provider, 'model': 'probe-model',
                'input_modalities': ['text', 'image'], 'max_images_per_request': 5}))
            dry = host.adapter_dry_run(caps)
            self.assertEqual(dry['status'], 'pass', dry)
            self.assertTrue(all(dry['checks'].values()), dry['checks'])
            self.assertEqual(dry['model_called'], False)
            self.assertEqual(dry['network_calls'], 0)

    def test_anthropic_adapter_translates_png_blocks_without_network(self):
        raw = fake_png('adapter-unit')
        canonical = {'model': 'claude-probe', 'max_tokens': 64, 'temperature': 0.0,
                     'enable_thinking': False, 'seed': 0, 'stream': False,
                     'messages': [
                         {'role': 'system', 'content': 'system text'},
                         {'role': 'user', 'content': [
                             {'type': 'text', 'text': '{"phase":"probe"}'},
                             {'type': 'text', 'text': '{"packet_id":"p002"}'},
                             {'type': 'image_url', 'image_url': {
                                 'url': 'data:image/png;base64,'
                                        + base64.b64encode(raw).decode('ascii')}}]}]}
        with unittest.mock.patch.object(host, 'http_json',
                                        side_effect=AssertionError('network used')):
            adapted = host.adapt_request(host.PROVIDER_ANTHROPIC, canonical)
            parsed = host.parse_response(
                host.PROVIDER_ANTHROPIC, host.provider_stub_response(host.PROVIDER_ANTHROPIC))
        self.assertEqual(adapted['system'], 'system text')
        self.assertEqual(len(adapted['messages']), 1)
        blocks = adapted['messages'][0]['content']
        self.assertEqual([block['type'] for block in blocks], ['text', 'text', 'image'])
        self.assertEqual(base64.b64decode(blocks[-1]['source']['data'], validate=True), raw)
        self.assertEqual(blocks[-1]['source']['media_type'], 'image/png')
        self.assertNotIn('image_url', json.dumps(adapted))
        self.assertEqual(parsed['text'], '{"action":"noop"}')
        self.assertEqual(parsed['stop'], 'end_turn')
        self.assertEqual(parsed['usage'], {'prompt_tokens': 1, 'completion_tokens': 1,
                                           'total_tokens': 2})
        self.assertEqual(host.adapter_dropped_params(host.PROVIDER_ANTHROPIC, canonical),
                         ['seed', 'enable_thinking', 'stream'])

    def test_parse_response_refuses_truncation_for_both_providers(self):
        with self.assertRaises(host.HostError):
            host.parse_response(host.PROVIDER_ANTHROPIC, {
                'stop_reason': 'max_tokens',
                'content': [{'type': 'text', 'text': '{}'}]})
        with self.assertRaises(host.HostError):
            host.parse_response(host.PROVIDER_OPENAI, {
                'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
        with self.assertRaises(host.HostError):
            host.adapt_request('mystery', {'model': 'x', 'messages': []})

    def test_probe_anthropic_uses_x_api_key_and_hides_value(self):
        captured = []

        def fake_http(url, *, payload=None, headers=None, timeout=15):
            captured.append({'url': url, 'headers': dict(headers or {}), 'payload': payload})
            if url.endswith('/health'):
                return {'status': 'ok'}, None
            return {'data': [{'id': 'claude-probe'}]}, None

        sentinel = 'test-sentinel-not-a-real-key'
        with unittest.mock.patch.dict(os.environ, {'HOST_TEST_ANTHROPIC_KEY': sentinel}), \
                unittest.mock.patch.object(host, 'http_json', side_effect=fake_http):
            probe = host.probe_endpoint('http://127.0.0.1:45691', 'claude-probe', 1,
                                        'HOST_TEST_ANTHROPIC_KEY',
                                        provider=host.PROVIDER_ANTHROPIC)
        self.assertEqual([item['url'] for item in captured],
                         ['http://127.0.0.1:45691/health',
                          'http://127.0.0.1:45691/v1/models'])
        self.assertEqual(captured[0]['headers']['x-api-key'], sentinel)
        self.assertNotIn('Authorization', captured[0]['headers'])
        self.assertEqual(captured[1]['headers']['anthropic-version'],
                         host.ANTHROPIC_VERSION_DEFAULT)
        self.assertTrue(probe['model_advertised'])
        self.assertTrue(probe['credential_present'])
        self.assertNotIn(sentinel, json.dumps(probe))
        self.assertEqual(probe['request_path'], '/v1/messages')

    def test_probe_anthropic_gap_classification_without_model_calls(self):
        caps = host.load_capabilities(self.caps('anthropic.json', {
            'provider': 'anthropic', 'model': 'claude-probe',
            'input_modalities': ['text', 'image'], 'max_images_per_request': 5}))
        with unittest.mock.patch.object(host, 'http_json',
                                        return_value=({'status': 'ok'}, None)):
            healthy = host.probe_endpoint('http://127.0.0.1:45691', 'claude-probe', 1,
                                          provider=host.PROVIDER_ANTHROPIC)
        self.assertIn('missing_api_key_env_for_anthropic',
                      host.live_gaps(caps, healthy, 5, 'claude-probe'))
        with unittest.mock.patch.object(host, 'http_json', return_value=(None, 'HTTP 401')):
            rejected = host.probe_endpoint('http://127.0.0.1:45691', 'claude-probe', 1,
                                           provider=host.PROVIDER_ANTHROPIC)
        self.assertTrue(rejected['auth_rejected'])
        gaps = host.live_gaps(caps, rejected, 5, 'claude-probe')
        self.assertIn('no_reachable_model_endpoint', gaps)
        self.assertIn('endpoint_auth_rejected', gaps)
