import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pb_backend_plan as pb

PB_ROOT = ROOT.parent / '相关文件/ProactiveBench/ProactiveBench'
WORLD_HASH = 'a' * 64
DECISION = '2026-09-14T08:35:01+08:00'
START = '2026-09-14T08:30:00+08:00'
CAL_EVENT = {'event_id': 'cal_001', 'business_ref': 'R7K2', 'title': '评审', 'start_at': '2026-09-14T09:00:00+08:00',
             'end_at': '2026-09-14T09:30:00+08:00', 'timezone': 'Asia/Shanghai', 'location': 'A',
             'description': '开场', 'calendar_reminder_minutes': [-1, -1, -1], 'status': 'confirmed',
             'updated_at': '2026-09-14T08:00:00+08:00'}
MAIL = {'headers': {'subject': '准备', 'date': '2026-09-14T08:20:00+08:00', 'from': '周宁',
                    'to': 'user@example.test', 'sender': 'zhouning@example.test', 'senderLogo': ''},
        'body': '联调 10 分钟', 'status': 'read', 'attachments': []}
ALARM = {'alarm_id': 'alarm_101', 'label': '晨起', 'hour': 7, 'minute': 0, 'enabled': True,
         'repeat_days': ['MO'], 'next_fire_at': '2026-09-15T07:00:00+08:00'}


