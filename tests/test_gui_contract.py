import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('gui_contract', Path(__file__).parents[1] / 'scripts/gui_contract.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from gui_formal import compile_formal_plan


class ContractTest(unittest.TestCase):
    def test_capture_schema_is_parseable(self):
        schema = json.loads((Path(__file__).parents[1] / 'contracts/gui-capture.schema.json').read_text())
        self.assertIn('capture', schema['properties']['packets']['items']['properties'])

    def make(self, directory, *, app='dudu'):
        fixture = {'episode_scope': 'episode-1', 'common_records': [
            {'app_id': app, 'entity_type': 'popup', 'semantic_ref': 'notice-1',
             'data': {'kind': 'promotion', 'title': 'Notice', 'message': 'Readable message'}}], 'world_deltas': []}
        author = {'episode_id': 'episode-1', 'baseline_ref': 'init_state', 'device_timezone': 'Asia/Shanghai', 'fixture_ref': 'fixture.json',
                  'packets': [{'packet_id': 'p001', 'sequence': 1, 'type': 'gui', 'source': app,
                               'observed_at': '2026-09-24T10:00:00+08:00', 'text_projection': 'Notice: Readable message',
                               'capture': {'surface': 'app_screen', 'app_id': app, 'view_id': 'popup',
                                           'subject_ref': 'notice-1', 'frame_id': 'p001_f1',
                                           'visible_assertions': ['title', 'message']}}],
                  'eof_at': '2026-09-24T10:01:00+08:00'}
        (directory / 'fixture.json').write_text(json.dumps(fixture))
        (directory / 'author.json').write_text(json.dumps(author))
        return author

    def test_compile_and_reject_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            author = self.make(directory)
            plan = module.compile_capture_plan(directory / 'author.json')
            self.assertEqual(plan['packets'][0]['capture']['expected_visible']['message'], 'Readable message')
            self.assertEqual(len(plan['source_assets']['fixture']), 64)
            for change in ({'source': 'calendar'}, {'observed_at': '2026-09-24T10:00:00'},
                           {'screenshot_ref': 'fabricated.png'}, {'world_id': 'w001'}):
                invalid = json.loads(json.dumps(author))
                invalid['packets'][0].update(change)
                (directory / 'author.json').write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    module.compile_capture_plan(directory / 'author.json')
            author['packets'][0]['capture']['frames'] = [{'frame_id': 'extra'}]
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'exactly one frame'):
                module.compile_capture_plan(directory / 'author.json')
            del author['packets'][0]['capture']['frames']
            author['device_timezone'] = 'UTC'
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'timezone conflicts'):
                module.compile_capture_plan(directory / 'author.json')
            author['device_timezone'] = 'Asia/Shanghai'
            author['fixture_ref'] = '../outside.json'
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'escapes'):
                module.compile_capture_plan(directory / 'author.json')

    def test_counterfactual_public_equivalence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            author = self.make(directory)
            plan = module.compile_capture_plan(directory / 'author.json')
            module.assert_public_equivalent([plan, plan])
            other = json.loads(json.dumps(plan))
            other['packets'][0]['capture']['expected_visible']['title'] = 'Different'
            with self.assertRaises(ValueError):
                module.assert_public_equivalent([plan, other])
            other = json.loads(json.dumps(plan))
            other['fixture']['common_records'][0]['data']['kind'] = 'different visible context'
            with self.assertRaises(ValueError):
                module.assert_public_equivalent([plan, other])

    def test_reject_record_routed_to_wrong_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.make(directory)
            fixture_path = directory / 'fixture.json'
            fixture = json.loads(fixture_path.read_text())
            fixture['common_records'][0]['app_id'] = 'calendar'
            fixture_path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'extension App'):
                module.compile_capture_plan(directory / 'author.json')

    def test_two_packet_detail_pilot(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-capture-pilot'
        a = module.compile_capture_plan(sample / 'author-a.json')
        b = module.compile_capture_plan(sample / 'author-b.json')
        module.assert_public_equivalent([a, b])
        self.assertEqual(a['packets'][1]['capture']['view_id'], 'article_detail')
        self.assertNotEqual(a['source_assets']['fixture'], b['source_assets']['fixture'])

    def test_calendar_pilot_compiles_only_supported_event(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-calendar-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['start_at'], '10:30 AM')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture = json.loads((sample / 'fixture.json').read_text())
            author = json.loads((sample / 'author.json').read_text())
            fixture['ku_records'][0]['data']['title'] = '非ASCII标题'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'ASCII'):
                module.compile_capture_plan(directory / 'author.json')

    def test_mail_pilot_is_app_screen_not_notification(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-mail-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['body'],
                         'Project Review has a new meeting time.')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'fixture.json').write_bytes((sample / 'fixture.json').read_bytes())
            author['packets'][0]['capture']['surface'] = 'system_surface'
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'app_screen'):
                module.compile_capture_plan(directory / 'author.json')

    def test_clock_pilot_requires_full_card_and_valid_time(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-clock-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible'],
                         {'label': 'GuiPilotAlarm', 'time': '10:45AM', 'enabled': 'false'})
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture = json.loads((sample / 'fixture.json').read_text())
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'author.json').write_text(json.dumps(author))
            fixture['ku_records'][0]['data']['time'] = '25:45'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'time'):
                module.compile_capture_plan(directory / 'author.json')
            fixture['ku_records'][0]['data']['time'] = '10:45'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            author['packets'][0]['capture']['visible_assertions'] = ['label']
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'requires label'):
                module.compile_capture_plan(directory / 'author.json')

    def test_contacts_pilot_distinguishes_same_name_records(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-contacts-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['phone'], '5550112')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture = json.loads((sample / 'fixture.json').read_text())
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'author.json').write_text(json.dumps(author))
            fixture['ku_records'][1]['data']['phone'] = 'same-phone'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'Contacts pilot'):
                module.compile_capture_plan(directory / 'author.json')
            fixture['ku_records'][1]['data']['phone'] = fixture['ku_records'][0]['data']['phone']
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'navigation identity'):
                module.compile_capture_plan(directory / 'author.json')

    def test_contacts_email_requires_visible_assertion(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-contacts-email-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible']['email'], 'target@example.test')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'fixture.json').write_bytes((sample / 'fixture.json').read_bytes())
            author['packets'][0]['capture']['visible_assertions'].remove('email')
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'every displayed field'):
                module.compile_capture_plan(directory / 'author.json')

    def test_files_pilot_limits_path_and_hashes_real_content(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-files-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        module.assert_public_equivalent([plan, module.compile_capture_plan(sample / 'author-b.json')])
        self.assertEqual(plan['packets'][0]['capture']['expected_visible'],
                         {'directory': 'Download', 'name': 'GuiPilotProof.txt'})
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture = json.loads((sample / 'fixture.json').read_text())
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'author.json').write_text(json.dumps(author))
            fixture['ku_records'][0]['data']['path'] = 'Download/../secret.txt'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'Files pilot'):
                module.compile_capture_plan(directory / 'author.json')

        other = json.loads(json.dumps(plan))
        other['fixture']['ku_records'][0]['data']['content'] = 'GuiPilotContentV2'
        module.assert_public_equivalent([plan, other])
        other['fixture']['ku_records'][0]['data']['content'] += ' extra'
        with self.assertRaisesRegex(ValueError, 'leak'):
            module.assert_public_equivalent([plan, other])
        other['fixture']['ku_records'][0]['data']['content'] = 'GuiPilotContentV2'
        other['fixture']['ku_records'][0]['data']['modified_at'] = '2026-09-25T09:00:00+08:00'
        with self.assertRaisesRegex(ValueError, 'leak'):
            module.assert_public_equivalent([plan, other])

    def test_docreader_requires_content_and_rejects_unreadable_pilot_text(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-docreader-pilot'
        plan = module.compile_capture_plan(sample / 'author.json')
        self.assertEqual(plan['packets'][0]['capture']['expected_visible'], {'content': 'GuiPilotContentV1'})
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture = json.loads((sample / 'fixture.json').read_text())
            author = json.loads((sample / 'author.json').read_text())
            (directory / 'author.json').write_text(json.dumps(author))
            fixture['ku_records'][0]['data']['content'] = 'line one\nline two'
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'single-line ASCII'):
                module.compile_capture_plan(directory / 'author.json')

    def test_formal_bundle_compiles_legal_opposite_worlds(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-files-formal-pilot'
        a = compile_formal_plan(sample / 'manifest-a.json')
        b = compile_formal_plan(sample / 'manifest-b.json')
        module.assert_public_equivalent([a, b])
        self.assertEqual(a['formal']['oracle']['expected_need_set'], [])
        self.assertNotEqual(b['formal']['oracle']['expected_need_set'], [])
        self.assertEqual(len(a['source_assets']), 7)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ('global-base.json', 'cluster-base.json', 'world-b.json', 'passive.json',
                         'compatibility.json', 'oracle-b.json', 'manifest-b.json'):
                (directory / name).write_bytes((sample / name).read_bytes())
            variant = json.loads((directory / 'world-b.json').read_text())
            variant['allowed_changed_fields'] = []
            (directory / 'world-b.json').write_text(json.dumps(variant))
            with self.assertRaisesRegex(ValueError, 'declared changed fields'):
                compile_formal_plan(directory / 'manifest-b.json')
            variant['patches'].append({'op': 'update', 'app_id': 'files', 'semantic_ref': 'file-01',
                                       'set': {'content': 'GuiPilotContentV1'}})
            variant['allowed_changed_fields'] = ['files.file-01.content']
            (directory / 'world-b.json').write_text(json.dumps(variant))
            with self.assertRaisesRegex(ValueError, 'declared changed fields'):
                compile_formal_plan(directory / 'manifest-b.json')
            (directory / 'world-b.json').write_bytes((sample / 'world-b.json').read_bytes())
            oracle = json.loads((directory / 'oracle-b.json').read_text())
            oracle['expected_need_set'] = []
            (directory / 'oracle-b.json').write_text(json.dumps(oracle))
            with self.assertRaisesRegex(ValueError, 'Need Set'):
                compile_formal_plan(directory / 'manifest-b.json')
            manifest = json.loads((directory / 'manifest-b.json').read_text())
            manifest['oracle_ref'] = '../oracle.json'
            (directory / 'manifest-b.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'escapes'):
                compile_formal_plan(directory / 'manifest-b.json')

    def test_appointment_formal_mail_same_calendar_need_opposite(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-appointment-formal-pilot'
        a = compile_formal_plan(sample / 'manifest-a.json')
        b = compile_formal_plan(sample / 'manifest-b.json')
        module.assert_public_equivalent([a, b])
        self.assertEqual(a['formal']['oracle']['expected_need_set'], [])
        self.assertEqual(b['formal']['oracle']['expected_need_set'][0]['type'], 'update_calendar_event')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ('global-base.json', 'cluster-base.json', 'world-b.json', 'passive.json',
                         'compatibility.json', 'oracle-b.json', 'manifest-b.json'):
                (directory / name).write_bytes((sample / name).read_bytes())
            cluster = json.loads((directory / 'cluster-base.json').read_text())
            cluster['records'][1]['data']['headers']['sender'] = 'stranger@example.com'
            (directory / 'cluster-base.json').write_text(json.dumps(cluster))
            with self.assertRaisesRegex(ValueError, 'goal time or sender'):
                compile_formal_plan(directory / 'manifest-b.json')

    def test_contact_formal_messages_public_same_need_opposite(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-contact-update-formal-pilot'
        a = compile_formal_plan(sample / 'manifest-a.json')
        b = compile_formal_plan(sample / 'manifest-b.json')
        module.assert_public_equivalent([a, b])
        self.assertEqual(a['formal']['oracle']['expected_need_set'], [])
        self.assertEqual(b['formal']['oracle']['expected_need_set'], [
            {'type': 'update_contact_phone', 'target': 'contact-01', 'phone': '13800138000'}])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ('global-base.json', 'cluster-base.json', 'world-a.json', 'passive.json',
                         'compatibility.json', 'oracle-a.json', 'manifest-a.json'):
                (directory / name).write_bytes((sample / name).read_bytes())
            cluster = json.loads((directory / 'cluster-base.json').read_text())
            cluster['goal']['source_sender'] = 'OtherSender'
            (directory / 'cluster-base.json').write_text(json.dumps(cluster))
            with self.assertRaisesRegex(ValueError, 'source label or body'):
                compile_formal_plan(directory / 'manifest-a.json')

    def test_reminder_formal_clock_alarm_opposite_need(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-reminder-formal-pilot'
        a = compile_formal_plan(sample / 'manifest-a.json')
        b = compile_formal_plan(sample / 'manifest-b.json')
        module.assert_public_equivalent([a, b])
        self.assertEqual(a['formal']['oracle']['expected_need_set'], [])
        self.assertEqual(b['formal']['oracle']['expected_need_set'], [
            {'type': 'update_clock_alarm', 'target': 'alarm-01', 'time': '14:30', 'enabled': True}])
        self.assertEqual([p['source'] for p in a['packets']], ['mail'])
        self.assertEqual([p['source'] for p in b['packets']], ['mail'])
        self.assertEqual(a['public_rule'], b['public_rule'])

        def records(plan):
            return {record['semantic_ref']: record['data'] for record in plan['fixture']['ku_records']}

        self.assertEqual(records(a)['alarm-01'], {'label': 'GuiReminderMeeting', 'time': '14:30', 'enabled': True})
        self.assertEqual(records(b)['alarm-01'], {'label': 'GuiReminderMeeting', 'time': '14:30', 'enabled': False})
        self.assertEqual(records(a)['mail-01'], records(b)['mail-01'])

    def test_reminder_formal_rejects_illegal_time_source_and_world_change(self):
        sample = Path(__file__).parents[1] / 'datasets/gui-reminder-formal-pilot'
        names = ('global-base.json', 'cluster-base.json', 'world-b.json', 'passive.json',
                 'compatibility.json', 'oracle-b.json', 'manifest-b.json')

        def stage(directory):
            for name in names:
                (directory / name).write_bytes((sample / name).read_bytes())

        def load(directory, name):
            return json.loads((directory / name).read_text())

        def save(directory, name, payload):
            (directory / name).write_text(json.dumps(payload))

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['goal']['approved_end_at'] = '2026-09-25T14:00:00+08:00'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'reminder goal time'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            manifest = load(directory, 'manifest-b.json')
            manifest['eof_at'] = '2026-09-25T14:30:00+08:00'
            save(directory, 'manifest-b.json', manifest)
            with self.assertRaisesRegex(ValueError, 'before the target reminder'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['goal']['approved_start_at'] = '2026-09-25T07:00:00+00:00'
            cluster['goal']['approved_end_at'] = '2026-09-25T07:30:00+00:00'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'device time zone'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['goal']['approved_start_at'] = '2026-09-25T00:10:00+08:00'
            cluster['goal']['approved_end_at'] = '2026-09-25T00:40:00+08:00'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'activity date'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['goal']['approved_reminder_minutes_before'] = True
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'reminder goal time'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['goal']['declared_channel'] = 'calendar+clock'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'single-channel'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['records'][0]['data']['headers']['sender'] = 'stranger@example.com'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'source mail'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['records'][0]['data']['body'] = 'GuiReminderMeeting moved to another slot.'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'source mail'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            cluster = load(directory, 'cluster-base.json')
            cluster['records'][1]['data']['label'] = 'OtherAlarm'
            save(directory, 'cluster-base.json', cluster)
            with self.assertRaisesRegex(ValueError, 'alarm label'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            world = load(directory, 'world-b.json')
            world['patches'] = [{'op': 'update', 'app_id': 'mail', 'semantic_ref': 'mail-01',
                                 'set': {'body': 'GuiReminderMeeting moved to another slot.'}}]
            world['allowed_changed_fields'] = ['mail.mail-01.body']
            save(directory, 'world-b.json', world)
            with self.assertRaisesRegex(ValueError, 'private target alarm'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            world = load(directory, 'world-b.json')
            world['patches'][0]['set'] = {'label': 'OtherAlarm'}
            world['allowed_changed_fields'] = ['clock.alarm-01.label']
            save(directory, 'world-b.json', world)
            with self.assertRaisesRegex(ValueError, 'time or enabled'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            passive = load(directory, 'passive.json')
            passive['packets'].append({'packet_id': 'p002', 'sequence': 2, 'type': 'gui', 'source': 'clock',
                                       'observed_at': '2026-09-25T10:00:00+08:00',
                                       'text_projection': 'Clock alarm card.',
                                       'capture': {'surface': 'app_screen', 'app_id': 'clock',
                                                   'view_id': 'alarm_card', 'subject_ref': 'alarm-01',
                                                   'frame_id': 'p002_f1',
                                                   'visible_assertions': ['label', 'time', 'enabled']}})
            save(directory, 'passive.json', passive)
            with self.assertRaisesRegex(ValueError, 'private until EOF'):
                compile_formal_plan(directory / 'manifest-b.json')

            stage(directory)
            oracle = load(directory, 'oracle-b.json')
            oracle['expected_need_set'] = []
            save(directory, 'oracle-b.json', oracle)
            with self.assertRaisesRegex(ValueError, 'Need Set'):
                compile_formal_plan(directory / 'manifest-b.json')

    def test_reject_unrenderable_popup_and_frame_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            author = self.make(directory)
            fixture = json.loads((directory / 'fixture.json').read_text())
            fixture['common_records'].append({**fixture['common_records'][0], 'semantic_ref': 'notice-2'})
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, 'one popup'):
                module.compile_capture_plan(directory / 'author.json')
            fixture['common_records'].pop()
            (directory / 'fixture.json').write_text(json.dumps(fixture))
            author['packets'][0]['capture']['frames'] = [{'frame_id': 'f1'}, {'frame_id': 'f2'}]
            del author['packets'][0]['capture']['frame_id']
            (directory / 'author.json').write_text(json.dumps(author))
            with self.assertRaisesRegex(ValueError, 'multi-frame'):
                module.compile_capture_plan(directory / 'author.json')


if __name__ == '__main__':
    unittest.main()
