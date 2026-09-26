import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pb_backend_plan as pb
import pb_gui_bridge as bridge
import pb_gui_capture_compile as compiler
import pb_gui_capture_gate as capture_gate
import gui_capture

CLUSTER = 'meeting-preparation-001'
EPISODE = 'ep_0004'
PASSIVE = 'p002'
WORLD = 'w001'
DECISION = '2026-09-14T08:35:01+08:00'
OBSERVATION_START = '2026-09-14T08:30:00+08:00'
OBSERVED = '2026-09-14T08:30:26+08:00'
APPLIED_AT = '2026-09-14T08:40:00+08:00'
TZ = 'Asia/Shanghai'
MAIL_1_EFFECTIVE = '2026-09-14T08:31:45+08:00'
MAIL_2_EFFECTIVE = '2026-09-14T08:32:35+08:00'
PB_ROOT = ROOT.parent / '相关文件/ProactiveBench/ProactiveBench'
GUI_PACKETS = (('p003', 3, 'calendar', '2026-09-14T08:30:30+08:00'),
               ('p004', 4, 'calendar', '2026-09-14T08:30:44+08:00'),
               ('p009', 8, 'mail', '2026-09-14T08:31:35+08:00'),
               ('p010', 9, 'mail', '2026-09-14T08:31:48+08:00'),
               ('p013', 11, 'mail', '2026-09-14T08:32:35+08:00'),
               ('p015', 13, 'mail', '2026-09-14T08:33:05+08:00'),
               ('p018', 16, 'calendar', '2026-09-14T08:33:50+08:00'))
CAL = {'title': '项目评审 R7K2', 'start_at': '2026-09-14T09:00:00+08:00',
       'end_at': '2026-09-14T09:30:00+08:00', 'location': '会议室 A',
       'description': '项目评审 R7K2：开场演示后进入讨论。', 'reminder_minutes_before': -1,
       'source': 'calendar'}
MAIL_1 = {'headers': {'subject': '项目评审 R7K2｜开场安排', 'date': '2026-09-14T08:10:00+08:00',
                      'from': '周宁', 'to': 'user@example.test', 'sender': 'zhouning@example.test',
                      'senderLogo': ''},
          'body': '项目评审 R7K2 将于今天 09:00 在会议室 A 开始，09:30 结束。',
          'status': 'unread', 'attachments': []}
MAIL_2 = {'headers': {'subject': '项目评审 R7K2｜设备准备说明', 'date': '2026-09-14T08:20:00+08:00',
                      'from': '周宁', 'to': 'user@example.test', 'sender': 'zhouning@example.test',
                      'senderLogo': ''},
          'body': '关于项目评审 R7K2：投屏与麦克风联调共需要连续 10 分钟。',
          'status': 'unread', 'attachments': []}
ALARM = {'label': '晨起', 'time': '07:00', 'enabled': True}
RECORD_REFS = ('calendar.event.cal_001', 'mail.message.mail_001', 'mail.message.mail_002',
               'clock.alarm.alarm_101')
REF_SOURCES = {'calendar.event.cal_001': 'calendar', 'mail.message.mail_001': 'mail',
               'mail.message.mail_002': 'mail', 'clock.alarm.alarm_101': 'clock'}
SCOPE = (CLUSTER, EPISODE)
CALENDAR_REMINDER_EFFECTIVE = '2026-09-14T08:31:00+08:00'


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return path


def episode_scope(episode=EPISODE, passive=PASSIVE, world=WORLD):
    return {'cluster_id': CLUSTER, 'episode_id': episode, 'passive_id': passive,
            'world_variant_id': world, 'track': 'mixed_text', 'decision_point': DECISION,
            'observation_start': OBSERVATION_START, 'timezone': TZ,
            'world_status': 'new_bundle_not_applied_to_android',
            'world_hash': 'a' * 64, 'device_applied': False}


def gui_document(episode=EPISODE, passive=PASSIVE, world=WORLD, extra_packets=(), drop=()):
    packets = [{'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
                'observed_at': observed, 'delivered_at': observed,
                'text_event': f'{packet_id} summary'} for packet_id, sequence, source, observed
               in GUI_PACKETS if packet_id not in drop]
    packets.extend(copy.deepcopy(list(extra_packets)))
    return {'schema_version': 'pb-gui-bridge.plan.v1', 'generated_at': APPLIED_AT,
            'cluster_id': CLUSTER, 'episode_id': episode, 'passive_id': passive,
            'world_variant_id': world, 'decision_point': DECISION,
            'screenshot_dir': f'passive_inputs/{passive}/gui', 'packets': packets}


def seed_record(ref, seed_data, readback_data=None, history_changes=()):
    return {'record_ref': ref, 'semantic_ref': ref, 'status': 'supported',
            'seed': {'at': OBSERVED, 'data': copy.deepcopy(seed_data)},
            'readback': {'at': DECISION, 'data': copy.deepcopy(readback_data or seed_data),
                         'fields': []},
            'history_changes': [copy.deepcopy(change) for change in history_changes]}


def mail_history_change(effective_at):
    return {'effective_at': effective_at, 'set': {'status': 'read'}}