def build_tree(root):
    def write(rel, payload):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')

    write('benchmark.json', {'layout_version': 'proactivebench.layout.v4', 'default_cluster': 'demo-001',
                             'clusters': {'demo-001': {'path': 'single-scenarios/demo-001'}}})
    write('shared/source_capabilities.json', {})
    write('shared/baselines/mobileworld_init_v1/access/evidence_tree.json', {})
    write('shared/baselines/mobileworld_init_v1/access/bindings.json', {})
    write('shared/baselines/mobileworld_init_v1/world_base.json', {
        'schema_version': 'aa-aca.mobileworld-baseline.v1',
        'records': {
            'calendar.record.base1': {'source': 'calendar', 'data': dict(CAL_EVENT, event_id='base1', title='Base')},
            'mail.record.base1': {'source': 'mail', 'data': {'message_id': 'base1', 'subject': 'Base',
                                                             'sent_at': '2026-09-14T07:00:00+08:00', 'from_name': 'x',
                                                             'sender': 'x@example.test', 'to': 'user@example.test',
                                                             'body': 'b', 'attachments': [], 'status': 'read'}},
            'clock.record.base1': {'source': 'clock', 'data': {'alarm_id': 'base1', 'label': 'base', 'hour': 6,
                                                               'minute': 30, 'enabled': False, 'repeat_days': [],
                                                               'next_fire_at': None}},
            'messages.record.x1': {'source': 'messages', 'data': {'message_id': 'x1'}}}})
    cluster = {
        'schema_version': 'aa-aca.meeting-cluster.v1', 'cluster_id': 'demo-001', 'decision_point': DECISION,
        'global_base_ref': 'shared://baselines/mobileworld_init_v1/world_base.json',
        'baseline_access_ref': 'shared://baselines/mobileworld_init_v1/access/evidence_tree.json',
        'baseline_binding_ref': 'shared://baselines/mobileworld_init_v1/access/bindings.json',
        'access_build_report_ref': 'validation/access_build.json', 'source_registry_ref': 'shared://source_capabilities.json',
        'cluster_base_ref': 'active_world/cluster_base.patch.json', 'need_rule_ref': 'need_rules/rules.json',
        'compatibility_ref': 'compatibility/matrix.json', 'validation_status_ref': 'validation/status.json',
        'initialization_contract_ref': 'active_world/initialization/adapter_contract.json',
        'world_context': {'timezone': 'Asia/Shanghai', 'logical_time': DECISION}, 'episode_ids': ['ep_0001'],
        'world_variants': [{'world_variant_id': 'w001', 'patch_ref': 'active_world/variants/w001.patch.json',
                            'access_ref': 'active_world/access/w001/evidence_tree.json',
                            'binding_ref': 'active_world/access/w001/bindings.json',
                            'history_ref': 'active_world/history/w001.json',
                            'initialization_ref': 'active_world/initialization/w001.json'}],
        'passive_variants': [{'passive_id': 'p001', 'stream_ref': 'passive_inputs/p001/stream.jsonl',
                              'track': 'mixed_text', 'passive_revision': 'rev1'}]}
    write('single-scenarios/demo-001/cluster.json', cluster)
    write('single-scenarios/demo-001/compatibility/matrix.json', {
        'pairs': [{'passive_id': 'p001', 'world_variant_id': 'w001', 'status': 'development_only',
                   'episode_id': 'ep_0001', 'history_ref': 'active_world/history/w001.json'}],
        'passive_revisions': {'p001': 'rev1'}, 'cartesian_product_not_assumed': True})
    write('single-scenarios/demo-001/active_world/cluster_base.patch.json', {
        'patches': [{'op': 'add', 'entity_ref': 'calendar.event.cal_001',
                     'record': {'source': 'calendar', 'data': dict(CAL_EVENT)}},
                    {'op': 'add', 'entity_ref': 'mail.message.mail_001',
                     'record': {'source': 'mail', 'data': {'message_id': 'mail_001', 'subject': '准备',
                                                           'sent_at': '2026-09-14T08:20:00+08:00', 'from_name': '周宁',
                                                           'sender': 'zhouning@example.test', 'to': 'user@example.test',
                                                           'body': '联调 10 分钟', 'attachments': [], 'status': 'read'}}},
                    {'op': 'add', 'entity_ref': 'clock.alarm.alarm_101', 'record': {'source': 'clock', 'data': dict(ALARM)}}]})
    write('single-scenarios/demo-001/active_world/variants/w001.patch.json', {'patches': []})
    write('single-scenarios/demo-001/active_world/access/w001/evidence_tree.json',
          {'world_hash': WORLD_HASH, 'world_variant_id': 'w001'})
    write('single-scenarios/demo-001/active_world/access/w001/bindings.json', {
        'world_hash': WORLD_HASH, 'world_variant_id': 'w001', 'queries': {
            'calendar.list': {'result_kind': 'list', 'record_refs': ['calendar.event.cal_001', 'calendar.record.base1']},
            'mail.list': {'result_kind': 'list', 'record_refs': ['mail.message.mail_001']},
            'clock.list': {'result_kind': 'list', 'record_refs': ['clock.alarm.alarm_101']},
            'messages.list': {'result_kind': 'list', 'record_refs': ['messages.record.x1']}}})
    write('single-scenarios/demo-001/active_world/history/w001.json', {
        'world_variant_id': 'w001', 'observation_start': START,
        'initial_overrides': [{'entity_ref': 'mail.message.mail_001', 'set': {'status': 'unread'}}],
        'events': [{'effective_at': '2026-09-14T08:31:45+08:00',
                    'updates': [{'entity_ref': 'mail.message.mail_001', 'set': {'status': 'read'}}]}]})
    write('single-scenarios/demo-001/active_world/initialization/w001.json', {
        'world_variant_id': 'w001', 'world_hash': WORLD_HASH, 'target_time': DECISION,
        'timezone': 'Asia/Shanghai', 'status': 'new_bundle_not_applied_to_android',
        'adapter_contract_ref': 'active_world/initialization/adapter_contract.json',
        'composition': {'baseline_ref': 'shared://baselines/mobileworld_init_v1/world_base.json',
                        'cluster_patch_ref': 'active_world/cluster_base.patch.json',
                        'variant_patch_ref': 'active_world/variants/w001.patch.json'},
        'payload': {'calendar': [dict(CAL_EVENT)], 'mail': {'mails': [dict(MAIL)]}, 'clock': [dict(ALARM)],
                    'device': {'timezone': 'Asia/Shanghai'}}})
    write('single-scenarios/demo-001/active_world/initialization/adapter_contract.json', {
        'observation_start': {'logical_time': START,
                              'calendar_seed_ref': 'active_world/initialization/observation_start/calendar_events.initial.json',
                              'mail_seed_ref': 'active_world/initialization/observation_start/mail_state.initial.json',
                              'clock_policy': 'world clock', 'history_policy': 'apply history at time'},
        'decision_point': {'logical_time_source': 'cluster.json decision_point'}})
    write('single-scenarios/demo-001/active_world/initialization/observation_start/calendar_events.initial.json', [dict(CAL_EVENT)])
    write('single-scenarios/demo-001/active_world/initialization/observation_start/mail_state.initial.json',
          {'mails': [dict(MAIL, status='unread')]})
    write('single-scenarios/demo-001/episodes/ep_0001/manifest.json', {
        'episode_id': 'ep_0001', 'world_variant_id': 'w001', 'passive_id': 'p001', 'track': 'mixed_text',
        'cluster_ref': 'cluster.json', 'passive_ref': 'passive_inputs/p001/stream.jsonl',
        'patch_ref': 'active_world/variants/w001.patch.json', 'access_ref': 'active_world/access/w001/evidence_tree.json',
        'binding_ref': 'active_world/access/w001/bindings.json', 'history_ref': 'active_world/history/w001.json',
        'initialization_ref': 'active_world/initialization/w001.json', 'decision_point': DECISION,
        'timezone': 'Asia/Shanghai', 'passive_revision': 'rev1', 'gt_ref': 'episodes/ep_0001/private_gt.json'})
    write('single-scenarios/demo-001/episodes/ep_0001/private_gt.json', {'need': 'SECRET', 'evidence_sets': ['SECRET']})
    write('single-scenarios/demo-001/need_rules/rules.json', {})
    write('single-scenarios/demo-001/validation/status.json', {})
    write('single-scenarios/demo-001/validation/access_build.json', {})
    write('single-scenarios/demo-001/passive_inputs/p001/stream.jsonl', '{"packet_id": "x", "sequence": 1}\n')
    (root / 'single-scenarios/demo-001/passive_inputs/p001/stream.jsonl').write_text(
        '{"packet_id": "x", "sequence": 1, "tag": "text", "source": "mail",' +
        ' "observed_at": "%s", "delivered_at": "%s", "text_event": "hello"}\n' % (START, START), encoding='utf8')


