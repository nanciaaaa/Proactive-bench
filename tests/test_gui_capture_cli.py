import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gui_capture

OBSERVATION_START = '2026-09-14T08:30:00+08:00'
DECISION_POINT = '2026-09-14T08:35:01+08:00'
CALENDAR = {'title': '项目评审 R7K2', 'start_at': '2026-09-14T09:00:00+08:00',
            'end_at': '2026-09-14T09:30:00+08:00', 'location': '会议室 A',
            'description': '项目评审 R7K2：开场演示后进入讨论。', 'reminder_minutes_before': -1,
            'source': 'calendar'}
MAIL_1 = {'headers': {'subject': '项目评审 R7K2｜开场安排',
                      'date': '2026-09-14T08:10:00+08:00', 'from': '周宁',
                      'to': 'user@example.test', 'sender': 'zhouning@example.test',
                      'senderLogo': ''},
          'body': '项目评审 R7K2 将于今天 09:00 在会议室 A 开始，09:30 结束。', 'status': 'unread',
          'attachments': []}
MAIL_2 = {'headers': {'subject': '项目评审 R7K2｜设备准备说明',
                      'date': '2026-09-14T08:20:00+08:00', 'from': '周宁',
                      'to': 'user@example.test', 'sender': 'zhouning@example.test',
                      'senderLogo': ''},
          'body': '关于项目评审 R7K2：投屏与麦克风联调共需要连续 10 分钟。', 'status': 'unread',
          'attachments': []}
ALARM = {'label': '晨起', 'time': '07:00', 'enabled': True}
CALENDAR_LIST = {'day_label': 'September 14 (Mon)', 'event_title': '项目评审 R7K2',
                 'event_time': '09:00 AM - 09:30 AM',
                 'event_description': '项目评审 R7K2：开场演示后进入讨论。'}
CALENDAR_EDITOR = {'title': '项目评审 R7K2', 'location': '会议室 A',
                   'description': '项目评审 R7K2：开场演示后进入讨论。',
                   'start_at': '09:00 AM', 'end_at': '09:30 AM'}
MAIL_1_DETAIL = {'subject': MAIL_1['headers']['subject'], 'from': '周宁', 'body': MAIL_1['body']}
MAIL_2_DETAIL = {'subject': MAIL_2['headers']['subject'], 'from': '周宁', 'body': MAIL_2['body']}
INBOX = {'row_1_sender': '周宁', 'row_1_subject': MAIL_2['headers']['subject'],
         'row_1_snippet': MAIL_2['body'], 'row_2_sender': '周宁',
         'row_2_subject': MAIL_1['headers']['subject'], 'row_2_snippet': MAIL_1['body']}
MAIL_1_EFFECTIVE = '2026-09-14T08:31:45+08:00'
MAIL_2_EFFECTIVE = '2026-09-14T08:32:35+08:00'
GUI_PACKETS = (('p003', 3, 'calendar', 'calendar_list', 'calendar.event.cal_001',
                '2026-09-14T08:30:30+08:00', CALENDAR_LIST),
               ('p004', 4, 'calendar', 'event_editor', 'calendar.event.cal_001',
                '2026-09-14T08:30:44+08:00', CALENDAR_EDITOR),
               ('p009', 8, 'mail', 'mail_inbox', 'mail.message.mail_002',
                '2026-09-14T08:31:35+08:00', INBOX),
               ('p010', 9, 'mail', 'mail_detail', 'mail.message.mail_001',
                '2026-09-14T08:31:48+08:00', MAIL_1_DETAIL),
               ('p013', 11, 'mail', 'mail_detail', 'mail.message.mail_002',
                '2026-09-14T08:32:35+08:00', MAIL_2_DETAIL),
               ('p015', 13, 'mail', 'mail_inbox', 'mail.message.mail_002',
                '2026-09-14T08:33:05+08:00', INBOX),
               ('p018', 16, 'calendar', 'calendar_list', 'calendar.event.cal_001',
                '2026-09-14T08:33:50+08:00', CALENDAR_LIST))


def fixture_record(app, entity_type, ref, data):
    return {'app_id': app, 'entity_type': entity_type, 'semantic_ref': ref,
            'data': copy.deepcopy(data)}


