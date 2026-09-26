"""Hermetic tests for the read-only PB GUI coverage audit (no device, no network)."""
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
import gui_machine_qa as machine_qa
import pb_gui_bridge as bridge
import pb_gui_capture_gate as capture_gate
import pb_gui_coverage_audit as audit

PB_ROOT = (ROOT.parent / '相关文件' / 'ProactiveBench' / 'ProactiveBench')
REAL_RELEASE_REPORT = Path('/tmp/pb-gui-release-report-ep0004-v3.json')
REAL_HOST_RECEIPT = ROOT / 'runtime' / 'pb-gui-image-host-ep0004' / 'host-receipt.json'
OBSERVED = '2026-09-14T08:30:26+08:00'
DECISION = '2026-09-14T08:35:01+08:00'
PRIVATE_SENTINEL = 'PRIVATE-GT-SENTINEL-MUST-NOT-BE-READ'
ZERO_CHECKS = ('pb_writes', 'gt_reads', 'images_copied', 'images_rewritten', 'device_writes',
               'scoring_invoked', 'publish_invoked', 'missing_evidence_marked_ready')


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return path


def device_bytes(packet_id):
    return f'device-frame:{packet_id}'.encode('utf8')


EPISODES = {
    ('demo-001', 'ep_0001'): {'passive': 'p001', 'world': 'w001',
                              'gui': [('p003', 'calendar', True), ('p009', 'mail', True)]},
    ('demo-001', 'ep_0002'): {'passive': 'p002', 'world': 'w001',
                              'gui': [('p004', 'contacts', True), ('p010', 'mail', False)]},
    ('demo-001', 'ep_0003'): {'passive': 'p003', 'world': 'w001', 'gui': []},
    ('demo-002', 'ep_0001'): {'passive': 'p001', 'world': 'w001',
                              'gui': [('p020', 'clock', True)]},
}


def build_pb(root):
    """Synthetic PB tree that mirrors the registered benchmark layout."""
    pb_root = root / 'pb'
    write_json(pb_root / 'benchmark.json', {
        'layout_version': 'test.layout.v1',
        'clusters': {'demo-001': {'path': 'single-scenarios/demo-001'},
                     'demo-002': {'path': 'single-scenarios/demo-002'}}})
    fixture = {'pb_root': pb_root, 'episodes': {}}
    sequences = {}
    for (cluster_id, episode_id), spec in EPISODES.items():
        cluster_dir = pb_root / 'single-scenarios' / cluster_id
        episode_dir = cluster_dir / 'episodes' / episode_id
        passive = spec['passive']
        manifest = {
            'schema_version': 'test.cluster.v1', 'episode_id': episode_id,
            'world_variant_id': spec['world'], 'passive_id': passive,
            'passive_ref': f'passive_inputs/{passive}/stream.jsonl',
            'track': 'mixed_text', 'decision_point': DECISION, 'timezone': 'Asia/Shanghai',
            'history_ref': f'active_world/history/{spec["world"]}.json',
            'initialization_ref': f'active_world/initialization/{spec["world"]}.json',
            'patch_ref': f'active_world/variants/{spec["world"]}.patch.json',
            'binding_ref': f'active_world/access/{spec["world"]}/bindings.json',
            'access_ref': f'active_world/access/{spec["world"]}/evidence_tree.json',
            'gt_ref': f'episodes/{episode_id}/private_gt.json',
        }
        write_json(episode_dir / 'manifest.json', manifest)
        # Deliberately invalid JSON: any accidental GT read/parse would fail the audit.
        (episode_dir / 'private_gt.json').write_text(f'{PRIVATE_SENTINEL} not-json',
                                                     encoding='utf8')
        write_json(cluster_dir / 'active_world' / 'history' / f'{spec["world"]}.json', {})
        write_json(cluster_dir / 'active_world' / 'initialization' / f'{spec["world"]}.json', {})
        write_json(cluster_dir / 'active_world' / 'variants' / f'{spec["world"]}.patch.json', {})
        write_json(cluster_dir / 'active_world' / 'access' / spec['world'] / 'bindings.json', {})
        write_json(cluster_dir / 'active_world' / 'access' / spec['world'] / 'evidence_tree.json', {})
        stream_path = cluster_dir / 'passive_inputs' / passive / 'stream.jsonl'
        packets, packets_by_id = [], {}
        sequence = sequences.get(cluster_id, 0)
        for packet_id, source, declared in spec['gui']:
            sequence += 1
            packet = {'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
                      'observed_at': OBSERVED, 'delivered_at': OBSERVED,
                      'text_event': f'{source} event'}
            if declared:
                raw = f'{packet_id}-pb-original'.encode('utf8')
                image_path = stream_path.parent / 'gui' / f'{packet_id}.png'
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(raw)
                packet['screenshot_ref'] = f'passive_inputs/{passive}/gui/{packet_id}.png'
                packet['screenshot_sha256'] = sha256_bytes(raw)
            packets.append(packet)
            packets_by_id[packet_id] = {'source': source, 'declared': declared,
                                        'declared_sha256': packet.get('screenshot_sha256')}
        for packet_id, tag in (('n001', 'app_event'), ('n002', 'time_event')):
            sequence += 1
            packets.append({'packet_id': packet_id, 'sequence': sequence, 'tag': tag,
                            'source': 'device', 'observed_at': OBSERVED,
                            'delivered_at': OBSERVED, 'text_event': f'{tag} event'})
        sequences[cluster_id] = sequence
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_path.write_text(''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in packets),
                               encoding='utf8')
        fixture['episodes'][(cluster_id, episode_id)] = {
            'cluster_dir': cluster_dir, 'manifest': manifest, 'stream_path': stream_path,
            'packets': packets_by_id,
            'stream_sha256': sha256_bytes(stream_path.read_bytes()),
            'manifest_sha256': sha256_bytes((episode_dir / 'manifest.json').read_bytes()),
        }
    return fixture