CONTACT_FIELDS = [
    {'mimetype': 'vnd.android.cursor.item/name', 'data1': 'Ada Lovelace'},
    {'mimetype': 'vnd.android.cursor.item/phone_v2', 'data1': '+15550101'},
    {'mimetype': 'vnd.android.cursor.item/email_v2', 'data1': 'ada@example.test'}]
CONTACT_REF = 'contacts.record.c001'
CONTACT_EFFECTIVE = '2026-09-14T08:31:00+08:00'


def add_contacts_scene(root, fields=None, record_ref=CONTACT_REF, record_id='c001',
                       title='Ada Lovelace'):
    """Wire one PB contacts record into the synthetic demo-001 tree as a scene record."""
    def load(rel):
        return json.loads((root / rel).read_text(encoding='utf8'))

    def save(rel, payload):
        (root / rel).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')

    record = {'source': 'contacts', 'entity_type': 'contacts_record',
              'data': {'record_id': record_id, 'title': title,
                       'details': {'fields': copy.deepcopy(
                           fields if fields is not None else CONTACT_FIELDS)}}}
    cluster_rel = 'single-scenarios/demo-001/active_world/cluster_base.patch.json'
    cluster = load(cluster_rel)
    cluster['patches'].append({'op': 'add', 'entity_ref': record_ref, 'record': record})
    save(cluster_rel, cluster)
    init_rel = 'single-scenarios/demo-001/active_world/initialization/w001.json'
    initialization = load(init_rel)
    initialization['payload']['contacts'] = [copy.deepcopy(record['data'])]
    save(init_rel, initialization)
    binding_rel = 'single-scenarios/demo-001/active_world/access/w001/bindings.json'
    bindings = load(binding_rel)
    bindings['queries']['contacts.list'] = {'result_kind': 'list', 'record_refs': [record_ref]}
    save(binding_rel, bindings)
    return record_ref