def packet(packet_id, sequence, source, view, ref, observed, expected, replays=()):
    assertions = {'calendar_list': ['day_label', 'event_title', 'event_time',
                                    'event_description'],
                  'event_editor': ['title', 'location', 'description', 'start_at', 'end_at'],
                  'mail_inbox': list(INBOX), 'mail_detail': ['subject', 'from', 'body']}[view]
    capture = {'surface': 'app_screen', 'app_id': source, 'view_id': view, 'subject_ref': ref,
               'visible_assertions': assertions, 'expected_visible': copy.deepcopy(expected),
               'frames': [{'frame_id': f'{packet_id}_f1'}], 'timeout_seconds': 30}
    if source == 'calendar':
        capture['navigation_mode'] = 'direct_event'
    compiled = {'packet_id': packet_id, 'sequence': sequence, 'tag': 'gui', 'source': source,
                'observed_at': observed, 'delivered_at': observed,
                'text_event': f'{packet_id} summary', 'capture': capture}
    if replays:
        compiled['history_replays'] = [dict(replay) for replay in replays]
    return compiled


def private_plan():
    records = [fixture_record('calendar', 'events', 'calendar.event.cal_001', CALENDAR),
               fixture_record('mail', 'mails', 'mail.message.mail_001', MAIL_1),
               fixture_record('mail', 'mails', 'mail.message.mail_002', MAIL_2),
               fixture_record('clock', 'alarms', 'clock.alarm.alarm_101', ALARM)]
    replays = {'p010': [{'semantic_ref': 'mail.message.mail_001',
                         'effective_at': MAIL_1_EFFECTIVE, 'set': {'status': 'read'}}],
               'p013': [{'semantic_ref': 'mail.message.mail_002',
                         'effective_at': MAIL_2_EFFECTIVE, 'set': {'status': 'read'}}]}
    packets = []
    for packet_id, sequence, source, view, ref, observed, expected in GUI_PACKETS:
        packets.append(packet(packet_id, sequence, source, view, ref, observed, expected,
                              replays.get(packet_id, ())))
    assets = {'gui_plan': ('pb-gui-bridge.plan.v1', '1' * 64),
              'backend_plan': ('pb-backend-plan.v1', '2' * 64),
              'backend_receipt': ('pb-backend-apply-receipt.v1', '3' * 64),
              'gate_plan': ('pb-gui-capture-gate.v1', '4' * 64)}
    return {
        'schema_version': 'pb-gui-private-capture-plan.v1',
        'generated_at': '2026-09-14T08:40:00+00:00', 'command': 'compile', 'status': 'compiled',
        'episode_scope': {'cluster_id': 'meeting-preparation-001', 'episode_id': 'ep_0004',
                          'passive_id': 'p002', 'world_variant_id': 'w001', 'track': 'mixed_text',
                          'decision_point': DECISION_POINT, 'timezone': 'Asia/Shanghai'},
        'cluster_id': 'meeting-preparation-001', 'episode_id': 'ep_0004', 'passive_id': 'p002',
        'world_variant_id': 'w001', 'baseline_ref': 'init_state',
        'device_timezone': 'Asia/Shanghai',
        'pb_clock_binding': {'observation_start': OBSERVATION_START,
                             'decision_point': DECISION_POINT, 'timezone': 'Asia/Shanghai',
                             'source': 'PB history/cluster'},
        'initial_simulated_time': OBSERVATION_START,
        'source_assets': {name: {'schema_version': schema, 'sha256': sha}
                          for name, (schema, sha) in assets.items()},
        'fixture': {'episode_scope': 'ep_0004', 'common_records': [], 'world_deltas': [],
                    'ku_records': records},
        'packets': packets, 'unsupported_packets': [],
        'counts': {'packets': 7, 'unsupported_packets': 0, 'fixture_records': 4, 'frames': 7,
                   'history_replays': 2},
        'checks': {'gate_status': 'ready_for_device_capture',
                   'gate_capture_state': 'pending_device_capture', 'receipt_status': 'ok',
                   'receipt_device_applied': True, 'hashes_verified': 4, 'scope_matches': True,
                   'gui_plan_sha256': '1' * 64, 'backend_plan_sha256': '2' * 64,
                   'backend_receipt_sha256': '3' * 64, 'gate_plan_sha256': '4' * 64,
                   'packets_executable': 7, 'packets_unsupported': 0, 'history_replays': 2,
                   'images_read': 0, 'images_copied': 0, 'device_writes': 0},
        'eof_at': DECISION_POINT,
    }


class PrivatePlanCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, plan):
        plan_path = self.root / 'capture-plan.json'
        plan_path.write_text(json.dumps(plan, ensure_ascii=False) + '\n', encoding='utf8')
        output = self.root / 'capture'
        fake_run = mock.Mock(return_value={'status': 'capture_complete',
                                           'failure_reason': None})
        stdout = io.StringIO()
        with mock.patch.object(gui_capture, 'run', fake_run), \
                contextlib.redirect_stdout(stdout):
            code = gui_capture.main([str(plan_path), '--instance-id', 'i1',
                                     '--output', str(output)])
        return code, fake_run, stdout.getvalue()

    def test_private_plan_is_validated_and_enters_run(self):
        plan = private_plan()
        code, fake_run, stdout = self.invoke(plan)
        self.assertEqual(code, 0)
        fake_run.assert_called_once()
        passed = fake_run.call_args.args[0]
        self.assertEqual(passed['schema_version'], gui_capture.PRIVATE_PLAN_SCHEMA)
        self.assertEqual(passed['pb_clock_binding'], plan['pb_clock_binding'])
        self.assertEqual([item['packet_id'] for item in passed['packets']],
                         [item[0] for item in GUI_PACKETS])
        self.assertEqual(json.loads(stdout)['status'], 'capture_complete')

    def test_bad_schema_and_missing_and_unknown_fields_fail_closed(self):
        cases = {}
        bad_schema = private_plan()
        bad_schema['schema_version'] = 'pb-gui-private-capture-plan.v2'
        cases['schema'] = bad_schema
        missing = private_plan()
        del missing['pb_clock_binding']
        cases['missing'] = missing
        unknown = private_plan()
        unknown['screenshot_ref'] = 'images/p003.png'
        cases['unknown'] = unknown
        nested_unknown = private_plan()
        nested_unknown['packets'][0]['capture']['navigation_hint'] = 'tap-anything'
        cases['nested unknown'] = nested_unknown
        for name, plan in cases.items():
            plan_path = self.root / f'{name}.json'
            plan_path.write_text(json.dumps(plan, ensure_ascii=False) + '\n', encoding='utf8')
            fake_run = mock.Mock(return_value={'status': 'capture_complete',
                                               'failure_reason': None})
            with self.subTest(name=name), mock.patch.object(gui_capture, 'run', fake_run):
                with self.assertRaises(ValueError) as context:
                    gui_capture.main([str(plan_path), '--instance-id', 'i1',
                                      '--output', str(self.root / 'capture')])
            fake_run.assert_not_called()
            self.assertTrue(str(context.exception))

    def test_path_leak_in_fixture_value_fails_closed(self):
        plan = private_plan()
        plan['fixture']['ku_records'][1]['data']['headers']['to'] = '/tmp/private-gt.json'
        with self.assertRaises(ValueError) as context:
            self.invoke(plan)[0]
        self.assertIn('absolute paths', str(context.exception))

    def test_legacy_pilot_author_keeps_the_previous_compile_path(self):
        code, fake_run, _ = self.invoke_file(ROOT / 'datasets/gui-mail-pilot/author.json')
        self.assertEqual(code, 0)
        passed = fake_run.call_args.args[0]
        self.assertEqual(passed['schema_version'], '1.0')
        self.assertNotIn('pb_clock_binding', passed)

    def invoke_file(self, path):
        fake_run = mock.Mock(return_value={'status': 'capture_complete',
                                           'failure_reason': None})
        with mock.patch.object(gui_capture, 'run', fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            code = gui_capture.main([str(path), '--instance-id', 'i1',
                                     '--output', str(self.root / 'capture')])
        return code, fake_run, ''


if __name__ == '__main__':
    unittest.main()