def scope_for(spec, manifest):
    return {'cluster_id': spec[0], 'episode_id': spec[1], 'passive_id': manifest['passive_id'],
            'world_variant_id': manifest['world_variant_id'], 'track': manifest['track'],
            'decision_point': manifest['decision_point'], 'timezone': manifest['timezone']}


def build_release_report(fixture, spec, frames, path, **overrides):
    episode = fixture['episodes'][spec]
    frame_docs = [{'packet_id': packet_id, 'frame_id': f'{packet_id}_f1',
                   'png': f'images/{packet_id}.png',
                   'sha256': sha256_bytes(device_bytes(packet_id)),
                   'width': 1080, 'height': 2400, 'status': 'pass'} for packet_id in frames]
    by_source = {}
    for packet_id in frames:
        source = episode['packets'][packet_id]['source']
        by_source[source] = by_source.get(source, 0) + 1
    rule, model = sorted(machine_qa.KNOWN_RULES)[0], machine_qa.MODEL_SHA256
    report = {
        'schema_version': 'pb-gui-release-report.v1', 'status': 'release_ready',
        'command': 'preflight', 'generated_at': OBSERVED, 'release_ready': True,
        'episode_scope': scope_for(spec, episode['manifest']),
        'bindings': {
            'gate_plan': {'schema_version': 'pb-gui-capture-gate.v1', 'sha256': 'a' * 64,
                          'status': 'ready_for_device_capture', 'targets': len(frames)},
            'backend_receipt': {'schema_version': 'pb-backend-apply-receipt.v1', 'sha256': 'b' * 64,
                                'status': 'ok', 'device_applied': True, 'applied_at': OBSERVED},
            'private_plan': {'schema_version': 'pb-gui-private-capture-plan.v1', 'sha256': 'c' * 64},
            'capture_receipt': {'schema_version': '1.0', 'sha256': 'd' * 64,
                                'status': 'capture_complete', 'reset_verified': True,
                                'capture_run_id': 'run-1', 'frames': len(frames)},
            'machine_qa': {'schema_version': '1.0', 'status': 'pass', 'rule_version': rule,
                           'ocr_model_sha256': model, 'frames': len(frames),
                           'stored_reports': ['qa-machine.json']},
        },
        'machine_qa': {'rule_version': rule, 'ocr_model_sha256': model,
                       'ocr_models': {'eng': model}},
        'counts': {'targets': len(frames), 'gate_targets': len(frames), 'unsupported_targets': 0,
                   'frames': len(frames), 'pass_frames': len(frames),
                   'declared_screenshot_targets': len(frames), 'derived_screenshot_targets': 0,
                   'by_source': by_source},
        'frames': frame_docs,
        'checks': {'private_fields_emitted': 0, 'absolute_paths_emitted': 0, 'images_copied': 0,
                   'images_rewritten': 0, 'device_writes': 0, 'gold_generated': 0,
                   'publish_invoked': 0},
    }
    report.update(overrides)
    return write_json(path, report)


