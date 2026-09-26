import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gui_scenario_audit import audit

DATASET = 'gui-x-formal-pilot'


def candidate(app, view, *, status='missing', source=None, assertions, surface='surface-x', gap='gap-x'):
    return {'app_id': app, 'surface': surface, 'status': status, 'pilot_view': view,
            'packet_source': source or app, 'required_assertions': assertions, 'gap': gap}


def packet(packet_id, *, source, view=None, app=None, assertions=None, packet_type='gui'):
    item = {'packet_id': packet_id, 'sequence': 1, 'type': packet_type, 'source': source,
            'observed_at': '2026-09-26T10:00:00+08:00', 'text_projection': 'projection'}
    if view is not None:
        item['capture'] = {'surface': 'app_screen', 'app_id': app or source, 'view_id': view,
                           'subject_ref': 'ref-01', 'frame_id': f'{packet_id}_f1',
                           'visible_assertions': assertions}
    return item


def write_json(path, payload):
    path.write_text(json.dumps(payload))
    return path


def dataset(root, name, cluster_id, packets):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'cluster-base.json', {'cluster_id': cluster_id})
    write_json(directory / 'passive.json', {'passive_id': f'{name}-passive', 'packets': packets})
    return directory


class ScenarioAuditTest(unittest.TestCase):
    def run_audit(self, clusters, packets, apps, *, dataset_name=DATASET, dataset_cluster_id='dataset-x'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        datasets_root = root / 'datasets'
        datasets_root.mkdir()
        dataset(datasets_root, dataset_name, dataset_cluster_id, packets)
        capabilities = write_json(root / 'capabilities.json',
                                  {'schema_version': '1.0', 'core_apps': apps, 'extension_apps': {}})
        contract = write_json(root / 'audit.json', {'schema_version': '1.0', 'clusters': clusters})
        return audit(contract, datasets_root, capabilities)

    def single_packet(self, report):
        packets = report['clusters'][0]['packets']
        self.assertEqual(len(packets), 1)
        return packets[0]

    def test_real_formal_pilot_packets_are_missing_and_not_gold(self):
        report = audit(ROOT / 'contracts/gui-scenario-audit.json', ROOT / 'datasets')
        self.assertEqual(report['totals'], {'supported': 0, 'missing': 4, 'text_only': 0})
        self.assertFalse(report['gui_gold'])
        self.assertEqual(report['device_evidence'], 'not_used')
        self.assertTrue(all(not cluster['gui_gold'] for cluster in report['clusters']))
        expected = {
            ('appointment_sync', 'gui-appointment-formal-pilot', 'p001'): ('candidate_status_missing', 'mail'),
            ('file_availability', 'gui-files-formal-pilot', 'p001'): ('candidate_status_missing', 'files'),
            ('contact_update', 'gui-contact-update-formal-pilot', 'p001'): ('candidate_status_pilot', 'messages'),
            ('reminder_coverage', 'gui-reminder-formal-pilot', 'p001'): ('candidate_status_missing', 'mail'),
        }
        seen = set()
        for cluster in report['clusters']:
            for item in cluster['packets']:
                key = (cluster['cluster_id'], item['dataset'], item['packet_id'])
                reason_code, app_id = expected[key]
                seen.add(key)
                self.assertEqual(item['conclusion'], 'missing')
                self.assertEqual(item['reason_code'], reason_code)
                self.assertEqual(item['matched_candidate']['app_id'], app_id)
                self.assertNotEqual(item['matched_candidate']['status'], 'supported')
        self.assertEqual(seen, set(expected))

    def test_real_contract_binds_every_first_batch_dataset_without_promotion(self):
        contract = json.loads((ROOT / 'contracts/gui-scenario-audit.json').read_text())
        clusters = {cluster['cluster_id']: cluster for cluster in contract['clusters']}
        declared = {}
        for cluster_id, cluster in clusters.items():
            for mapping in cluster['formal_pilot_datasets']:
                declared[mapping['dataset']] = mapping['cluster_id']
                base = json.loads((ROOT / 'datasets' / mapping['dataset'] / 'cluster-base.json').read_text())
                self.assertEqual(base['cluster_id'], mapping['cluster_id'])
            for item in cluster['candidates']:
                self.assertIn(item['status'], ('supported', 'pilot', 'missing'))
                self.assertNotEqual(item['status'], 'supported')
                self.assertEqual(item['packet_source'], item['app_id'])
                self.assertTrue(item['pilot_view'])
                self.assertTrue(item['required_assertions'])
        self.assertEqual(declared, {
            'gui-appointment-formal-pilot': 'appointment-sync-mail-calendar-pilot',
            'gui-files-formal-pilot': 'file-availability-download-pilot',
            'gui-contact-update-formal-pilot': 'contact-update-messages-contacts-pilot',
            'gui-reminder-formal-pilot': 'reminder-coverage-mail-clock-pilot',
        })
        self.assertEqual([mapping['dataset'] for mapping in clusters['reminder_coverage']['formal_pilot_datasets']],
                         ['gui-reminder-formal-pilot'])
        statuses = {(cluster_id, item['app_id'], item['pilot_view']): item['status']
                    for cluster_id, cluster in clusters.items() for item in cluster['candidates']}
        self.assertEqual(statuses['contact_update', 'messages', 'conversation_list'], 'pilot')
        self.assertEqual([value for value in statuses.values() if value == 'pilot'], ['pilot'])
        self.assertEqual(sum(value == 'missing' for value in statuses.values()), 10)

    def test_pilot_candidate_is_never_promoted_to_supported(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject', 'from', 'date', 'body']}}}
        candidates = [candidate('mail', 'mail_detail', status='pilot',
                                assertions=['subject', 'from', 'date', 'body'])]
        packets = [packet('p001', source='mail', view='mail_detail',
                          assertions=['subject', 'from', 'date', 'body'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        report = self.run_audit([cluster], packets, apps)
        item = self.single_packet(report)
        self.assertEqual(item['conclusion'], 'missing')
        self.assertEqual(item['reason_code'], 'candidate_status_pilot')
        self.assertFalse(report['gui_gold'])

    def test_supported_requires_candidate_and_capability_support(self):
        apps = {'mail': {'status': 'supported', 'views': {'mail_detail': ['subject']}}}
        candidates = [candidate('mail', 'mail_detail', status='supported', assertions=['subject'])]
        packets = [packet('p001', source='mail', view='mail_detail', assertions=['subject'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        report = self.run_audit([cluster], packets, apps)
        item = self.single_packet(report)
        self.assertEqual(item['conclusion'], 'supported')
        self.assertEqual(item['reason_code'], 'contract_supported')

        candidates[0]['required_assertions'] = ['subject', 'from']
        apps['mail']['views']['mail_detail'].append('from')
        incomplete = self.single_packet(self.run_audit([cluster], [packet('p002', source='mail',
                                            view='mail_detail', assertions=['subject'])], apps))
        self.assertEqual(incomplete['reason_code'], 'required_assertions_missing')
        packets[0]['capture']['surface'] = 'system_surface'
        wrong_surface = self.single_packet(self.run_audit([cluster], packets, apps))
        self.assertEqual(wrong_surface['reason_code'], 'unsupported_capture_surface')
        self.assertFalse(report['clusters'][0]['gui_gold'])
        promoted = json.loads((ROOT / 'contracts/gui-scenario-audit.json').read_text())
        self.assertNotIn('supported', [entry['status'] for cluster in promoted['clusters']
                                       for entry in cluster['candidates']])

    def test_capability_declaration_alone_is_not_supported(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject']}}}
        candidates = [candidate('mail', 'mail_detail', status='supported', assertions=['subject'])]
        packets = [packet('p001', source='mail', view='mail_detail', assertions=['subject'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        item = self.single_packet(self.run_audit([cluster], packets, apps))
        self.assertEqual(item['conclusion'], 'missing')
        self.assertEqual(item['reason_code'], 'capability_status_not_supported')

    def test_unmapped_and_undeclared_surfaces_fail_closed(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject']}},
                'calendar': {'status': 'pilot', 'views': {'event_editor': ['title']}}}
        candidates = [candidate('mail', 'mail_detail', assertions=['subject']),
                      candidate('clock', 'alarm_card', assertions=['label'])]
        packets = [packet('p001', source='calendar', view='event_editor', assertions=['title']),
                   packet('p002', source='clock', view='alarm_card', assertions=['label'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        items = self.run_audit([cluster], packets, apps)['clusters'][0]['packets']
        self.assertEqual(items[0]['reason_code'], 'candidate_unmapped')
        self.assertIsNone(items[0]['matched_candidate'])
        self.assertEqual(items[1]['reason_code'], 'capability_surface_missing')
        self.assertEqual(items[1]['matched_candidate']['app_id'], 'clock')
        self.assertTrue(all(item['conclusion'] == 'missing' for item in items))

    def test_assertion_overreach_fails_closed(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject', 'from', 'date', 'body']}}}
        candidates = [candidate('mail', 'mail_detail', assertions=['subject', 'from'])]
        packets = [packet('p001', source='mail', view='mail_detail', assertions=['subject', 'body'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        item = self.single_packet(self.run_audit([cluster], packets, apps))
        self.assertEqual(item['conclusion'], 'missing')
        self.assertEqual(item['reason_code'], 'assertions_exceed_candidate')
        self.assertIn('body', item['reason'])

        narrow = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject', 'from']}}}
        wide = [candidate('mail', 'mail_detail', assertions=['subject', 'from', 'date'])]
        cluster['candidates'] = wide
        second = self.run_audit([cluster], [
            packet('p002', source='mail', view='mail_detail', assertions=['subject', 'date'])], narrow)
        item = self.single_packet(second)
        self.assertEqual(item['conclusion'], 'missing')
        self.assertEqual(item['reason_code'], 'assertions_exceed_capability')
        self.assertIn('date', item['reason'])

    def test_text_only_only_for_text_packets_without_capture(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject']}}}
        candidates = [candidate('mail', 'mail_detail', assertions=['subject'])]
        packets = [packet('t001', source='mail', packet_type='text'),
                   packet('g001', source='mail'),
                   packet('t002', source='mail', view='mail_detail', assertions=['subject'], packet_type='text')]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        items = self.run_audit([cluster], packets, apps)['clusters'][0]['packets']
        self.assertEqual([item['conclusion'] for item in items], ['text_only', 'missing', 'missing'])
        self.assertEqual([item['reason_code'] for item in items],
                         ['text_packet_without_capture', 'capture_spec_missing', 'non_gui_packet_with_capture'])

    def test_dataset_and_stream_binding_errors_are_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        datasets_root = root / 'datasets'
        datasets_root.mkdir()
        capabilities = write_json(root / 'capabilities.json', {'schema_version': '1.0', 'core_apps': {}, 'extension_apps': {}})
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': [candidate('mail', 'mail_detail', assertions=['subject'])]}
        dataset(datasets_root, DATASET, 'other-cluster', [packet('p001', source='mail', view='mail_detail', assertions=['subject'])])
        contract = write_json(root / 'audit.json', {'schema_version': '1.0', 'clusters': [cluster]})
        with self.assertRaisesRegex(ValueError, 'differs from declared'):
            audit(contract, datasets_root, capabilities)
        dataset(datasets_root, 'gui-extra-formal-pilot', 'extra', [])
        dataset(datasets_root, DATASET, 'dataset-x', [packet('p001', source='mail', view='mail_detail', assertions=['subject'])])
        with self.assertRaisesRegex(ValueError, 'undeclared formal pilot dataset'):
            audit(contract, datasets_root, capabilities)
        missing_root = root / 'empty'
        missing_root.mkdir()
        with self.assertRaisesRegex(ValueError, 'missing'):
            audit(contract, missing_root, capabilities)

    def test_duplicate_packet_id_is_rejected(self):
        apps = {'mail': {'status': 'pilot', 'views': {'mail_detail': ['subject']}}}
        candidates = [candidate('mail', 'mail_detail', assertions=['subject'])]
        packets = [packet('p001', source='mail', view='mail_detail', assertions=['subject']),
                   packet('p001', source='mail', view='mail_detail', assertions=['subject'])]
        cluster = {'cluster_id': 'x', 'formal_pilot_datasets': [
            {'dataset': DATASET, 'cluster_id': 'dataset-x'}], 'candidates': candidates}
        with self.assertRaisesRegex(ValueError, 'duplicate or missing packet_id'):
            self.run_audit([cluster], packets, apps)


if __name__ == '__main__':
    unittest.main()