def add_contacts_history(root, entity_ref=CONTACT_REF, settlement=None):
    history_rel = 'single-scenarios/demo-001/active_world/history/w001.json'
    history = json.loads((root / history_rel).read_text(encoding='utf8'))
    changes = {'title': 'Ada L.'}
    history['events'].append({'effective_at': settlement or CONTACT_EFFECTIVE,
                              'updates': [{'entity_ref': entity_ref, 'set': dict(changes)}]})
    (root / history_rel).write_text(json.dumps(history, ensure_ascii=False), encoding='utf8')
    init_rel = 'single-scenarios/demo-001/active_world/initialization/w001.json'
    initialization = json.loads((root / init_rel).read_text(encoding='utf8'))
    for item in initialization['payload']['contacts']:
        item.update(changes)
    (root / init_rel).write_text(json.dumps(initialization, ensure_ascii=False), encoding='utf8')


class SyntheticPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_tree(self.root)
        self.addCleanup(self.tmp.cleanup)

    def compile(self):
        return pb.compile_plan(self.root, 'ep_0001')

    def test_supported_mapping_and_unsupported_sources(self):
        plan = self.compile()
        self.assertEqual(plan['status'], 'planned')
        self.assertFalse(plan['device_applied'])
        self.assertEqual(plan['world_variant_id'], 'w001')
        self.assertEqual(plan['sources']['calendar']['seed_records'][0]['seed']['data'],
                         {'title': '评审', 'start_at': '2026-09-14T09:00:00+08:00',
                          'end_at': '2026-09-14T09:30:00+08:00', 'location': 'A', 'description': '开场',
                          'reminder_minutes_before': -1, 'source': 'calendar'})
        mail = plan['sources']['mail']['seed_records'][0]
        self.assertEqual((mail['seed']['data']['status'], mail['readback']['data']['status']),
                         ('unread', 'read'))
        self.assertIn('headers', mail['seed']['data'])
        clock = plan['sources']['clock']['seed_records'][0]['seed']['data']
        self.assertEqual(clock, {'label': '晨起', 'time': '07:00', 'enabled': True})
        self.assertEqual([event['effective_at'] for event in plan['history']['events']],
                         ['2026-09-14T08:31:45+08:00'])
        self.assertEqual(len(plan['history']['initial_overrides']), 1)
        unsupported = {entry['source']: entry for entry in plan['unsupported']}
        self.assertEqual(sorted(unsupported), ['device', 'messages'])
        self.assertTrue(unsupported['messages']['reason'])
        self.assertTrue(unsupported['device']['reason'])
        self.assertEqual(unsupported['messages']['record_refs'], ['messages.record.x1'])
        self.assertEqual(plan['checks']['private_fields_emitted'], 0)
        self.assertNotIn('SECRET', json.dumps(plan, ensure_ascii=False))

    def test_path_time_and_pair_rejections(self):
        cases = {'cluster-ref': ['single-scenarios/demo-001/cluster.json', 'cluster_base_ref', '../outside.json'],
                 'target': ['single-scenarios/demo-001/active_world/initialization/w001.json', 'target_time',
                            '2026-09-14T08:36:01+08:00'],
                 'event': ['single-scenarios/demo-001/active_world/history/w001.json', 'events',
                           [{'effective_at': '2026-09-14T08:20:00+08:00',
                             'updates': [{'entity_ref': 'mail.message.mail_001', 'set': {'status': 'read'}}]}]]}
        for name, (rel, key, value) in cases.items():
            with self.subTest(name):
                path = self.root / rel
                original = json.loads(path.read_text(encoding='utf8'))
                payload = copy.deepcopy(original)
                payload[key] = value
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')
                with self.assertRaises(ValueError):
                    self.compile()
                path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf8')
        manifest_path = self.root / 'single-scenarios/demo-001/episodes/ep_0001/manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        manifest['passive_id'] = 'p999'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf8')
        with self.assertRaises(ValueError):
            self.compile()

    def test_validate_rejects_completion_claim_and_leaks(self):
        plan = self.compile()
        pb.validate_plan(plan)
        for change in ({'device_applied': True}, {'status': 'applied'}):
            broken = copy.deepcopy(plan)
            broken.update(change)
            with self.assertRaises(ValueError):
                pb.validate_plan(broken)
        leaked = copy.deepcopy(plan)
        leaked['oracle'] = {}
        with self.assertRaises(ValueError):
            pb.validate_plan(leaked)
        absolute = copy.deepcopy(plan)
        absolute['sources']['calendar']['seed_records'][0]['seed']['data']['title'] = '/home/me/shot.png'
        with self.assertRaises(ValueError):
            pb.validate_plan(absolute)
        broken_ref = copy.deepcopy(plan)
        broken_ref['references']['manifest']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            pb.validate_plan(broken_ref, self.root)