def build_host_receipt(fixture, spec, frames, release_sha, path, **overrides):
    episode = fixture['episodes'][spec]
    frame_docs = []
    for packet_id in frames:
        record = episode['packets'][packet_id]
        frame_docs.append({'packet_id': packet_id, 'frame_id': f'{packet_id}_f1',
                           'source': record['source'],
                           'device_sha256': sha256_bytes(device_bytes(packet_id)), 'bytes': 100,
                           'width': 1080, 'height': 2400,
                           'pb_declared_sha256': record['declared_sha256'],
                           'staged_ref': f'passive_inputs/{episode["manifest"]["passive_id"]}'
                                         f'/gui/{packet_id}.png'})
    receipt = {
        'schema_version': 'pb-gui-image-host-receipt.v1', 'status': 'bound', 'command': 'preflight',
        'generated_at': OBSERVED, 'episode_scope': scope_for(spec, episode['manifest']),
        'bindings': {
            'release_report': {'schema_version': 'pb-gui-release-report.v1', 'sha256': release_sha,
                               'status': 'release_ready', 'frames': len(frames)},
            'pb': {'cluster_json_sha256': 'e' * 64,
                   'manifest_sha256': episode['manifest_sha256'],
                   'stream_sha256': episode['stream_sha256'], 'entry': {}},
        },
        'frames': frame_docs,
    }
    receipt.update(overrides)
    return write_json(path, receipt)


def by_key(report, cluster_id, episode_id):
    for episode in report['episodes']:
        if episode['cluster_id'] == cluster_id and episode['episode_id'] == episode_id:
            return episode
    raise AssertionError(f'episode not in report: {cluster_id}/{episode_id}')