def backend_document(episode=EPISODE, passive=PASSIVE, world=WORLD):
    mail_1_read = dict(MAIL_1, status='read')
    mail_2_read = dict(MAIL_2, status='read')
    return {'schema_version': 'pb-backend-plan.v1', 'status': 'planned', 'device_applied': False,
            'episode_scope': episode_scope(episode, passive, world), 'decision_point': DECISION,
            'observation_start': OBSERVATION_START, 'timezone': TZ, 'world_variant_id': world,
            'history': {'observation_start': OBSERVATION_START},
            'sources': {
                'calendar': {'source': 'calendar',
                             'seed_records': [seed_record('calendar.event.cal_001', CAL)]},
                'mail': {'source': 'mail',
                         'seed_records': [seed_record('mail.message.mail_001', MAIL_1, mail_1_read,
                                                      [mail_history_change(MAIL_1_EFFECTIVE)]),
                                          seed_record('mail.message.mail_002', MAIL_2, mail_2_read,
                                                      [mail_history_change(MAIL_2_EFFECTIVE)])]},
                'clock': {'source': 'clock',
                          'seed_records': [seed_record('clock.alarm.alarm_101', ALARM)]}}}


def ep_0005_backend_document(drop_calendar_history=False, readback_reminder=None):
    """Ep_0005 shaped backend plan; w002 adds a Calendar reminder change at 08:31."""
    document = backend_document('ep_0005', 'p003', 'w002')
    document['episode_scope']['world_hash'] = 'b' * 64
    calendar = document['sources']['calendar']['seed_records'][0]
    seed_reminder = calendar['seed']['data']['reminder_minutes_before']
    if drop_calendar_history:
        reminder = seed_reminder if readback_reminder is None else readback_reminder
    else:
        reminder = 10 if readback_reminder is None else readback_reminder
    calendar['readback']['data'] = dict(calendar['seed']['data'],
                                        reminder_minutes_before=reminder)
    if not drop_calendar_history:
        calendar['history_changes'] = [{
            'effective_at': CALENDAR_REMINDER_EFFECTIVE,
            'set': {'calendar_reminder_minutes': [10, -1, -1],
                    'updated_at': CALENDAR_REMINDER_EFFECTIVE}}]
    return document


def receipt_document(backend, backend_path, record_refs=RECORD_REFS, scope_document=None):
    scope = copy.deepcopy(scope_document or backend['episode_scope'])
    records = []
    for ref in record_refs:
        contract = pb.ADAPTERS[REF_SOURCES[ref]]
        records.append({'order': len(records) + 1, 'source': REF_SOURCES[ref], 'semantic_ref': ref,
                        'adapter': {'module': contract['module'], 'seed': contract['seed'],
                                    'read': contract['read']},
                        'seed': {'at': OBSERVED, 'function': contract['seed'],
                                 'result': {'verified': True}},
                        'readback': {'at': DECISION, 'function': contract['read'],
                                     'result': {'verified': True}},
                        'status': 'ok'})
    return {'schema_version': 'pb-backend-apply-receipt.v1', 'status': 'ok', 'command': 'apply',
            'device_applied': True, 'allow_device_apply': True, 'applied_at': APPLIED_AT,
            'plan_binding': {'schema_version': backend['schema_version'],
                             'plan_sha256': pb.digest(backend_path), 'status': backend['status'],
                             'device_applied': backend['device_applied'],
                             'episode_scope': scope, 'world_status': scope.get('world_status'),
                             'world_hash': scope.get('world_hash')},
            'device': {'serial': 'emulator-5554', 'timezone': TZ, 'adb': 'adb'},
            'ordering': ['calendar', 'mail', 'clock'], 'records': records,
            'checks': {'seed_records': len(records), 'seeds': len(records),
                       'readbacks': len(records), 'failures': 0}}


class CompilePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.build_chain()

    def build_chain(self, episode=EPISODE, passive=PASSIVE, world=WORLD, gui=None, receipt=None):
        self.gui = gui or gui_document(episode, passive, world)
        self.gui_path = write_json(self.root / 'gui-plan.json', self.gui)
        self.backend = backend_document(episode, passive, world)
        self.backend_path = write_json(self.root / 'backend-plan.json', self.backend)
        self.receipt = receipt or receipt_document(self.backend, self.backend_path)
        self.receipt_path = write_json(self.root / 'receipt.json', self.receipt)
        self.gate = capture_gate.build_gate_plan(self.gui_path, self.receipt_path)
        self.gate_path = write_json(self.root / 'gate-plan.json', self.gate)

    def compile(self):
        return compiler.compile_plan(self.gui_path, self.backend_path, self.receipt_path,
                                     self.gate_path, *SCOPE)

    def test_executable_packet_plan_structure(self):
        plan = self.compile()
        self.assertEqual(plan['schema_version'], compiler.PLAN_SCHEMA)
        self.assertEqual(plan['baseline_ref'], 'init_state')
        self.assertEqual(plan['device_timezone'], 'Asia/Shanghai')
        self.assertEqual(plan['eof_at'], DECISION)
        self.assertEqual([packet['packet_id'] for packet in plan['packets']],
                         ['p003', 'p004', 'p009', 'p010', 'p013', 'p015', 'p018'])
        self.assertEqual(plan['unsupported_packets'], [])
        for packet in plan['packets']:
            self.assertEqual(set(packet) - {'history_replays'},
                             {'packet_id', 'sequence', 'tag', 'source', 'observed_at',
                              'delivered_at', 'text_event', 'capture'})
            self.assertEqual(packet['tag'], 'gui')
            capture = packet['capture']
            expected_keys = {'surface', 'app_id', 'view_id', 'subject_ref',
                             'visible_assertions', 'expected_visible', 'frames',
                             'timeout_seconds'}
            if packet['source'] == 'calendar':
                expected_keys.add('navigation_mode')
            self.assertEqual(set(capture), expected_keys)
            self.assertEqual(capture['surface'], 'app_screen')
            self.assertEqual(set(capture['visible_assertions']), set(capture['expected_visible']))
            self.assertTrue(all(isinstance(value, str) and value
                                for value in capture['expected_visible'].values()))
            self.assertEqual(capture['frames'], [{'frame_id': f'{packet["packet_id"]}_f1'}])
            self.assertEqual(capture['timeout_seconds'], compiler.TIMEOUT_SECONDS)
        by_id = {packet['packet_id']: packet['capture'] for packet in plan['packets']}
        day_expected = {'day_label': 'September 14 (Mon)', 'event_title': CAL['title'],
                        'event_time': '09:00 AM - 09:30 AM',
                        'event_description': CAL['description']}
        inbox_expected = {'row_1_sender': MAIL_2['headers']['from'],
                          'row_1_subject': MAIL_2['headers']['subject'],
                          'row_1_snippet': MAIL_2['body'],
                          'row_2_sender': MAIL_1['headers']['from'],
                          'row_2_subject': MAIL_1['headers']['subject'],
                          'row_2_snippet': MAIL_1['body']}
        self.assertEqual(by_id['p003']['view_id'], 'calendar_list')
        self.assertEqual(by_id['p003']['navigation_mode'], compiler.CALENDAR_DIRECT_NAVIGATION)
        self.assertEqual(by_id['p003']['expected_visible'], day_expected)
        self.assertEqual(by_id['p018']['view_id'], 'calendar_list')
        self.assertEqual(by_id['p018']['navigation_mode'], compiler.CALENDAR_DIRECT_NAVIGATION)
        self.assertEqual(by_id['p018']['expected_visible'], day_expected)
        self.assertEqual(by_id['p004']['view_id'], 'event_editor')
        self.assertEqual(by_id['p004']['navigation_mode'], compiler.CALENDAR_DIRECT_NAVIGATION)
        self.assertNotIn('navigation_mode', by_id['p010'])
        self.assertNotIn('navigation_mode', by_id['p013'])
        self.assertEqual(by_id['p004']['subject_ref'], 'calendar.event.cal_001')
        self.assertEqual(by_id['p004']['expected_visible']['title'], CAL['title'])
        self.assertEqual(by_id['p004']['expected_visible']['start_at'], '09:00 AM')
        self.assertEqual(by_id['p009']['view_id'], 'mail_inbox')
        self.assertNotIn('navigation_mode', by_id['p009'])
        self.assertEqual(by_id['p009']['expected_visible'], inbox_expected)
        self.assertEqual(by_id['p015']['view_id'], 'mail_inbox')
        self.assertNotIn('navigation_mode', by_id['p015'])
        self.assertEqual(by_id['p015']['expected_visible'], inbox_expected)
        self.assertEqual(by_id['p010']['view_id'], 'mail_detail')
        self.assertEqual(by_id['p010']['expected_visible']['subject'], MAIL_1['headers']['subject'])
        self.assertEqual(by_id['p013']['expected_visible']['subject'], MAIL_2['headers']['subject'])
        self.assertEqual(by_id['p013']['subject_ref'], 'mail.message.mail_002')
        records = {record['semantic_ref']: record for record in plan['fixture']['ku_records']}
        for packet in plan['packets']:
            subject = records[packet['capture']['subject_ref']]
            self.assertEqual(subject['app_id'], packet['source'])
            self.assertEqual(packet['capture']['app_id'], packet['source'])

    def test_history_replays_follow_each_packet_observed_at(self):
        plan = self.compile()
        by_id = {packet['packet_id']: packet for packet in plan['packets']}
        for packet_id in ('p003', 'p004', 'p009', 'p015', 'p018'):
            self.assertNotIn('history_replays', by_id[packet_id])
        self.assertEqual(by_id['p010']['history_replays'],
                         [{'semantic_ref': 'mail.message.mail_001',
                           'effective_at': MAIL_1_EFFECTIVE, 'set': {'status': 'read'}}])
        self.assertEqual(by_id['p013']['history_replays'],
                         [{'semantic_ref': 'mail.message.mail_002',
                           'effective_at': MAIL_2_EFFECTIVE, 'set': {'status': 'read'}}])
        self.assertEqual(plan['counts']['history_replays'], 2)
        self.assertEqual(plan['checks']['history_replays'], 2)
        for packet in plan['packets']:
            observed = pb.maybe_timestamp(packet['observed_at'])
            for replay in packet.get('history_replays', []):
                self.assertLessEqual(pb.maybe_timestamp(replay['effective_at']), observed)

    def test_inbox_packets_observe_the_history_state_of_their_instant(self):
        plan = self.compile()
        by_id = {packet['packet_id']: packet for packet in plan['packets']}
        # p009 is observed before mail_001 is read; p015 and p018 are observed
        # after both mail_001 and mail_002 were replayed to read by p010/p013.
        self.assertLess(pb.maybe_timestamp(by_id['p009']['observed_at']),
                        pb.maybe_timestamp(MAIL_1_EFFECTIVE))
        self.assertNotIn('history_replays', by_id['p009'])
        for packet_id in ('p015', 'p018'):
            self.assertNotIn('history_replays', by_id[packet_id])
            self.assertLessEqual(pb.maybe_timestamp(MAIL_2_EFFECTIVE),
                                 pb.maybe_timestamp(by_id[packet_id]['observed_at']))
        replays = [replay for packet in plan['packets']
                   for replay in packet.get('history_replays', [])]
        self.assertEqual([(replay['semantic_ref'], replay['set']['status'])
                          for replay in replays],
                         [('mail.message.mail_001', 'read'), ('mail.message.mail_002', 'read')])

    def test_fixture_and_bindings_match_the_backend_receipt(self):
        plan = self.compile()
        fixture = plan['fixture']
        self.assertEqual(fixture['episode_scope'], EPISODE)
        self.assertEqual(fixture['common_records'], [])
        self.assertEqual(fixture['world_deltas'], [])
        self.assertEqual([record['semantic_ref'] for record in fixture['ku_records']],
                         list(RECORD_REFS))
        by_ref = {record['semantic_ref']: record for record in fixture['ku_records']}
        self.assertEqual(by_ref['calendar.event.cal_001']['entity_type'], 'events')
        self.assertEqual(by_ref['calendar.event.cal_001']['data']['reminder_minutes_before'], -1)
        self.assertEqual(by_ref['mail.message.mail_001']['entity_type'], 'mails')
        self.assertEqual(by_ref['mail.message.mail_001']['data']['status'], 'unread')
        self.assertEqual(by_ref['mail.message.mail_002']['data']['status'], 'unread')
        self.assertEqual(by_ref['clock.alarm.alarm_101']['entity_type'], 'alarms')
        backend = json.loads(self.backend_path.read_text(encoding='utf8'))
        readback_status = {record['semantic_ref']: record['readback']['data']['status']
                           for record in backend['sources']['mail']['seed_records']}
        self.assertEqual(readback_status,
                         {'mail.message.mail_001': 'read', 'mail.message.mail_002': 'read'})
        assets = plan['source_assets']
        for name, path in (('gui_plan', self.gui_path), ('backend_plan', self.backend_path),
                           ('backend_receipt', self.receipt_path), ('gate_plan', self.gate_path)):
            self.assertEqual(assets[name]['sha256'], pb.digest(path))
        self.assertEqual(plan['checks']['images_read'], 0)
        self.assertEqual(plan['checks']['images_copied'], 0)
        self.assertEqual(pb.private_key_paths(plan), [])
        self.assertEqual(pb.absolute_value_paths(plan), [])
        serialized = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn('screenshot', serialized)
        self.assertNotIn('.png', serialized)

    def test_plan_binds_the_capture_clock_to_the_pb_observation_start(self):
        plan = self.compile()
        self.assertEqual(plan['pb_clock_binding'],
                         {'observation_start': OBSERVATION_START, 'decision_point': DECISION,
                          'timezone': TZ, 'source': 'PB history/cluster'})
        self.assertEqual(plan['initial_simulated_time'], OBSERVATION_START)
        start = pb.maybe_timestamp(OBSERVATION_START)
        eof = pb.maybe_timestamp(DECISION)
        for packet in plan['packets']:
            observed = pb.maybe_timestamp(packet['observed_at'])
            self.assertLessEqual(start, observed)
            self.assertLessEqual(observed, eof)
            self.assertEqual(observed.utcoffset(), start.utcoffset())
        self.assertLess(pb.maybe_timestamp(plan['pb_clock_binding']['observation_start']),
                        pb.maybe_timestamp(plan['pb_clock_binding']['decision_point']))

    def test_packet_before_the_observation_start_aborts(self):
        backend = backend_document()
        backend['observation_start'] = '2026-09-14T08:30:45+08:00'
        backend['episode_scope']['observation_start'] = backend['observation_start']
        backend['history']['observation_start'] = backend['observation_start']
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('precedes the backend observation_start', str(context.exception))

    def test_observation_start_after_the_decision_point_aborts(self):
        backend = backend_document()
        backend['observation_start'] = '2026-09-14T08:36:00+08:00'
        backend['episode_scope']['observation_start'] = backend['observation_start']
        backend['history']['observation_start'] = backend['observation_start']
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('is after decision_point', str(context.exception))

    def test_packet_after_the_decision_point_aborts(self):
        document = gui_document()
        for packet in document['packets']:
            if packet['packet_id'] == 'p018':
                packet['observed_at'] = '2026-09-14T08:35:02+08:00'
        gui_path = write_json(self.root / 'gui-after-eof.json', document)
        gate_path = write_json(self.root / 'gate-after-eof.json',
                               capture_gate.build_gate_plan(gui_path, self.receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui_path, self.backend_path, self.receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('after the backend decision_point', str(context.exception))

    def test_packet_timezone_offset_drift_aborts(self):
        document = gui_document()
        for packet in document['packets']:
            if packet['packet_id'] == 'p004':
                packet['observed_at'] = '2026-09-14T00:30:44+00:00'
        gui_path = write_json(self.root / 'gui-offset-drift.json', document)
        gate_path = write_json(self.root / 'gate-offset-drift.json',
                               capture_gate.build_gate_plan(gui_path, self.receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui_path, self.backend_path, self.receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('timezone offset does not match', str(context.exception))

    def test_missing_or_mismatched_observation_start_aborts(self):
        backend = backend_document()
        del backend['observation_start']
        del backend['episode_scope']['observation_start']
        del backend['history']['observation_start']
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('carries no observation_start', str(context.exception))
        backend = backend_document()
        backend['history']['observation_start'] = '2026-09-14T08:29:00+08:00'
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('does not match backend plan observation_start', str(context.exception))

    def test_cli_writes_plan_and_reports_summary(self):
        output = self.root / 'capture-plan.json'
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = compiler.main(['compile', '--cluster-id', CLUSTER, '--episode-id', EPISODE,
                                  '--gui-plan', str(self.gui_path), '--backend-plan',
                                  str(self.backend_path), '--backend-receipt',
                                  str(self.receipt_path), '--gate-plan', str(self.gate_path),
                                  '--output', str(output)])
        self.assertEqual(code, 0, stderr.getvalue())
        summary = json.loads(stdout.getvalue())
        self.assertEqual((summary['packets'], summary['unsupported_packets'],
                          summary['fixture_records']), (7, 0, 4))
        self.assertEqual(summary['history_replays'], 2)
        self.assertEqual(summary['episode_id'], EPISODE)
        self.assertEqual(json.loads(output.read_text(encoding='utf8'))['schema_version'],
                         compiler.PLAN_SCHEMA)

    def test_scope_mismatch_aborts(self):
        document = json.loads(self.gate_path.read_text(encoding='utf8'))
        document['episode_scope']['passive_id'] = 'p999'
        write_json(self.gate_path, document)
        with self.assertRaises(ValueError) as context:
            self.compile()
        self.assertIn('unsupported episode scope', str(context.exception))

    def test_unknown_episode_or_cluster_aborts(self):
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, self.backend_path, self.receipt_path,
                                  self.gate_path, CLUSTER, 'ep_9999')
        self.assertIn('unsupported episode', str(context.exception))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, self.backend_path, self.receipt_path,
                                  self.gate_path, 'unknown-cluster', EPISODE)
        self.assertIn('unsupported episode', str(context.exception))

    def test_explicit_selection_must_match_every_artifact(self):
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, self.backend_path, self.receipt_path,
                                  self.gate_path, CLUSTER, 'ep_0005')
        self.assertIn('unsupported episode scope', str(context.exception))

    def test_missing_or_incomplete_receipt_aborts(self):
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, self.backend_path, self.root / 'missing.json',
                                  self.gate_path, *SCOPE)
        self.assertIn('backend receipt not found', str(context.exception))
        receipt = receipt_document(self.backend, self.backend_path,
                                   record_refs=RECORD_REFS[:3])
        receipt_path = write_json(self.root / 'receipt-short.json', receipt)
        gate_path = write_json(self.root / 'gate-short.json',
                               capture_gate.build_gate_plan(self.gui_path, receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, self.backend_path, receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('misses planned seed records', str(context.exception))

    def test_hash_drift_aborts(self):
        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        receipt['applied_at'] = '2026-09-14T09:00:00+08:00'
        write_json(self.receipt_path, receipt)
        with self.assertRaises(ValueError) as context:
            self.compile()
        self.assertIn('stale', str(context.exception))
        self.build_chain()
        backend = json.loads(self.backend_path.read_text(encoding='utf8'))
        backend['generated_at'] = '2026-09-26T00:00:00+00:00'
        tampered = write_json(self.root / 'backend-plan-tampered.json', backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, tampered, self.receipt_path, self.gate_path,
                                  *SCOPE)
        self.assertIn('plan_sha256', str(context.exception))

    def test_unknown_or_drifted_gui_packet_aborts(self):
        extra = {'packet_id': 'p999', 'sequence': 17, 'tag': 'gui', 'source': 'calendar',
                 'observed_at': OBSERVED, 'delivered_at': OBSERVED, 'text_event': 'unknown'}
        gui_path = write_json(self.root / 'gui-unknown.json',
                              gui_document(extra_packets=[extra]))
        gate_path = write_json(self.root / 'gate-unknown.json',
                               capture_gate.build_gate_plan(gui_path, self.receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui_path, self.backend_path, self.receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('unsupported GUI packet', str(context.exception))
        gui_path = write_json(self.root / 'gui-missing.json', gui_document(drop=('p018',)))
        gate_path = write_json(self.root / 'gate-missing.json',
                               capture_gate.build_gate_plan(gui_path, self.receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui_path, self.backend_path, self.receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('miss frozen meeting-preparation-001/ep_0004 GUI packets',
                      str(context.exception))

    def test_source_drift_aborts(self):
        packets = gui_document()['packets']
        for packet in packets:
            if packet['packet_id'] == 'p004':
                packet['source'] = 'mail'
        document = gui_document()
        document['packets'] = packets
        gui_path = write_json(self.root / 'gui-source-drift.json', document)
        gate_path = write_json(self.root / 'gate-source-drift.json',
                               capture_gate.build_gate_plan(gui_path, self.receipt_path))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui_path, self.backend_path, self.receipt_path, gate_path,
                                  *SCOPE)
        self.assertIn('unsupported GUI packet', str(context.exception))

    def rebuild_chain(self, backend):
        backend_path = write_json(self.root / 'backend-replay.json', backend)
        receipt = receipt_document(backend, backend_path)
        receipt_path = write_json(self.root / 'receipt-replay.json', receipt)
        gate_path = write_json(self.root / 'gate-replay.json',
                               capture_gate.build_gate_plan(self.gui_path, receipt_path))
        return backend_path, receipt_path, gate_path

    def test_history_change_no_compiled_packet_can_cover_aborts(self):
        backend = backend_document()
        backend['sources']['mail']['seed_records'][0]['history_changes'] = [
            mail_history_change('2026-09-14T08:34:00+08:00')]
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('not covered by any compiled GUI packet', str(context.exception))

    def test_unknown_history_transition_or_source_aborts(self):
        backend = backend_document()
        backend['sources']['mail']['seed_records'][0]['history_changes'] = [
            mail_history_change('2026-09-14T08:31:00+08:00')]
        backend['sources']['mail']['seed_records'][0]['seed']['data']['status'] = 'read'
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('unknown history transition', str(context.exception))
        backend = backend_document()
        backend['sources']['calendar']['seed_records'][0]['history_changes'] = [
            mail_history_change('2026-09-14T08:31:00+08:00')]
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('Calendar history fields', str(context.exception))
        self.assertIn('cannot apply', str(context.exception))
        backend = backend_document()
        backend['sources']['clock']['seed_records'][0]['history_changes'] = [
            {'effective_at': '2026-09-14T08:31:00+08:00', 'set': {'label': 'changed'}}]
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('clock history changes', str(context.exception))
        self.assertIn('cannot replay or read back', str(context.exception))
        backend = backend_document()
        backend['sources']['mail']['seed_records'][0]['history_changes'] = [
            {'effective_at': '2026-09-14T08:31:00+08:00', 'set': {'status': 'archived'}}]
        backend_path, receipt_path, gate_path = self.rebuild_chain(backend)
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(self.gui_path, backend_path, receipt_path, gate_path, *SCOPE)
        self.assertIn('only allows the declared Mail unread -> read', str(context.exception))


@unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(), 'ProactiveBench tree not present')
class RealEpisodeTest(unittest.TestCase):
    def test_real_ep_0004_compiles_every_frozen_gui_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark, clusters = bridge.load_benchmark(PB_ROOT)
            cluster_id, cluster_dir, manifest = bridge.find_episode(
                clusters, 'ep_0004', 'meeting-preparation-001')
            gui_path = write_json(root / 'gui-plan.json',
                                  bridge.compile_episode(PB_ROOT, benchmark, cluster_id, cluster_dir,
                                                         manifest))
            backend = pb.compile_plan(PB_ROOT, 'ep_0004')
            backend_path = write_json(root / 'backend-plan.json', backend)
            receipt_path = write_json(root / 'receipt.json',
                                      receipt_document(backend, backend_path))
            gate_path = write_json(root / 'gate-plan.json',
                                   capture_gate.build_gate_plan(gui_path, receipt_path))
            plan = compiler.compile_plan(gui_path, backend_path, receipt_path, gate_path,
                                         'meeting-preparation-001', 'ep_0004')
            gui_capture.validate_private_capture_plan(plan)
            self.assertEqual([packet['packet_id'] for packet in plan['packets']],
                             ['p003', 'p004', 'p009', 'p010', 'p013', 'p015', 'p018'])
            self.assertEqual(plan['unsupported_packets'], [])
            self.assertEqual([record['semantic_ref'] for record in plan['fixture']['ku_records']],
                             list(RECORD_REFS))
            by_ref = {record['semantic_ref']: record for record in plan['fixture']['ku_records']}
            self.assertEqual(by_ref['mail.message.mail_001']['data']['status'], 'unread')
            self.assertEqual(by_ref['mail.message.mail_002']['data']['status'], 'unread')
            by_packet = {packet['packet_id']: packet for packet in plan['packets']}
            for packet_id in ('p003', 'p004', 'p018'):
                self.assertEqual(by_packet[packet_id]['capture']['navigation_mode'],
                                 compiler.CALENDAR_DIRECT_NAVIGATION)
            self.assertEqual(by_packet['p003']['capture']['view_id'], 'calendar_list')
            self.assertEqual(by_packet['p018']['capture']['view_id'], 'calendar_list')
            self.assertEqual(by_packet['p003']['capture']['expected_visible'],
                             {'day_label': 'September 14 (Mon)', 'event_title': '项目评审 R7K2',
                              'event_time': '09:00 AM - 09:30 AM',
                              'event_description': '项目评审 R7K2：开场演示后进入讨论。'})
            self.assertEqual(by_packet['p018']['capture']['expected_visible'],
                             by_packet['p003']['capture']['expected_visible'])
            self.assertEqual(by_packet['p009']['capture']['view_id'], 'mail_inbox')
            self.assertEqual(by_packet['p015']['capture']['view_id'], 'mail_inbox')
            inbox_rows = by_packet['p009']['capture']['expected_visible']
            self.assertEqual(inbox_rows['row_1_subject'], '项目评审 R7K2｜设备准备说明')
            self.assertEqual(inbox_rows['row_1_sender'], '周宁')
            self.assertEqual(inbox_rows['row_1_snippet'],
                             '关于项目评审 R7K2：投屏与麦克风联调共需要连续 10 分钟，应在开场演示之前完成。')
            self.assertEqual(inbox_rows['row_2_subject'], '项目评审 R7K2｜开场安排')
            self.assertEqual(inbox_rows['row_2_sender'], '周宁')
            self.assertEqual(inbox_rows['row_2_snippet'],
                             '项目评审 R7K2 将于今天 09:00 在会议室 A 开始，09:30 结束。'
                             '开场演示由你负责，之后进入讨论环节。')
            self.assertEqual(by_packet['p015']['capture']['expected_visible'], inbox_rows)
            self.assertNotIn('navigation_mode', by_packet['p010']['capture'])
            self.assertNotIn('navigation_mode', by_packet['p013']['capture'])
            self.assertNotIn('navigation_mode', by_packet['p009']['capture'])
            self.assertNotIn('navigation_mode', by_packet['p015']['capture'])
            for packet_id in ('p003', 'p004', 'p009', 'p015', 'p018'):
                self.assertNotIn('history_replays', by_packet[packet_id])
            self.assertEqual([replay['semantic_ref']
                              for replay in by_packet['p010']['history_replays']],
                             ['mail.message.mail_001'])
            self.assertEqual([replay['semantic_ref']
                              for replay in by_packet['p013']['history_replays']],
                             ['mail.message.mail_002'])
            self.assertEqual(plan['counts']['history_replays'], 2)
            self.assertEqual(plan['eof_at'], backend['decision_point'])
            self.assertEqual(plan['pb_clock_binding'],
                             {'observation_start': backend['observation_start'],
                              'decision_point': backend['decision_point'],
                              'timezone': backend['timezone'], 'source': 'PB history/cluster'})
            self.assertEqual(plan['initial_simulated_time'], backend['observation_start'])
            start = pb.maybe_timestamp(plan['pb_clock_binding']['observation_start'])
            for packet in plan['packets']:
                self.assertLessEqual(start, pb.maybe_timestamp(packet['observed_at']))
            self.assertEqual(plan['baseline_ref'], 'init_state')
            self.assertEqual(plan['device_timezone'], 'Asia/Shanghai')
            serialized = json.dumps(plan, ensure_ascii=False)
            self.assertNotIn('screenshot', serialized)
            self.assertEqual(pb.private_key_paths(plan), [])
            self.assertEqual(pb.absolute_value_paths(plan), [])


class Ep0005ProfileTest(unittest.TestCase):
    """ep_0005 (p003/w002) consumes the Calendar reminder replay capability."""

    EPISODE_ID = 'ep_0005'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def build_chain(self, backend):
        gui_path = write_json(self.root / 'gui-plan.json',
                              gui_document(self.EPISODE_ID, 'p003', 'w002'))
        backend_path = write_json(self.root / 'backend-plan.json', backend)
        receipt_path = write_json(self.root / 'receipt.json',
                                  receipt_document(backend, backend_path))
        gate_path = write_json(self.root / 'gate-plan.json',
                               capture_gate.build_gate_plan(gui_path, receipt_path))
        return gui_path, backend_path, receipt_path, gate_path

    def test_profile_declares_the_seven_ep_0004_equivalent_packet_views(self):
        profile = compiler.select_profile(CLUSTER, self.EPISODE_ID)
        compiler.check_packet_table(profile)
        self.assertEqual(profile['scope'], {'cluster_id': CLUSTER, 'episode_id': self.EPISODE_ID,
                                            'passive_id': 'p003', 'world_variant_id': 'w002'})
        self.assertEqual(set(profile['packets']),
                         {'p003', 'p004', 'p009', 'p010', 'p013', 'p015', 'p018'})
        views = {packet_id: entry['view_id'] for packet_id, entry in profile['packets'].items()}
        self.assertEqual(views, {packet_id: entry['view_id']
                                 for packet_id, entry in compiler.EP_0004_PACKETS.items()})

    def test_real_w002_calendar_reminder_change_compiles_with_a_history_replay(self):
        gui, backend, receipt, gate = self.build_chain(ep_0005_backend_document())
        plan = compiler.compile_plan(gui, backend, receipt, gate, CLUSTER, self.EPISODE_ID)
        self.assertEqual([packet['packet_id'] for packet in plan['packets']],
                         ['p003', 'p004', 'p009', 'p010', 'p013', 'p015', 'p018'])
        by_packet = {packet['packet_id']: packet for packet in plan['packets']}
        self.assertEqual([(replay['semantic_ref'], replay['effective_at'], replay['set'])
                          for replay in by_packet['p009']['history_replays']],
                         [('calendar.event.cal_001', CALENDAR_REMINDER_EFFECTIVE,
                           {'calendar_reminder_minutes': [10, -1, -1],
                            'updated_at': CALENDAR_REMINDER_EFFECTIVE})])
        self.assertEqual([replay['semantic_ref']
                          for replay in by_packet['p010']['history_replays']],
                         ['mail.message.mail_001'])
        self.assertEqual([replay['semantic_ref']
                          for replay in by_packet['p013']['history_replays']],
                         ['mail.message.mail_002'])
        self.assertEqual(plan['counts']['history_replays'], 3)
        self.assertEqual(plan['checks']['history_replays'], 3)
        for packet in plan['packets']:
            observed = pb.maybe_timestamp(packet['observed_at'])
            for replay in packet.get('history_replays', []):
                self.assertLessEqual(pb.maybe_timestamp(replay['effective_at']), observed)
        calendar = next(record for record in plan['fixture']['ku_records']
                        if record['semantic_ref'] == 'calendar.event.cal_001')
        # The fixture still seeds the observation start; the replay advances it.
        self.assertEqual(calendar['data']['reminder_minutes_before'], -1)

    def test_cli_compiles_ep_0005_and_refuses_malformed_calendar_changes(self):
        gui, backend, receipt, gate = self.build_chain(ep_0005_backend_document())
        output = self.root / 'capture-plan.json'
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = compiler.main(['compile', '--cluster-id', CLUSTER, '--episode-id',
                                  self.EPISODE_ID, '--gui-plan', str(gui), '--backend-plan',
                                  str(backend), '--backend-receipt', str(receipt), '--gate-plan',
                                  str(gate), '--output', str(output)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(output.exists())
        written = json.loads(output.read_text(encoding='utf8'))
        self.assertEqual(written['counts']['history_replays'], 3)

        invalid = (
            ({'calendar_reminder_minutes': [10, -1, -1], 'updated_at': CALENDAR_REMINDER_EFFECTIVE,
              'status': 'read'}, 'cannot apply'),
            ({'calendar_reminder_minutes': [10, -1], 'updated_at': CALENDAR_REMINDER_EFFECTIVE},
             'three-element integer list'),
            ({'calendar_reminder_minutes': [10, -1, True],
              'updated_at': CALENDAR_REMINDER_EFFECTIVE}, 'three-element integer list'),
            ({'calendar_reminder_minutes': [10, -1, -1]}, 'misses the declared Calendar'),
            ({'calendar_reminder_minutes': [10, -1, -1],
              'updated_at': '2026-09-14T08:30:00+08:00'}, 'does not match its effective_at'),
        )
        for updates, message in invalid:
            document = ep_0005_backend_document()
            document['sources']['calendar']['seed_records'][0]['history_changes'][0]['set'] = updates
            gui_path, backend_path, receipt_path, gate_path = self.build_chain(document)
            refused = self.root / 'refused-plan.json'
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = compiler.main(['compile', '--cluster-id', CLUSTER, '--episode-id',
                                      self.EPISODE_ID, '--gui-plan', str(gui_path),
                                      '--backend-plan', str(backend_path), '--backend-receipt',
                                      str(receipt_path), '--gate-plan', str(gate_path),
                                      '--output', str(refused)])
            self.assertEqual(code, 1)
            self.assertFalse(refused.exists())
            self.assertIn(message, stderr.getvalue())

    def test_calendar_change_without_a_changed_readback_refuses(self):
        gui, backend, receipt, gate = self.build_chain(
            ep_0005_backend_document(readback_reminder=-1))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui, backend, receipt, gate, CLUSTER, self.EPISODE_ID)
        message = str(context.exception)
        self.assertIn('changed Calendar readback', message)
        self.assertIn('calendar_reminder_minutes', message)

    def test_readback_change_without_a_declared_replay_refuses(self):
        gui, backend, receipt, gate = self.build_chain(
            ep_0005_backend_document(drop_calendar_history=True, readback_reminder=10))
        with self.assertRaises(ValueError) as context:
            compiler.compile_plan(gui, backend, receipt, gate, CLUSTER, self.EPISODE_ID)
        self.assertIn('no device replay could reproduce', str(context.exception))

    def test_without_the_calendar_change_the_rest_of_ep_0005_compiles(self):
        """Counterfactual probe: a w002 without the Calendar reminder change."""
        gui, backend, receipt, gate = self.build_chain(
            ep_0005_backend_document(drop_calendar_history=True))
        plan = compiler.compile_plan(gui, backend, receipt, gate, CLUSTER, self.EPISODE_ID)
        self.assertEqual(plan['episode_id'], self.EPISODE_ID)
        self.assertEqual(plan['passive_id'], 'p003')
        self.assertEqual(plan['world_variant_id'], 'w002')
        self.assertEqual([packet['packet_id'] for packet in plan['packets']],
                         ['p003', 'p004', 'p009', 'p010', 'p013', 'p015', 'p018'])
        by_packet = {packet['packet_id']: packet for packet in plan['packets']}
        self.assertEqual([replay['semantic_ref']
                          for replay in by_packet['p010']['history_replays']],
                         ['mail.message.mail_001'])
        self.assertEqual([replay['semantic_ref']
                          for replay in by_packet['p013']['history_replays']],
                         ['mail.message.mail_002'])
        self.assertNotIn('history_replays', by_packet['p009'])
        self.assertEqual(plan['counts']['history_replays'], 2)

    @unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(),
                         'ProactiveBench tree not present')
    def test_real_ep_0005_offline_compile_emits_the_calendar_replay(self):
        """Real PB artifacts, synthetic offline receipt: the Calendar replay compiles."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark, clusters = bridge.load_benchmark(PB_ROOT)
            cluster_id, cluster_dir, manifest = bridge.find_episode(
                clusters, self.EPISODE_ID, CLUSTER)
            gui_path = write_json(root / 'gui-plan.json',
                                  bridge.compile_episode(PB_ROOT, benchmark, cluster_id, cluster_dir,
                                                         manifest))
            backend = pb.compile_plan(PB_ROOT, self.EPISODE_ID)
            backend_path = write_json(root / 'backend-plan.json', backend)
            receipt_path = write_json(root / 'receipt.json',
                                      receipt_document(backend, backend_path))
            gate_path = write_json(root / 'gate-plan.json',
                                   capture_gate.build_gate_plan(gui_path, receipt_path))
            output = root / 'capture-plan.json'
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = compiler.main(['compile', '--cluster-id', CLUSTER, '--episode-id',
                                      self.EPISODE_ID, '--gui-plan', str(gui_path),
                                      '--backend-plan', str(backend_path), '--backend-receipt',
                                      str(receipt_path), '--gate-plan', str(gate_path),
                                      '--output', str(output)])
            message = stderr.getvalue()
            self.assertEqual(code, 0, message)
            self.assertTrue(output.exists())
            gui_capture.validate_private_capture_plan(
                json.loads(output.read_text(encoding='utf8')))
            written = json.loads(output.read_text(encoding='utf8'))
            self.assertEqual(written['counts']['history_replays'], 3)
            by_packet = {packet['packet_id']: packet for packet in written['packets']}
            self.assertEqual(by_packet['p009']['history_replays'],
                             [{'semantic_ref': 'calendar.event.cal_001',
                               'effective_at': CALENDAR_REMINDER_EFFECTIVE,
                               'set': {'calendar_reminder_minutes': [10, -1, -1],
                                       'updated_at': CALENDAR_REMINDER_EFFECTIVE}}])
            self.assertEqual([replay['semantic_ref']
                              for replay in by_packet['p010']['history_replays']],
                             ['mail.message.mail_001'])
            self.assertEqual([replay['semantic_ref']
                              for replay in by_packet['p013']['history_replays']],
                             ['mail.message.mail_002'])
            self.assertEqual(pb.private_key_paths(written), [])
            self.assertEqual(pb.absolute_value_paths(written), [])


if __name__ == '__main__':
    unittest.main()
