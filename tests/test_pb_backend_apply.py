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
sys.path.insert(0, str(ROOT / 'tests'))

import gui_calendar
import gui_clock
import gui_contacts
import gui_mail
import pb_backend_apply as applier
import pb_backend_plan as pb
from test_pb_backend_plan import (CONTACT_EFFECTIVE, CONTACT_FIELDS, add_contacts_scene,
                                  build_tree)

PB_ROOT = ROOT.parent / '相关文件/ProactiveBench/ProactiveBench'
SERIAL = 'emulator-5554'
TZ = 'Asia/Shanghai'
DEFAULT_RUNNER = object()
CALENDAR_EFFECTIVE = '2026-09-14T08:31:00+08:00'
MAIL_EFFECTIVE = '2026-09-14T08:31:45+08:00'

ADAPTER_FUNCTIONS = (
    (gui_calendar, 'seed_event', 'calendar.seed_event'),
    (gui_calendar, 'read_event', 'calendar.read_event'),
    (gui_calendar, 'assert_absent', 'calendar.assert_absent'),
    (gui_calendar, 'apply_history_change', 'calendar.apply_history_change'),
    (gui_mail, 'seed_mail', 'mail.seed_mail'),
    (gui_mail, 'read_mail', 'mail.read_mail'),
    (gui_mail, 'assert_absent', 'mail.assert_absent'),
    (gui_mail, 'apply_history_change', 'mail.apply_history_change'),
    (gui_clock, 'seed_alarm', 'clock.seed_alarm'),
    (gui_clock, 'read_alarm', 'clock.read_alarm'),
    (gui_clock, 'assert_absent', 'clock.assert_absent'),
    (gui_contacts, 'seed_contact', 'contacts.seed_contact'),
    (gui_contacts, 'read_contact', 'contacts.read_contact'),
    (gui_contacts, 'assert_absent', 'contacts.assert_absent'),
)


class FakeAdb:
    def __init__(self):
        self.calls = []

    def __call__(self, serial, *args, **kwargs):
        self.calls.append((serial, args))
        return b''


class AdapterMocks:
    """Replace the native seed/read/absent adapter functions with recorders."""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.calls = []
        self.patches = []

    def _fake(self, label):
        def function(*args, **kwargs):
            self.calls.append((label, args))
            if label in self.fail_on:
                raise RuntimeError(f'{label} exploded')
            return {'mock': label}
        return function

    def install(self, testcase):
        for module, name, label in ADAPTER_FUNCTIONS:
            patch = mock.patch.object(module, name, self._fake(label))
            patch.start()
            testcase.addCleanup(patch.stop)
        return self

    @property
    def labels(self):
        return [label for label, _ in self.calls]


class BackendApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_tree(self.root)
        self.plan = pb.compile_plan(self.root, 'ep_0001')
        self.plan_path = self.write_plan(self.plan)
        self.receipt_path = self.root / 'receipt.json'
        self.adb = FakeAdb()
        self.addCleanup(self.tmp.cleanup)

    def write_plan(self, plan, name='plan.json'):
        path = self.root / name
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
        return path

    def run_main(self, argv, adb_runner=DEFAULT_RUNNER):
        runner = self.adb if adb_runner is DEFAULT_RUNNER else adb_runner
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = applier.main(argv, adb_runner=runner)
        return code, stdout.getvalue(), stderr.getvalue()

    def apply_argv(self, plan_path=None, receipt=None, extra=(), serial=SERIAL, timezone=TZ):
        return ['apply', '--plan', str(plan_path or self.plan_path), '--serial', serial,
                '--device-timezone', timezone, '--receipt', str(receipt or self.receipt_path), *extra]

    def with_calendar_history(self, change=None, seed_reminder=-1, readback_reminder=10,
                              history_changes=None, plan=None):
        plan = copy.deepcopy(plan or self.plan)
        record = plan['sources']['calendar']['seed_records'][0]
        record['seed']['data']['reminder_minutes_before'] = seed_reminder
        record['readback']['data']['reminder_minutes_before'] = readback_reminder
        if history_changes is None:
            if change is None:
                change = {'effective_at': CALENDAR_EFFECTIVE,
                          'set': {'calendar_reminder_minutes': [10, -1, -1],
                                  'updated_at': CALENDAR_EFFECTIVE}}
            history_changes = [change]
        record['history_changes'] = list(history_changes)
        return plan

    def test_preflight_lists_items_without_device(self):
        mocks = AdapterMocks().install(self)
        code, out, _ = self.run_main(['preflight', '--plan', str(self.plan_path),
                                      '--pb-root', str(self.root)])
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertEqual(summary['schema_version'], 'pb-backend-preflight.v1')
        self.assertFalse(summary['device_write'])
        self.assertFalse(summary['adb_required'])
        self.assertEqual(summary['ordering'], ['calendar', 'mail', 'clock'])
        self.assertEqual([(item['source'], item['seed_function'], item['readback_function'])
                          for item in summary['items']],
                         [('calendar', 'seed_event', 'read_event'),
                          ('mail', 'seed_mail', 'read_mail'),
                          ('clock', 'seed_alarm', 'read_alarm')])
        self.assertEqual([item['semantic_ref'] for item in summary['items']],
                         ['calendar.event.cal_001', 'mail.message.mail_001', 'clock.alarm.alarm_101'])
        self.assertEqual(summary['counts']['by_source'], {'calendar': 1, 'mail': 1, 'clock': 1})
        self.assertFalse(summary['device_apply_ready'])
        self.assertTrue(any('allow-device-apply' in blocker for blocker in summary['blockers']))
        self.assertEqual([entry['source'] for entry in summary['unsupported_not_executed']],
                         ['device', 'messages'])
        self.assertEqual(mocks.calls, [])
        self.assertEqual(self.adb.calls, [])
        self.assertFalse(self.receipt_path.exists())

        allowed, out, _ = self.run_main(['preflight', '--plan', str(self.plan_path),
                                         '--allow-device-apply'])
        self.assertEqual(allowed, 0)
        self.assertTrue(json.loads(out)['device_apply_ready'])
        self.assertEqual(mocks.calls, [])

    def test_preflight_rejects_non_planned_completion_and_unsupported_source(self):
        cases = {}
        broken = copy.deepcopy(self.plan)
        broken['status'] = 'unsupported'
        cases['unsupported-status.json'] = broken
        broken = copy.deepcopy(self.plan)
        broken['device_applied'] = True
        cases['completion-claim.json'] = broken
        broken = copy.deepcopy(self.plan)
        broken['sources']['messages'] = {'source': 'messages', 'status': 'supported',
                                         'adapter': {'module': 'scripts/gui_messages.py',
                                                     'seed': 'seed_message', 'read': 'read_message'},
                                         'seed_records': []}
        cases['unknown-source.json'] = broken
        for name, payload in cases.items():
            with self.subTest(name):
                path = self.write_plan(payload, name)
                code, _, stderr = self.run_main(['preflight', '--plan', str(path)])
                self.assertEqual(code, 1)
                self.assertTrue(stderr.strip())
                self.assertFalse(self.receipt_path.exists())

    def test_apply_rejects_completion_claim_and_requires_allow_flag(self):
        mocks = AdapterMocks().install(self)
        claim = copy.deepcopy(self.plan)
        claim['device_applied'] = True
        claim_path = self.write_plan(claim, 'claim.json')
        for extra in ((), ('--allow-device-apply',)):
            code, _, _ = self.run_main(self.apply_argv(plan_path=claim_path, extra=extra))
            self.assertEqual(code, 1, 'a plan completion claim must never be re-applied')
        code, _, stderr = self.run_main(self.apply_argv(receipt=self.root / 'default.json'))
        self.assertEqual(code, 1)
        self.assertIn('--allow-device-apply', stderr)
        self.assertEqual(mocks.calls, [])
        self.assertFalse((self.root / 'default.json').exists())

    def test_apply_requires_serial_and_online_adb(self):
        mocks = AdapterMocks().install(self)
        missing_serial = self.apply_argv()
        missing_serial.remove('--serial')
        missing_serial.remove(SERIAL)
        with self.assertRaises(SystemExit) as raised:
            self.run_main(missing_serial)
        self.assertEqual(raised.exception.code, 2)

        code, _, _ = self.run_main(self.apply_argv(serial='   '))
        self.assertEqual(code, 1)

        with mock.patch.object(applier.shutil, 'which', return_value=None):
            code, _, stderr = self.run_main(self.apply_argv(extra=['--allow-device-apply']),
                                            adb_runner=None)
        self.assertEqual(code, 1)
        self.assertIn('adb', stderr)

        with mock.patch.object(applier.shutil, 'which', return_value='/usr/bin/adb'), \
                mock.patch.object(applier.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0, stdout=b'offline\n', stderr=b'')
            code, _, stderr = self.run_main(self.apply_argv(extra=['--allow-device-apply']),
                                            adb_runner=None)
        self.assertEqual(code, 1)
        self.assertIn('not online', stderr)
        self.assertEqual(mocks.calls, [])
        self.assertFalse(self.receipt_path.exists())

    def test_apply_rejects_timezone_mismatch(self):
        mocks = AdapterMocks().install(self)
        code, _, stderr = self.run_main(self.apply_argv(timezone='UTC', extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertIn('does not match plan timezone', stderr)
        self.assertEqual(mocks.calls, [])
        self.assertFalse(self.receipt_path.exists())

    def test_apply_aborts_on_first_failure(self):
        mocks = AdapterMocks(fail_on={'mail.seed_mail'}).install(self)
        code, _, stderr = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertIn('mail.seed_mail', stderr)
        self.assertEqual(mocks.labels,
                         ['calendar.assert_absent', 'mail.assert_absent', 'clock.assert_absent',
                          'calendar.seed_event', 'calendar.read_event', 'mail.seed_mail'])
        self.assertFalse(self.receipt_path.exists())

        self.receipt_path = self.root / 'readback-receipt.json'
        mocks = AdapterMocks(fail_on={'calendar.read_event'}).install(self)
        code, _, _ = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertEqual(mocks.labels,
                         ['calendar.assert_absent', 'mail.assert_absent', 'clock.assert_absent',
                          'calendar.seed_event', 'calendar.read_event'])
        self.assertNotIn('mail.seed_mail', mocks.labels)
        self.assertFalse(self.receipt_path.exists())

    def test_apply_success_receipt_binding(self):
        mocks = AdapterMocks().install(self)
        code, out, _ = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertTrue(summary['device_applied'])
        self.assertEqual(summary['serial'], SERIAL)

        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        self.assertEqual(receipt['schema_version'], 'pb-backend-apply-receipt.v1')
        self.assertEqual(receipt['status'], 'ok')
        self.assertTrue(receipt['device_applied'])
        binding = receipt['plan_binding']
        self.assertEqual(binding['schema_version'], pb.PLAN_SCHEMA)
        self.assertEqual(binding['plan_sha256'], pb.digest(self.plan_path))
        self.assertEqual(binding['status'], 'planned')
        self.assertFalse(binding['device_applied'])
        self.assertEqual(binding['episode_scope'], self.plan['episode_scope'])
        self.assertEqual(binding['world_hash'], self.plan['world']['world_hash'])
        self.assertEqual(binding['world_status'], 'new_bundle_not_applied_to_android')
        self.assertEqual(receipt['device'], {'serial': SERIAL, 'timezone': TZ, 'adb': 'adb'})
        self.assertEqual(receipt['ordering'], ['calendar', 'mail', 'clock'])
        self.assertEqual([record['semantic_ref'] for record in receipt['records']],
                         ['calendar.event.cal_001', 'mail.message.mail_001', 'clock.alarm.alarm_101'])
        calendar = receipt['records'][0]
        self.assertEqual(calendar['adapter'], {'module': 'scripts/gui_calendar.py',
                                               'seed': 'seed_event', 'read': 'read_event'})
        self.assertEqual(calendar['seed']['function'], 'seed_event')
        self.assertEqual(calendar['seed']['result'], {'mock': 'calendar.seed_event'})
        self.assertEqual(calendar['readback']['function'], 'read_event')
        self.assertEqual(calendar['readback']['result'], {'mock': 'calendar.read_event'})
        self.assertEqual(receipt['checks'],
                         {'seed_records': 3, 'seeds': 3, 'readbacks': 3, 'absence_guards': 3,
                          'failures': 0})
        self.assertEqual(mocks.labels,
                         ['calendar.assert_absent', 'mail.assert_absent', 'clock.assert_absent',
                          'calendar.seed_event', 'calendar.read_event',
                          'mail.seed_mail', 'mail.apply_history_change', 'mail.read_mail',
                          'clock.seed_alarm', 'clock.read_alarm'])
        seed_call = next(args for label, args in mocks.calls if label == 'calendar.seed_event')
        self.assertEqual(seed_call[1], SERIAL)
        self.assertEqual(seed_call[4], TZ)
        self.assertEqual(seed_call[5], self.plan['sources']['calendar']['seed_records'][0]['seed']['at'])
        self.assertEqual(pb.private_key_paths(receipt), [])
        self.assertEqual(pb.absolute_value_paths(receipt), [])
        self.assertNotIn('SECRET', json.dumps(receipt, ensure_ascii=False))
        pb.validate_plan(self.plan, self.root)

    def test_apply_replays_mail_history_before_readback(self):
        mocks = AdapterMocks().install(self)
        code, _, _ = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 0)
        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        records = {record['source']: record for record in receipt['records']}
        self.assertEqual(records['calendar']['history_replays'], [])
        self.assertEqual(records['clock']['history_replays'], [])
        plan_record = self.plan['sources']['mail']['seed_records'][0]
        replays = records['mail']['history_replays']
        self.assertEqual(len(replays), 1)
        self.assertEqual(replays[0]['effective_at'], plan_record['history_changes'][0]['effective_at'])
        self.assertEqual(replays[0]['set'], {'status': 'read'})
        self.assertEqual(replays[0]['function'], 'apply_history_change')
        self.assertEqual(replays[0]['result'], {'mock': 'mail.apply_history_change'})
        labels = mocks.labels
        self.assertLess(labels.index('mail.seed_mail'), labels.index('mail.apply_history_change'))
        self.assertLess(labels.index('mail.apply_history_change'), labels.index('mail.read_mail'))
        call = next(args for label, args in mocks.calls if label == 'mail.apply_history_change')
        self.assertEqual(call[1], SERIAL)
        self.assertEqual(call[2]['data']['status'], 'read')
        self.assertEqual(call[2]['data']['headers']['subject'],
                         plan_record['readback']['data']['headers']['subject'])

    def test_apply_replays_calendar_reminder_before_readback(self):
        plan = self.with_calendar_history()
        path = self.write_plan(plan, 'calendar-history.json')
        mocks = AdapterMocks().install(self)
        code, _, stderr = self.run_main(self.apply_argv(plan_path=path,
                                                        extra=['--allow-device-apply']))
        self.assertEqual(code, 0, stderr)
        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        calendar = receipt['records'][0]
        self.assertEqual(calendar['source'], 'calendar')
        self.assertEqual(calendar['history_replays'], [{
            'function': 'apply_history_change',
            'semantic_ref': 'calendar.event.cal_001',
            'effective_at': CALENDAR_EFFECTIVE,
            'set': {'calendar_reminder_minutes': [10, -1, -1], 'updated_at': CALENDAR_EFFECTIVE},
            'result': {'mock': 'calendar.apply_history_change'}}])
        labels = mocks.labels
        self.assertLess(labels.index('calendar.seed_event'),
                        labels.index('calendar.apply_history_change'))
        self.assertLess(labels.index('calendar.apply_history_change'),
                        labels.index('calendar.read_event'))
        self.assertLess(labels.index('calendar.read_event'), labels.index('mail.seed_mail'))
        call = next(args for label, args in mocks.calls if label == 'calendar.apply_history_change')
        self.assertEqual(call[1], SERIAL)
        self.assertEqual(call[2], plan['episode_scope']['episode_id'])
        self.assertEqual(call[3], {'semantic_ref': 'calendar.event.cal_001', 'entity_type': 'events',
                                   'data': {'calendar_reminder_minutes': [10, -1, -1],
                                            'updated_at': CALENDAR_EFFECTIVE}})
        self.assertEqual(call[4], CALENDAR_EFFECTIVE)
        readback_call = next(args for label, args in mocks.calls if label == 'calendar.read_event')
        self.assertEqual(readback_call[3]['data'],
                         plan['sources']['calendar']['seed_records'][0]['readback']['data'])
        self.assertEqual(readback_call[3]['data']['reminder_minutes_before'], 10)
        self.assertEqual(pb.private_key_paths(receipt), [])
        self.assertEqual(pb.absolute_value_paths(receipt), [])

    def test_apply_orders_multiple_calendar_replays_by_effective_at(self):
        first = {'effective_at': '2026-09-14T08:31:00+08:00',
                 'set': {'calendar_reminder_minutes': [5, -1, -1],
                         'updated_at': '2026-09-14T08:31:00+08:00'}}
        second = {'effective_at': '2026-09-14T08:32:00+08:00',
                  'set': {'calendar_reminder_minutes': [10, -1, -1],
                          'updated_at': '2026-09-14T08:32:00+08:00'}}
        plan = self.with_calendar_history(history_changes=[second, first])
        path = self.write_plan(plan, 'calendar-history-ordered.json')
        mocks = AdapterMocks().install(self)
        code, _, stderr = self.run_main(self.apply_argv(plan_path=path,
                                                        extra=['--allow-device-apply']))
        self.assertEqual(code, 0, stderr)
        receipt = json.loads(self.receipt_path.read_text(encoding='utf8'))
        replays = receipt['records'][0]['history_replays']
        self.assertEqual([replay['effective_at'] for replay in replays],
                         ['2026-09-14T08:31:00+08:00', '2026-09-14T08:32:00+08:00'])
        self.assertEqual([replay['set']['calendar_reminder_minutes'][0] for replay in replays], [5, 10])

    def test_preflight_validates_calendar_history_without_device(self):
        plan = self.with_calendar_history()
        path = self.write_plan(plan, 'calendar-history-preflight.json')
        mocks = AdapterMocks().install(self)
        code, out, _ = self.run_main(['preflight', '--plan', str(path), '--allow-device-apply'])
        self.assertEqual(code, 0)
        summary = json.loads(out)
        calendar = summary['items'][0]
        self.assertEqual(calendar['history_replays'], [{
            'function': 'apply_history_change',
            'semantic_ref': 'calendar.event.cal_001',
            'effective_at': CALENDAR_EFFECTIVE,
            'set': {'calendar_reminder_minutes': [10, -1, -1], 'updated_at': CALENDAR_EFFECTIVE}}])
        self.assertEqual(mocks.calls, [])
        self.assertEqual(self.adb.calls, [])
        self.assertEqual(pb.private_key_paths(summary), [])
        self.assertEqual(pb.absolute_value_paths(summary), [])

    def test_calendar_history_fail_closed_without_device(self):
        length = {'effective_at': CALENDAR_EFFECTIVE,
                  'set': {'calendar_reminder_minutes': [10, -1], 'updated_at': CALENDAR_EFFECTIVE}}
        strings = {'effective_at': CALENDAR_EFFECTIVE,
                   'set': {'calendar_reminder_minutes': ['10', -1, -1],
                           'updated_at': CALENDAR_EFFECTIVE}}
        boolean = {'effective_at': CALENDAR_EFFECTIVE,
                   'set': {'calendar_reminder_minutes': [True, -1, -1],
                           'updated_at': CALENDAR_EFFECTIVE}}
        unknown_field = {'effective_at': CALENDAR_EFFECTIVE,
                         'set': {'calendar_reminder_minutes': [10, -1, -1], 'calendar_color': 1,
                                 'updated_at': CALENDAR_EFFECTIVE}}
        wrong_field = {'effective_at': CALENDAR_EFFECTIVE,
                       'set': {'reminder_minutes_before': 10, 'updated_at': CALENDAR_EFFECTIVE}}
        missing_updated = {'effective_at': CALENDAR_EFFECTIVE,
                           'set': {'calendar_reminder_minutes': [10, -1, -1]}}
        drift = {'effective_at': CALENDAR_EFFECTIVE,
                 'set': {'calendar_reminder_minutes': [10, -1, -1],
                         'updated_at': '2026-09-14T08:32:00+08:00'}}
        naive_updated = {'effective_at': CALENDAR_EFFECTIVE,
                         'set': {'calendar_reminder_minutes': [10, -1, -1],
                                 'updated_at': '2026-09-14T08:31:00'}}
        missing_effective = {'set': {'calendar_reminder_minutes': [10, -1, -1],
                                     'updated_at': CALENDAR_EFFECTIVE}}
        naive_effective = {'effective_at': '2026-09-14T08:31:00',
                           'set': {'calendar_reminder_minutes': [10, -1, -1],
                                   'updated_at': '2026-09-14T08:31:00'}}
        after_readback = {'effective_at': '2026-09-14T08:36:00+08:00',
                          'set': {'calendar_reminder_minutes': [10, -1, -1],
                                  'updated_at': '2026-09-14T08:36:00+08:00'}}
        cases = [
            ('bad-length', {'change': length}, {}),
            ('bad-type-strings', {'change': strings}, {}),
            ('bad-type-bool', {'change': boolean}, {}),
            ('unknown-field', {'change': unknown_field}, {}),
            ('wrong-field', {'change': wrong_field}, {}),
            ('missing-updated-at', {'change': missing_updated}, {}),
            ('updated-at-drift', {'change': drift}, {}),
            ('updated-at-naive', {'change': naive_updated}, {}),
            ('missing-effective-at', {'change': missing_effective}, {}),
            ('effective-at-naive', {'change': naive_effective}, {}),
            ('effective-at-after-readback', {'change': after_readback}, {}),
            ('readback-reminder-not-changed', {}, {'readback_reminder': -1}),
            ('readback-changed-without-replay', {}, {'history_changes': []}),
        ]
        for name, change_kwargs, plan_kwargs in cases:
            with self.subTest(case=name):
                plan = self.with_calendar_history(**change_kwargs, **plan_kwargs)
                path = self.write_plan(plan, f'calendar-{name}.json')
                receipt = self.root / f'calendar-{name}-receipt.json'
                mocks = AdapterMocks().install(self)
                code, out, preflight_err = self.run_main(
                    ['preflight', '--plan', str(path), '--allow-device-apply'])
                self.assertEqual(code, 1, f'{name}: preflight must refuse')
                self.assertEqual(out, '')
                self.assertIn('history', preflight_err)
                code, _, stderr = self.run_main(self.apply_argv(plan_path=path, receipt=receipt,
                                                               extra=['--allow-device-apply']))
                self.assertEqual(code, 1, f'{name}: apply must refuse')
                self.assertIn('history', stderr)
                self.assertEqual(mocks.labels, [])
                self.assertEqual(self.adb.calls, [])
                self.assertFalse(receipt.exists())

    def test_unknown_history_source_fails_closed_without_device(self):
        plan = copy.deepcopy(self.plan)
        clock = plan['sources']['clock']['seed_records'][0]
        clock['history_changes'] = [{'effective_at': MAIL_EFFECTIVE, 'set': {'enabled': False}}]
        path = self.write_plan(plan, 'clock-history.json')
        mocks = AdapterMocks().install(self)
        code, _, stderr = self.run_main(self.apply_argv(plan_path=path,
                                                        extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertIn('history', stderr)
        self.assertEqual(mocks.labels, [])
        self.assertFalse(self.receipt_path.exists())

    def test_preflight_and_apply_refuse_gt_and_absolute_path_leaks(self):
        leaky = copy.deepcopy(self.plan)
        leaky['sources']['calendar']['seed_records'][0]['readback']['data']['title'] = \
            '/home/secret/ground_truth.json'
        with self.assertRaises(RuntimeError) as raised:
            applier.preflight_summary(leaky, allow_device_apply=True)
        self.assertIn('absolute paths', str(raised.exception))
        leaky = copy.deepcopy(self.plan)
        leaky['sources']['calendar']['seed_records'][0]['readback']['data']['need_set'] = ['secret']
        with self.assertRaises(RuntimeError) as raised:
            applier.preflight_summary(leaky, allow_device_apply=True)
        self.assertIn('private GT material', str(raised.exception))

        plan = copy.deepcopy(self.plan)
        plan['sources']['calendar']['seed_records'][0]['seed']['data']['title'] = '/root/gt.json'
        path = self.write_plan(plan, 'leak.json')
        mocks = AdapterMocks().install(self)
        for argv in (['preflight', '--plan', str(path)],
                     self.apply_argv(plan_path=path, extra=['--allow-device-apply'])):
            code, out, stderr = self.run_main(argv)
            self.assertEqual(code, 1)
            self.assertEqual(out, '')
            self.assertTrue(stderr.strip())
        self.assertEqual(mocks.labels, [])
        self.assertFalse(self.receipt_path.exists())

    def test_apply_rejects_unknown_mail_history(self):
        cases = [
            ('mail.message.mail_001', 'unknown-transition', {'set': {'status': 'archived'}}),
            ('mail.message.mail_001', 'unknown-field', {'set': {'body': 'rewritten'}}),
            ('calendar.event.cal_001', 'unknown-source', {'set': {'status': 'read'}}),
        ]
        for record_ref, name, change in cases:
            with self.subTest(case=name):
                plan = copy.deepcopy(self.plan)
                source = record_ref.split('.')[0]
                record = next(entry for entry in plan['sources'][source]['seed_records']
                              if entry['semantic_ref'] == record_ref)
                record['history_changes'] = [dict(change, effective_at='2026-09-14T08:35:00+08:00')]
                path = self.write_plan(plan, f'{name}.json')
                mocks = AdapterMocks().install(self)
                code, _, stderr = self.run_main(self.apply_argv(plan_path=path,
                                                               extra=['--allow-device-apply']))
                self.assertEqual(code, 1)
                self.assertIn('history', stderr)
                self.assertEqual(mocks.labels, [], 'an unknown history change must be refused before '
                                                   'any device write')
                self.assertFalse(self.receipt_path.exists())

    def test_apply_aborts_when_mail_history_replay_fails(self):
        mocks = AdapterMocks(fail_on={'mail.apply_history_change'}).install(self)
        code, _, stderr = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertIn('mail.apply_history_change', stderr)
        self.assertEqual(mocks.labels,
                         ['calendar.assert_absent', 'mail.assert_absent', 'clock.assert_absent',
                          'calendar.seed_event', 'calendar.read_event', 'mail.seed_mail',
                          'mail.apply_history_change'])
        self.assertNotIn('mail.read_mail', mocks.labels)
        self.assertNotIn('clock.seed_alarm', mocks.labels)
        self.assertFalse(self.receipt_path.exists())

    def test_apply_clears_stale_receipt_before_device_writes(self):
        self.receipt_path.write_text('{"device_applied": true, "stale": true}', encoding='utf8')
        mocks = AdapterMocks(fail_on={'calendar.seed_event'}).install(self)
        code, _, _ = self.run_main(self.apply_argv(extra=['--allow-device-apply']))
        self.assertEqual(code, 1)
        self.assertEqual(mocks.labels,
                         ['calendar.assert_absent', 'mail.assert_absent', 'clock.assert_absent',
                          'calendar.seed_event'])
        self.assertFalse(self.receipt_path.exists())

        self.receipt_path.write_text('{"device_applied": true, "stale": true}', encoding='utf8')
        with mock.patch.object(applier.shutil, 'which', return_value=None):
            code, _, _ = self.run_main(self.apply_argv(extra=['--allow-device-apply']), adb_runner=None)
        self.assertEqual(code, 1)
        self.assertTrue(self.receipt_path.exists(), 'a local adb failure must not erase a prior receipt')

    def contacts_plan(self, fields=None):
        add_contacts_scene(self.root, fields)
        plan = pb.compile_plan(self.root, 'ep_0001')
        return plan, self.write_plan(plan, 'contacts-plan.json')

    def test_contacts_seed_readback_and_absence_order(self):
        plan, plan_path = self.contacts_plan()
        self.assertIn('contacts', plan['sources'])
        mocks = AdapterMocks().install(self)
        receipt_path = self.root / 'contacts-receipt.json'
        code, _, stderr = self.run_main(self.apply_argv(
            plan_path, receipt=receipt_path, extra=['--allow-device-apply']))
        self.assertEqual(code, 0, stderr)
        labels = mocks.labels
        self.assertLess(labels.index('contacts.assert_absent'), labels.index('contacts.seed_contact'))
        self.assertLess(labels.index('contacts.seed_contact'), labels.index('contacts.read_contact'))
        receipt = json.loads(receipt_path.read_text(encoding='utf8'))
        self.assertEqual(receipt['ordering'], ['calendar', 'mail', 'clock', 'contacts'])
        contacts = [record for record in receipt['records'] if record['source'] == 'contacts']
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]['adapter'], {'module': 'scripts/gui_contacts.py',
                                                  'seed': 'seed_contact', 'read': 'read_contact'})
        self.assertEqual(contacts[0]['seed']['result'], {'mock': 'contacts.seed_contact'})
        self.assertEqual(contacts[0]['readback']['result'], {'mock': 'contacts.read_contact'})
        self.assertEqual(contacts[0]['history_replays'], [])
        seed_call = next(args for label, args in mocks.calls if label == 'contacts.seed_contact')
        self.assertEqual(seed_call[1], SERIAL)
        self.assertEqual(seed_call[2]['data'],
                         {'name': 'Ada Lovelace', 'phone': '+15550101', 'email': 'ada@example.test'})

    def test_contacts_history_change_is_refused_before_device(self):
        plan, _ = self.contacts_plan()
        record = plan['sources']['contacts']['seed_records'][0]
        record['history_changes'] = [{'effective_at': CONTACT_EFFECTIVE, 'set': {'name': 'Ada L.'}}]
        mocks = AdapterMocks().install(self)
        with self.assertRaises(ValueError):
            applier.validate_executable_plan(plan)
        self.assertEqual(mocks.calls, [])

    def test_contacts_preflight_lists_contacts_last(self):
        _, plan_path = self.contacts_plan()
        code, out, _ = self.run_main(['preflight', '--plan', str(plan_path),
                                      '--allow-device-apply'])
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertEqual(summary['ordering'], ['calendar', 'mail', 'clock', 'contacts'])
        contacts = [item for item in summary['items'] if item['source'] == 'contacts']
        self.assertEqual([(item['seed_function'], item['readback_function']) for item in contacts],
                         [('seed_contact', 'read_contact')])
        self.assertEqual(contacts[0]['readback_fields'], ['name', 'phone', 'email'])


@unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(), 'ProactiveBench tree not present')
class RealPlanPreflightTest(unittest.TestCase):
    def test_ep_0004_preflight(self):
        plan = pb.compile_plan(PB_ROOT, 'ep_0004')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = applier.main(['preflight', '--plan', str(path), '--pb-root', str(PB_ROOT)])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertFalse(summary['device_write'])
        refs = {item['semantic_ref'] for item in summary['items']}
        self.assertEqual(refs, {'calendar.event.cal_001', 'mail.message.mail_001',
                                'mail.message.mail_002', 'clock.alarm.alarm_101'})
        self.assertFalse(summary['device_apply_ready'])
        calendars = [item for item in summary['items'] if item['source'] == 'calendar']
        self.assertEqual(calendars[0]['history_replays'], [])