def tree_hashes(root):
    return {str(path.relative_to(root)): sha256_bytes(path.read_bytes())
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


class CoverageAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = build_pb(self.root)
        self.out = self.root / 'audit.json'

    def run_audit(self, *extra):
        if self.out.exists():
            self.out.unlink()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = audit.main(['--pb-root', str(self.fixture['pb_root']),
                               '--output', str(self.out), *extra])
        report = json.loads(self.out.read_text(encoding='utf8')) if self.out.is_file() else None
        return code, report, stdout.getvalue(), stderr.getvalue()

    def test_enumerates_bindings_and_classifies_without_receipts(self):
        before = tree_hashes(self.fixture['pb_root'])
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 0)
        self.assertEqual(before, tree_hashes(self.fixture['pb_root']))
        self.assertEqual(report['schema_version'], 'pb-gui-coverage-audit.v1')
        self.assertEqual(report['pb']['episodes'], 4)
        self.assertEqual(report['pb']['clusters'], 2)
        self.assertEqual(report['receipts'], {'release_report': None, 'host_receipt': None})
        counts = report['counts']
        self.assertEqual(
            {field: counts[field] for field in ('episodes', 'gui_packets', 'supported_gui_packets',
                                                'unsupported_gui_packets', 'declared_screenshots',
                                                'pending_screenshots',
                                                'evidence_bound_gui_packets',
                                                'missing_evidence_gui_packets')},
            {'episodes': 4, 'gui_packets': 5, 'supported_gui_packets': 5,
             'unsupported_gui_packets': 0, 'declared_screenshots': 4, 'pending_screenshots': 1,
             'evidence_bound_gui_packets': 0, 'missing_evidence_gui_packets': 5})
        self.assertEqual(counts['by_status'], {'covered': 0, 'partial_evidence': 0,
                                               'missing_evidence': 3, 'blocked_unsupported': 0,
                                               'no_gui_packets': 1, 'invalid': 0})
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['covered_episodes'], [])
        self.assertEqual(len(report['gaps']), 3)
        fine = by_key(report, 'demo-001', 'ep_0001')
        self.assertEqual((fine['status'], fine['gui_packets'], fine['supported_gui_packets'],
                          fine['unsupported_gui_packets'], fine['evidence_bound_gui_packets'],
                          fine['missing_evidence_gui_packets'],
                          fine['declared_screenshots'], fine['pending_screenshots']),
                         ('missing_evidence', 2, 2, 0, 0, 2, 2, 0))
        self.assertEqual(fine['sources']['calendar'],
                         {'gui_packets': 1, 'adapter_supported': True, 'evidence_bound': 0,
                          'missing_evidence': 1})
        self.assertTrue(all(packet['status'] == 'missing_evidence' for packet in fine['packets']))
        mixed = by_key(report, 'demo-001', 'ep_0002')
        self.assertEqual(mixed['status'], 'missing_evidence')
        self.assertEqual(mixed['sources']['contacts']['adapter_supported'], True)
        self.assertEqual(mixed['sources']['mail']['adapter_supported'], True)
        self.assertEqual(mixed['pending_screenshots'], 1)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0003')['status'], 'no_gui_packets')
        self.assertEqual(by_key(report, 'demo-002', 'ep_0001')['status'], 'missing_evidence')
        for field in ZERO_CHECKS:
            self.assertEqual(report['checks'][field], 0, field)
        capture_gate.assert_private_free(report, 'audit report')
        capture_gate.assert_no_absolute_paths(report, 'audit report')
        text = self.out.read_text(encoding='utf8')
        self.assertNotIn(PRIVATE_SENTINEL, text)
        self.assertNotIn('"ready"', text)

    def test_release_report_binds_only_its_own_episode(self):
        report_path = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                           ['p003', 'p009'], self.root / 'release-v3.json')
        code, report, _, _ = self.run_audit('--release-report', str(report_path))
        self.assertEqual(code, 0)
        episode = by_key(report, 'demo-001', 'ep_0001')
        self.assertEqual((episode['status'], episode['evidence_bound_gui_packets'],
                          episode['missing_evidence_gui_packets']), ('covered', 2, 0))
        self.assertEqual(episode['evidence_sources'],
                         {'release_report': 2, 'host_receipt': 0})
        self.assertEqual(report['receipts']['release_report']['packets'], ['p003', 'p009'])
        self.assertEqual(report['receipts']['release_report']['frames'], 2)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0002')['status'], 'missing_evidence')
        self.assertEqual(by_key(report, 'demo-002', 'ep_0001')['status'], 'missing_evidence')
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['counts']['evidence_bound_gui_packets'], 2)

    def test_host_receipt_binds_current_pb_bytes(self):
        release_path = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                            ['p003', 'p009'], self.root / 'release.json')
        host_path = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                                       sha256_bytes(release_path.read_bytes()),
                                       self.root / 'host.json')
        code, report, _, _ = self.run_audit('--host-receipt', str(host_path))
        self.assertEqual(code, 0)
        episode = by_key(report, 'demo-001', 'ep_0001')
        self.assertEqual(episode['status'], 'covered')
        self.assertEqual(episode['evidence_sources'], {'release_report': 0, 'host_receipt': 2})
        self.assertEqual(report['receipts']['host_receipt']['frames'], 2)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0002')['status'], 'missing_evidence')

    def test_both_receipts_must_cross_bind(self):
        release_path = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                            ['p003', 'p009'], self.root / 'release.json')
        host_path = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                                       sha256_bytes(release_path.read_bytes()),
                                       self.root / 'host.json')
        code, report, _, _ = self.run_audit('--release-report', str(release_path),
                                            '--host-receipt', str(host_path))
        self.assertEqual(code, 0)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0001')['status'], 'covered')
        stale = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                   'f' * 64, self.root / 'host-stale.json')
        code, report, _, stderr = self.run_audit('--release-report', str(release_path),
                                                 '--host-receipt', str(stale))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('not bound to the provided release report', stderr)
        conflicting_report = json.loads(release_path.read_text(encoding='utf8'))
        conflicting_report['frames'] = [dict(frame, sha256='9' * 64)
                                        for frame in conflicting_report['frames']]
        conflict_path = write_json(self.root / 'release-conflict.json', conflicting_report)
        conflicting = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                         sha256_bytes(conflict_path.read_bytes()),
                                         self.root / 'host-conflict.json')
        code, report, _, stderr = self.run_audit(
            '--release-report', str(conflict_path), '--host-receipt', str(conflicting))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('disagree on packet', stderr)

    def test_invalid_receipts_fail_closed(self):
        release_path = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                            ['p003', 'p009'], self.root / 'release.json')
        cases = []
        unknown = build_release_report(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                       self.root / 'release-unknown.json')
        unknown_report = json.loads(unknown.read_text(encoding='utf8'))
        unknown_report['frames'] = [dict(unknown_report['frames'][0], packet_id='p999',
                                         png='images/p999.png')]
        cases.append(('release unknown packet', '--release-report',
                      write_json(self.root / 'release-unknown.json', unknown_report)))
        bad = json.loads(release_path.read_text(encoding='utf8'))
        bad['episode_scope'] = dict(bad['episode_scope'], world_variant_id='w999')
        cases.append(('release scope drift', '--release-report',
                      write_json(self.root / 'release-scope.json', bad)))
        private = json.loads(release_path.read_text(encoding='utf8'))
        private['need'] = PRIVATE_SENTINEL
        cases.append(('release private field', '--release-report',
                      write_json(self.root / 'release-private.json', private)))
        cases.append(('host declared mismatch', '--host-receipt',
                      build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                         sha256_bytes(release_path.read_bytes()),
                                         self.root / 'host-declared.json')))
        declared_host = json.loads((self.root / 'host-declared.json').read_text(encoding='utf8'))
        declared_host['frames'][0]['pb_declared_sha256'] = '0' * 64
        cases[-1] = (cases[-1][0], cases[-1][1],
                     write_json(self.root / 'host-declared.json', declared_host))
        cases.append(('host stream drift', '--host-receipt',
                      build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                         sha256_bytes(release_path.read_bytes()),
                                         self.root / 'host-stream.json', bindings={
                                             'pb': {'cluster_json_sha256': 'e' * 64,
                                                    'manifest_sha256': self.fixture['episodes'][
                                                        ('demo-001', 'ep_0001')]['manifest_sha256'],
                                                    'stream_sha256': '0' * 64, 'entry': {}}})))
        republished = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'],
                                         sha256_bytes(release_path.read_bytes()),
                                         self.root / 'host-original.json')
        republished_report = json.loads(republished.read_text(encoding='utf8'))
        republished_report['frames'][0]['device_sha256'] = self.fixture['episodes'][
            ('demo-001', 'ep_0001')]['packets']['p003']['declared_sha256']
        cases.append(('host republished original', '--host-receipt',
                      write_json(self.root / 'host-original.json', republished_report)))
        for name, flag, path in cases:
            with self.subTest(name=name):
                code, report, _, stderr = self.run_audit(flag, str(path))
                self.assertEqual(code, 2, name)
                self.assertIsNone(report, name)
                self.assertIn('error:', stderr, name)

    def test_evidence_dir_aggregates_multiple_episode_pairs(self):
        evidence = self.root / 'evidence'
        release_a = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                         ['p003', 'p009'],
                                         evidence / 'ep_0001-release-report.json')
        build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                           sha256_bytes(release_a.read_bytes()),
                           evidence / 'ep_0001-host-receipt.json')
        bundle = evidence / 'demo-002-ep_0001'
        release_b = build_release_report(self.fixture, ('demo-002', 'ep_0001'), ['p020'],
                                         bundle / 'release-report.json')
        build_host_receipt(self.fixture, ('demo-002', 'ep_0001'), ['p020'],
                           sha256_bytes(release_b.read_bytes()), bundle / 'host-receipt.json')
        write_json(bundle / 'replay-receipt.json',
                   {'schema_version': 'pb-gui-image-replay-receipt.v1'})
        code, report, _, stderr = self.run_audit('--evidence-dir', str(evidence))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0001')['status'], 'covered')
        self.assertEqual(by_key(report, 'demo-002', 'ep_0001')['status'], 'covered')
        self.assertEqual(report['counts']['evidence_bound_gui_packets'], 3)
        self.assertEqual(report['counts']['by_status']['covered'], 2)
        self.assertEqual(len(report['receipts']['release_reports']), 2)
        self.assertEqual(len(report['receipts']['host_receipts']), 2)
        self.assertIsNone(report['receipts']['release_report'])
        self.assertIsNone(report['receipts']['host_receipt'])
        self.assertEqual(report['receipts']['evidence_dir'],
                         {'basename': 'evidence', 'release_reports': 2, 'host_receipts': 2,
                          'rejected_candidates': 1,
                          'rejected_sample': ['demo-002-ep_0001/replay-receipt.json']})
        self.assertFalse(report['coverage_complete'])
        capture_gate.assert_private_free(report, 'audit report')
        capture_gate.assert_no_absolute_paths(report, 'audit report')

    def test_single_file_inputs_keep_legacy_receipt_summaries(self):
        release = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                       ['p003', 'p009'],
                                       self.root / 'legacy-release-report.json')
        host = build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                                  sha256_bytes(release.read_bytes()),
                                  self.root / 'legacy-host-receipt.json')
        code, report, _, _ = self.run_audit('--release-report', str(release))
        self.assertEqual(code, 0)
        self.assertEqual(set(report['receipts']), {'release_report', 'host_receipt'})
        self.assertEqual(report['receipts']['host_receipt'], None)
        self.assertEqual(report['receipts']['release_report']['frames'], 2)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0001')['status'], 'covered')
        code, report, _, _ = self.run_audit('--release-report', str(release),
                                            '--host-receipt', str(host))
        self.assertEqual(code, 0)
        self.assertEqual(set(report['receipts']), {'release_report', 'host_receipt'})
        self.assertEqual(report['receipts']['release_report']['sha256'],
                         sha256_bytes(release.read_bytes()))
        self.assertEqual(report['receipts']['host_receipt']['frames'], 2)
        self.assertEqual(by_key(report, 'demo-001', 'ep_0001')['status'], 'covered')
        other_host = build_host_receipt(self.fixture, ('demo-002', 'ep_0001'), ['p020'],
                                        sha256_bytes(release.read_bytes()),
                                        self.root / 'legacy-other-host-receipt.json')
        code, report, _, stderr = self.run_audit('--release-report', str(release),
                                                 '--host-receipt', str(other_host))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('scope different episodes', stderr)

    def test_evidence_dir_rejects_duplicate_and_unknown_receipts(self):
        evidence = self.root / 'duplicate'
        release = build_release_report(self.fixture, ('demo-001', 'ep_0001'),
                                       ['p003', 'p009'],
                                       evidence / 'ep_0001-release-report.json')
        build_release_report(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                             evidence / 'ep_0001-release-report-copy.json')
        build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                           sha256_bytes(release.read_bytes()),
                           evidence / 'ep_0001-host-receipt.json')
        code, report, _, stderr = self.run_audit('--evidence-dir', str(evidence))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('binds demo-001/ep_0001 twice', stderr)
        code, report, _, stderr = self.run_audit('--release-report', str(release),
                                                 '--evidence-dir', str(evidence))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('twice', stderr)
        unknown = self.root / 'unknown'
        write_json(unknown / 'notes.json', {'schema_version': 'not-a-receipt.v1'})
        code, report, _, stderr = self.run_audit('--evidence-dir', str(unknown))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('not named as a release report or host receipt', stderr)
        foreign = self.root / 'foreign'
        write_json(foreign / 'host-receipt.json', {'schema_version': 'not-a-receipt.v1'})
        code, report, _, stderr = self.run_audit('--evidence-dir', str(foreign))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('host receipt failed validation', stderr)

    def test_evidence_dir_requires_paired_receipts(self):
        release_only = self.root / 'release-only'
        build_release_report(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                             release_only / 'release-report.json')
        code, report, _, stderr = self.run_audit('--evidence-dir', str(release_only))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('no paired host receipt', stderr)
        host_only = self.root / 'host-only'
        build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003'], 'a' * 64,
                           host_only / 'host-receipt.json')
        code, report, _, stderr = self.run_audit('--evidence-dir', str(host_only))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('no paired release report', stderr)
        stale = self.root / 'stale'
        build_release_report(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'],
                             stale / 'release-report.json')
        build_host_receipt(self.fixture, ('demo-001', 'ep_0001'), ['p003', 'p009'], 'f' * 64,
                           stale / 'host-receipt.json')
        code, report, _, stderr = self.run_audit('--evidence-dir', str(stale))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('not bound to the provided release report', stderr)
        empty = self.root / 'empty'
        empty.mkdir()
        (empty / 'notes.txt').write_text('no receipts here', encoding='utf8')
        code, report, _, stderr = self.run_audit('--evidence-dir', str(empty))
        self.assertEqual(code, 2)
        self.assertIsNone(report)
        self.assertIn('no named release report or host receipt', stderr)

    def test_output_must_stay_outside_pb_root_and_cluster_filter_is_scoped(self):
        inside = self.fixture['pb_root'] / 'audit.json'
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = audit.main(['--pb-root', str(self.fixture['pb_root']),
                               '--output', str(inside)])
        self.assertEqual(code, 2)
        self.assertFalse(inside.exists())
        self.assertIn('must stay outside --pb-root', stderr.getvalue())
        code, report, _, _ = self.run_audit('--cluster', 'demo-002')
        self.assertEqual(code, 0)
        self.assertEqual(report['counts']['episodes'], 1)
        self.assertEqual(report['counts']['gui_packets'], 1)
        self.assertEqual(by_key(report, 'demo-002', 'ep_0001')['status'], 'missing_evidence')
        code, report, _, stderr = self.run_audit('--cluster', 'missing-cluster')
        self.assertEqual(code, 2)
        self.assertIsNone(report)


@unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(), 'ProactiveBench tree not present')
class RealPBCoverageSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = PB_ROOT.resolve()
        cls.benchmark, cls.clusters = bridge.load_benchmark(cls.root)

    def build(self, receipts):
        return audit.build_report(self.root, self.benchmark, self.clusters, None, receipts)

    def test_registered_17_episodes_are_enumerated_with_gaps(self):
        report, invalid = self.build({'release_report': None, 'host_receipt': None})
        self.assertEqual(invalid, 0)
        self.assertEqual(report['counts']['episodes'], 17)
        self.assertEqual(report['counts']['clusters'], 5)
        self.assertEqual(report['counts']['gui_packets'], 38)
        self.assertEqual(report['counts']['supported_gui_packets'], 23)
        self.assertEqual(report['counts']['unsupported_gui_packets'], 15)
        self.assertEqual(report['counts']['evidence_bound_gui_packets'], 0)
        self.assertEqual(report['counts']['missing_evidence_gui_packets'], 38)
        self.assertFalse(report['coverage_complete'])
        ep0004 = by_key(report, 'meeting-preparation-001', 'ep_0004')
        self.assertEqual((ep0004['status'], ep0004['gui_packets'],
                          ep0004['supported_gui_packets'], ep0004['evidence_bound_gui_packets']),
                         ('missing_evidence', 7, 7, 0))
        ep0005 = by_key(report, 'meeting-preparation-001', 'ep_0005')
        self.assertEqual((ep0005['status'], ep0005['gui_packets'],
                          ep0005['evidence_bound_gui_packets']), ('missing_evidence', 7, 0))
        self.assertEqual(by_key(report, 'photo-resolution-preparation-001',
                                'ep_photo_001')['status'], 'blocked_unsupported')

    @unittest.skipUnless(REAL_RELEASE_REPORT.is_file() and REAL_HOST_RECEIPT.is_file(),
                         'real ep_0004 release/host receipts not present')
    def test_real_ep0004_evidence_is_split_from_other_gaps(self):
        receipts = {'release_report': audit.load_release_evidence(REAL_RELEASE_REPORT),
                    'host_receipt': audit.load_host_evidence(REAL_HOST_RECEIPT)}
        report, invalid = self.build(receipts)
        self.assertEqual(invalid, 0)
        ep0004 = by_key(report, 'meeting-preparation-001', 'ep_0004')
        self.assertEqual((ep0004['status'], ep0004['gui_packets'],
                          ep0004['evidence_bound_gui_packets'],
                          ep0004['missing_evidence_gui_packets']), ('covered', 7, 7, 0))
        self.assertEqual(ep0004['evidence_sources'], {'release_report': 7, 'host_receipt': 7})
        self.assertEqual(report['counts']['evidence_bound_gui_packets'], 7)
        self.assertEqual(report['counts']['missing_evidence_gui_packets'], 31)
        self.assertEqual(report['counts']['by_status']['covered'], 1)
        for episode in report['episodes']:
            if (episode['cluster_id'], episode['episode_id']) != ('meeting-preparation-001',
                                                                  'ep_0004'):
                self.assertNotEqual(episode['status'], 'covered')
        self.assertFalse(report['coverage_complete'])


if __name__ == '__main__':
    unittest.main()