class ContactsPlanTest(unittest.TestCase):
    """Contacts joins the plan only through records the existing adapter can verify."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_tree(self.root)
        self.addCleanup(self.tmp.cleanup)

    def compile(self):
        return pb.compile_plan(self.root, 'ep_0001')

    def contacts_records(self):
        return [entry for entry in self.compile()['unsupported'] if entry['source'] == 'contacts']

    def test_seed_readback_contract_and_ordering(self):
        add_contacts_scene(self.root)
        plan = self.compile()
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual([source for source in pb.SUPPORTED_SOURCES if source in plan['sources']],
                         ['calendar', 'mail', 'clock', 'contacts'])
        self.assertEqual(plan['sources']['contacts']['adapter'], pb.ADAPTERS['contacts'])
        self.assertEqual(plan['sources']['contacts']['field_map'], pb.FIELD_MAP['contacts'])
        self.assertEqual(plan['sources']['contacts']['expected_readback_fields'],
                         pb.READBACK_FIELDS['contacts'])
        record = plan['sources']['contacts']['seed_records'][0]
        expected = {'name': 'Ada Lovelace', 'phone': '+15550101', 'email': 'ada@example.test'}
        self.assertEqual(record['seed']['data'], expected)
        self.assertEqual(record['readback']['data'], expected)
        self.assertEqual(record['readback']['fields'], ['name', 'phone', 'email'])
        self.assertEqual(record['history_changes'], [])
        self.assertEqual([event for event in plan['history']['events']
                          if event['updates'][0]['entity_ref'] == CONTACT_REF], [])
        self.assertEqual(self.contacts_records(), [])
        pb.validate_plan(plan)

    def test_contact_without_email_uses_name_and_phone_only(self):
        add_contacts_scene(self.root, [field for field in CONTACT_FIELDS
                                       if field['mimetype'] != 'vnd.android.cursor.item/email_v2'])
        record = self.compile()['sources']['contacts']['seed_records'][0]
        self.assertEqual(record['seed']['data'], {'name': 'Ada Lovelace', 'phone': '+15550101'})
        self.assertEqual(record['readback']['fields'], ['name', 'phone'])

    def test_unknown_contact_field_fails_closed(self):
        add_contacts_scene(self.root, CONTACT_FIELDS + [
            {'mimetype': 'vnd.android.cursor.item/organization', 'data1': 'Analytical Engines'}])
        plan = self.compile()
        self.assertEqual([entry for entry in plan['unsupported'] if entry['source'] == 'contacts'][0]
                         ['record_refs'], [CONTACT_REF])
        self.assertNotIn('contacts', plan['sources'])
        self.assertEqual(plan['checks']['supported_seed_records'], 3)

    def test_duplicate_contact_email_fails_closed(self):
        add_contacts_scene(self.root, CONTACT_FIELDS + [
            {'mimetype': 'vnd.android.cursor.item/email_v2', 'data1': 'ada.work@example.test'}])
        plan = self.compile()
        self.assertEqual([entry for entry in plan['unsupported'] if entry['source'] == 'contacts'][0]
                         ['record_refs'], [CONTACT_REF])
        self.assertNotIn('contacts', plan['sources'])

    def test_contact_history_change_fails_closed(self):
        add_contacts_scene(self.root)
        add_contacts_history(self.root)
        plan = self.compile()
        self.assertEqual([entry for entry in plan['unsupported'] if entry['source'] == 'contacts'][0]
                         ['record_refs'], [CONTACT_REF])
        self.assertNotIn('contacts', plan['sources'])
        contacts_events = [update for event in plan['history']['events']
                           for update in event['updates'] if update['entity_ref'] == CONTACT_REF]
        self.assertEqual(len(contacts_events), 1)
        self.assertEqual(contacts_events[0]['supported'], False)
        pb.validate_plan(plan)


@unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(), 'ProactiveBench tree not present')
class RealEpisodeTest(unittest.TestCase):
    def test_meeting_ep_0004_slice(self):
        plan = pb.compile_plan(PB_ROOT, 'ep_0004')
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['episode_scope']['cluster_id'], 'meeting-preparation-001')
        self.assertEqual(plan['episode_scope']['passive_id'], 'p002')
        self.assertEqual(plan['world_variant_id'], 'w001')
        self.assertFalse(plan['device_applied'])
        self.assertEqual(sorted(plan['sources']), ['calendar', 'clock', 'mail'])
        refs = {item['record_ref'] for entries in plan['sources'].values() for item in entries['seed_records']}
        self.assertEqual(refs, {'calendar.event.cal_001', 'mail.message.mail_001',
                                'mail.message.mail_002', 'clock.alarm.alarm_101'})
        self.assertEqual(plan['sources']['calendar']['seed_records'][0]['seed']['data']['start_at'],
                         '2026-09-14T09:00:00+08:00')
        self.assertEqual(plan['checks']['world_records'] > 0, True)
        self.assertEqual(plan['checks']['references_checked'],
                         plan['checks']['references_within_root_and_existing'])
        pb.validate_plan(plan, PB_ROOT)

    def test_contact_episodes_offline_smoke(self):
        for episode_id, variant in (('ep_contact_001', 'w001'), ('ep_contact_002', 'w002'),
                                    ('ep_contact_003', 'w003')):
            with self.subTest(episode_id):
                plan = pb.compile_plan(PB_ROOT, episode_id)
                self.assertEqual(plan['episode_scope']['cluster_id'], 'contact-email-sync-001')
                self.assertEqual(plan['world_variant_id'], variant)
                self.assertFalse(plan['device_applied'])
                self.assertEqual(plan['history']['events'], [])
                self.assertEqual(plan['history']['initial_overrides'], [])
                contacts = [entry for entry in plan['unsupported'] if entry['source'] == 'contacts']
                self.assertEqual(len(contacts), 1)
                self.assertEqual(contacts[0]['record_refs'], ['contacts.record.8b7e02a4'])
                self.assertTrue(contacts[0]['reason'])
                self.assertNotIn('contacts', plan['sources'])
                self.assertEqual(plan['checks']['supported_seed_records'], 0)
                self.assertEqual(plan['status'], 'unsupported')
                pb.validate_plan(plan, PB_ROOT)


if __name__ == '__main__':
    unittest.main()