@unittest.skipUnless((PB_ROOT / 'benchmark.json').is_file(), 'ProactiveBench tree not present')
class RealPlanApplyTest(unittest.TestCase):
    """Real PB plans applied through mocked adapters (no device is touched)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def real_apply(self, episode_id):
        plan = pb.compile_plan(PB_ROOT, episode_id)
        plan_path = self.root / f'{episode_id}-plan.json'
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
        receipt_path = self.root / f'{episode_id}-receipt.json'
        mocks = AdapterMocks().install(self)
        adb = FakeAdb()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = applier.main(['apply', '--plan', str(plan_path), '--pb-root', str(PB_ROOT),
                                 '--serial', SERIAL, '--device-timezone', TZ,
                                 '--receipt', str(receipt_path), '--allow-device-apply'],
                                adb_runner=adb)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(adb.calls, [])
        receipt = json.loads(receipt_path.read_text(encoding='utf8'))
        self.assertEqual(pb.private_key_paths(receipt), [])
        self.assertEqual(pb.absolute_value_paths(receipt), [])
        return plan, receipt, mocks

    @staticmethod
    def records_by_source(receipt):
        grouped = {}
        for record in receipt['records']:
            grouped.setdefault(record['source'], []).append(record)
        return grouped

    def test_ep_0005_apply_replays_calendar_reminder(self):
        plan, receipt, mocks = self.real_apply('ep_0005')
        calendar_plan = plan['sources']['calendar']['seed_records'][0]
        self.assertEqual(calendar_plan['history_changes'][0]['set'],
                         {'calendar_reminder_minutes': [10, -1, -1],
                          'updated_at': '2026-09-14T08:31:00+08:00'})
        records = self.records_by_source(receipt)
        replays = records['calendar'][0]['history_replays']
        self.assertEqual(len(replays), 1)
        self.assertEqual(replays[0]['function'], 'apply_history_change')
        self.assertEqual(replays[0]['semantic_ref'], 'calendar.event.cal_001')
        self.assertEqual(replays[0]['effective_at'], '2026-09-14T08:31:00+08:00')
        self.assertEqual(replays[0]['set'], calendar_plan['history_changes'][0]['set'])
        self.assertEqual(replays[0]['result'], {'mock': 'calendar.apply_history_change'})
        self.assertEqual(records['calendar'][0]['readback']['result'], {'mock': 'calendar.read_event'})
        labels = mocks.labels
        self.assertLess(labels.index('calendar.seed_event'),
                        labels.index('calendar.apply_history_change'))
        self.assertLess(labels.index('calendar.apply_history_change'),
                        labels.index('calendar.read_event'))
        mail_replays = [replay for record in records['mail'] for replay in record['history_replays']]
        self.assertEqual([replay['effective_at'] for replay in mail_replays],
                         ['2026-09-14T08:31:45+08:00', '2026-09-14T08:32:35+08:00'])
        self.assertEqual(records['clock'][0]['history_replays'], [])

    def test_ep_0004_apply_keeps_the_mail_only_path(self):
        _, receipt, _ = self.real_apply('ep_0004')
        records = self.records_by_source(receipt)
        self.assertEqual(records['calendar'][0]['history_replays'], [])
        replays = [replay for record in records['mail'] for replay in record['history_replays']]
        self.assertEqual([replay['set'] for replay in replays], [{'status': 'read'}, {'status': 'read'}])
        self.assertEqual([replay['semantic_ref'] for replay in replays],
                         ['mail.message.mail_001', 'mail.message.mail_002'])
        self.assertEqual(records['clock'][0]['history_replays'], [])


if __name__ == '__main__':
    unittest.main()
