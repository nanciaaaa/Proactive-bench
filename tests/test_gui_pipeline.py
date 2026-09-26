import copy
import importlib.util
import json
import re
import shlex
import struct
import subprocess
import tempfile
import unittest
import unittest.mock
import zlib
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
import gui_calendar
import gui_capture
from gui_capture import apply_history_replays, assert_unobscured, png_size, read_device_env, read_device_wall_clock, suppress_unrelated_notifications, tap_unique_text, verify_seeded
from gui_calendar import PACKAGE as CALENDAR_PACKAGE, event_key, read_event, seed_event
from gui_clock import alarm_key, read_alarm, ready_alarm, seed_alarm
from gui_publish import publish, verify_release
from gui_pair_qa import differs_outside, rgb_rows
from gui_machine_qa import (canonical_text, fallback_attempts, field_language, frame_regions,
                            field_matches, preview_prefix_matches, status_time_equivalent)


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(b'\0\xff\x00\x00')) + chunk(b'IEND', b'')


def machine_capture(root, observed_at, android_time):
    """One-frame Calendar capture whose PNG/XML pass the machine pixel rule."""
    import hashlib
    capture = root / 'capture'
    (capture / 'images').mkdir(parents=True)
    (capture / 'ui').mkdir()
    raw = png()
    name = 'b' * 32
    (capture / 'images' / f'{name}.png').write_bytes(raw)
    xml = ('<root>'
           '<node package="org.fossify.calendar" text="项目评审 R7K2" bounds="[0,0][1,1]"/>'
           '<node package="org.fossify.calendar" text="09:00 AM" bounds="[0,0][1,1]"/>'
           '</root>').encode()
    (capture / 'ui' / f'{name}.xml').write_bytes(xml)
    environment = {'resolution': 'Physical size: 1x1', 'density': 'Physical density: 420',
                   'font_scale': 'default', 'locale': 'en-US',
                   'theme': 'Night mode: no', 'timezone': 'Asia/Shanghai'}
    frame = {'packet_id': 'p010', 'frame_id': 'f1', 'png': f'images/{name}.png',
             'sha256': hashlib.sha256(raw).hexdigest(), 'width': 1, 'height': 1,
             'ui_dump': f'ui/{name}.xml', 'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
             'package': 'org.fossify.calendar', 'activity': 'org.fossify.calendar/.activities.EventActivity',
             'observed_at': observed_at, 'android_time': android_time,
             'clock_synced_at': observed_at, 'device_env': dict(environment)}
    plan = {'episode_id': 'e1', 'source_assets': {'author': 'hash'},
            'packets': [{'packet_id': 'p010', 'sequence': 1, 'source': 'calendar',
                         'observed_at': observed_at,
                         'capture': {'view_id': 'event_editor', 'visible_assertions': ['title', 'start_at'],
                                     'expected_visible': {'title': '项目评审 R7K2', 'start_at': '09:00 AM'},
                                     'frames': [{'frame_id': 'f1'}]}}]}
    plan_bytes = json.dumps(plan, ensure_ascii=False).encode()
    receipt = {'status': 'capture_complete', 'reset_verified': True, 'episode_id': 'e1',
               'capture_run_id': 'run-1', 'source_assets': plan['source_assets'],
               'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'frames': [frame],
               'device': dict(environment)}
    (capture / 'private-plan.json').write_bytes(plan_bytes)
    (capture / 'receipt.json').write_text(json.dumps(receipt))
    return capture, receipt, frame


class PipelineTest(unittest.TestCase):
    def test_device_env_rejects_unreadable_locale(self):
        with unittest.mock.patch('gui_capture.adb', return_value=b''):
            with self.assertRaisesRegex(RuntimeError, 'locale is unavailable'):
                read_device_env('emulator-5554')

    def test_device_wall_clock_requires_iso_time_with_timezone(self):
        with unittest.mock.patch('gui_capture.adb',
                                 return_value=DEVICE_WALL_CLOCK.encode()) as fake:
            self.assertEqual(read_device_wall_clock('emulator-5554'), DEVICE_WALL_CLOCK)
            self.assertEqual(fake.call_args.args, ('emulator-5554', 'shell', 'date', '-Is'))
        for value, message in ((b'', 'unavailable'), (b'not-a-timestamp', 'ISO-8601'),
                               (b'2026-09-14T08:30:44', 'timezone')):
            with self.subTest(value=value), \
                    unittest.mock.patch('gui_capture.adb', return_value=value):
                with self.assertRaisesRegex(RuntimeError, message):
                    read_device_wall_clock('emulator-5554')

    def test_pair_qa_ignores_only_declared_volatile_pixels(self):
        left = bytes([0, 0, 0, 10, 10, 10, 20, 20, 20])
        right = bytes([0, 0, 0, 99, 99, 99, 20, 20, 20])
        self.assertFalse(differs_outside(left, right, 5, 3, [(1, 0, 2, 10)]))
        self.assertTrue(differs_outside(left, right, 5, 3, [(1, 0, 2, 4)]))

    def test_system_overlay_rejects_app_screen(self):
        root = ElementTree.fromstring('<root><node package="com.gmailclone"/></root>')
        hidden = 'Window #5 Window{abc u0 NotificationShade}:\n    isVisible=false'
        assert_unobscured(root, 'com.gmailclone', hidden)
        with self.assertRaisesRegex(RuntimeError, 'notification shade'):
            assert_unobscured(root, 'com.gmailclone', hidden.replace('false', 'true'))
        popup = 'Window #8 Window{abc u0 PopupWindow:tip}:\n    isVisible=true'
        with self.assertRaisesRegex(RuntimeError, 'tutorial popup'):
            assert_unobscured(root, 'com.gmailclone', popup, reject_popups=True)
        root.append(ElementTree.Element('node', {'package': 'com.android.systemui'}))
        with self.assertRaisesRegex(RuntimeError, 'overlays'):
            assert_unobscured(root, 'com.gmailclone')

    def test_app_screen_requires_notification_suppression(self):
        import unittest.mock
        with unittest.mock.patch('gui_capture.adb', return_value=b'mZenMode=ZEN_MODE_NO_INTERRUPTIONS') as fake:
            suppress_unrelated_notifications('device')
            self.assertEqual(fake.call_args_list[0].args, ('device', 'shell', 'cmd', 'notification', 'set_dnd', 'on'))
        with unittest.mock.patch('gui_capture.adb', return_value=b'mZenMode=ZEN_MODE_OFF'):
            with self.assertRaisesRegex(RuntimeError, 'suppression'):
                suppress_unrelated_notifications('device')

    def test_tap_unique_text_deduplicates_same_visible_bounds(self):
        root = ElementTree.fromstring(
            '<root><node package="com.google.android.documentsui" text="GuiPilotProof.txt" '
            'visible-to-user="true" bounds="[10,20][300,80]"/>'
            '<node package="com.google.android.documentsui" text="GuiPilotProof.txt" '
            'visible-to-user="true" bounds="[10,20][300,80]"/>'
            '<node package="com.google.android.documentsui" text="GuiPilotProof.txt" '
            'visible-to-user="false" bounds="[500,20][800,80]"/></root>')
        with unittest.mock.patch('gui_capture.ui_text', return_value=(root, b'')), \
             unittest.mock.patch('gui_capture.adb', return_value=b'') as adb_call:
            tap_unique_text('device', 'files', 'GuiPilotProof.txt', package='com.google.android.documentsui')
        self.assertEqual(adb_call.call_args.args[-2:], ('155', '50'))

    def test_calendar_seed_has_stable_identity_and_independent_readback(self):
        record = {'semantic_ref': 'meeting-01', 'data': {'title': 'GuiPilotMeeting',
                  'start_at': '2026-09-24T10:30:00+08:00', 'end_at': '2026-09-24T11:00:00+08:00',
                  'location': "Manager's room", 'description': 'Detail proof',
                  'reminder_minutes_before': 30, 'source': 'manual'}}
        key = event_key('episode-1', 'meeting-01')
        row = {'id': 17, 'title': record['data']['title'], 'start_ts': 1790217000,
               'end_ts': 1790218800, 'location': record['data']['location'],
               'description': record['data']['description'], 'reminder_1_minutes': 30,
               'source': 'manual', 'time_zone': 'Asia/Shanghai', 'import_id': key}
        commands = []
        def fake_adb(serial, *args):
            commands.append(args[-1])
            if 'SELECT' in args[-1]:
                return json.dumps([row] if len(commands) > 1 else []).encode()
            return b''
        seeded = seed_event(fake_adb, 'device', 'episode-1', record, 'Asia/Shanghai',
                            '2026-09-24T10:00:00+08:00')
        self.assertEqual(seeded, read_event(fake_adb, 'device', 'episode-1', record, 'Asia/Shanghai'))
        self.assertIn("Manager''s room", shlex.split(commands[1])[-1])
        row['source'] = 'unexpected'
        with self.assertRaisesRegex(RuntimeError, 'authority'):
            read_event(fake_adb, 'device', 'episode-1', record, 'Asia/Shanghai')

    def test_clock_seed_uuid_readback_and_card_switch(self):
        import uuid
        record = {'semantic_ref': 'alarm-01', 'data': {'label': 'GuiPilotAlarm', 'time': '10:45', 'enabled': False}}
        key = alarm_key('episode-1', 'alarm-01')
        self.assertEqual(str(uuid.UUID(key)), key)
        row = {'_id': 3, 'external_uuid': key, 'hour': 10, 'minutes': 45,
               'enabled': 0, 'daysofweek': 0, 'label': 'GuiPilotAlarm'}
        commands = []
        def fake_adb(serial, *args):
            commands.append(args)
            return json.dumps([row] if 'SELECT _id,external_uuid' in args[-1] else []).encode()
        self.assertEqual(seed_alarm(fake_adb, 'device', 'episode-1', record), row)
        self.assertEqual(read_alarm(fake_adb, 'device', 'episode-1', record), row)
        self.assertTrue(any('INSERT INTO alarm_templates' in args[-1] and key in args[-1] for args in commands))
        xml = ('<root><node package="com.google.android.deskclock" '
               'resource-id="com.google.android.deskclock:id/alarm_card_layout">'
               '<node resource-id="com.google.android.deskclock:id/label" text="GuiPilotAlarm"/>'
               '<node resource-id="com.google.android.deskclock:id/digital_clock" text="10:45 AM"/>'
               '<node resource-id="com.google.android.deskclock:id/onoff" checked="false"/>'
               '</node></root>').encode()
        def ui_text(serial):
            return ElementTree.fromstring(xml), xml
        expected = {'label': 'GuiPilotAlarm', 'time': '10:45AM', 'enabled': 'false'}
        self.assertTrue(ready_alarm(ui_text, 'device', expected, 1)['visible_assertions']['enabled'])
        row['enabled'] = 1
        with self.assertRaisesRegex(RuntimeError, 'authority'):
            read_alarm(fake_adb, 'device', 'episode-1', record)

    def test_world_readback_includes_hidden_records(self):
        records = [{'entity_type': 'articles', 'semantic_ref': 'a', 'data': {'title': 'Visible'}},
                   {'entity_type': 'favorite', 'semantic_ref': 'hidden', 'data': {'item_id': 'other'}}]
        state = {'typed_entities': [{**record, 'episode_scope': 'e1', 'record_version': 'chaowei.v1'}
                                    for record in records]}
        self.assertEqual(len(verify_seeded(state, records, 'e1')), 64)
        state['typed_entities'].pop()
        with self.assertRaisesRegex(RuntimeError, 'authority'):
            verify_seeded(state, records, 'e1')

    def test_png_integrity(self):
        raw = png()
        self.assertEqual(png_size(raw), (1, 1))
        with self.assertRaises(ValueError):
            png_size(raw[:-3])
        with self.assertRaises(ValueError):
            png_size(raw[:45] + b'X' + raw[46:])
        with self.assertRaisesRegex(ValueError, 'scanlines'):
            def chunk(kind, data):
                return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
            broken = raw[:33] + chunk(b'IDAT', zlib.compress(b'\0')) + chunk(b'IEND', b'')
            png_size(broken)

    def test_pair_pixel_decoder_reads_actual_rgb(self):
        self.assertEqual(rgb_rows(png()), (1, 1, [b'\xff\x00\x00']))

    def test_chinese_popup_ocr_keeps_body_on_one_line(self):
        fixture = ROOT / 'tests/fixtures/gui-chinese-ocr/popup.png'
        region = {'field': 'message', 'rectangle': {'left': 131, 'top': 1176, 'width': 819, 'height': 61}}
        result = subprocess.run(['node', str(ROOT / 'scripts/gui_ocr.cjs')],
                                input=json.dumps({'image': str(fixture), 'language': 'chi_sim',
                                                  'regions': [region]}), text=True, capture_output=True, check=True)
        recognized = json.loads(result.stdout)['regions'][0]
        self.assertEqual(recognized['text'], '请提前确认您的出发地点。')
        self.assertGreaterEqual(recognized['confidence'], 80)

    def test_machine_ocr_language_follows_field_script_and_app_default(self):
        self.assertEqual(field_language('title', '项目评审 R7K2', 'eng'), 'chi_sim')
        self.assertEqual(field_language('body', '关于项目评审 R7K2：投屏与麦克风联调。', 'eng'), 'chi_sim')
        self.assertEqual(field_language('message', '请提前确认您的出发地点。', 'chi_sim'), 'chi_sim')
        self.assertEqual(field_language('start_at', '09:00 AM', 'eng'), 'eng')
        self.assertEqual(field_language('_status_time', '08:30', 'eng'), 'eng')
        self.assertEqual(field_language('_status_time', '08:30', 'chi_sim'), 'chi_sim')

    def test_machine_cjk_text_equivalence_folds_width_and_spacing(self):
        self.assertEqual(canonical_text('项目评审 R7K2｜ 开场 安排'), canonical_text('项目评审R7K2|开场安排'))
        self.assertEqual(canonical_text('项目评审 R7K2：开场'), canonical_text('项目评审R7K2:开场'))
        self.assertNotEqual(canonical_text('项目评审 R7K2'), canonical_text('项目评审 R7K3'))
        self.assertNotEqual(canonical_text('会议室A开始'), canonical_text('会议室A开台'))

    def test_machine_status_time_accepts_android_single_digit_hour(self):
        self.assertTrue(status_time_equivalent('8:30 ¢', '08:30'))
        self.assertTrue(status_time_equivalent('10:00', '10:00'))
        self.assertFalse(status_time_equivalent('18:30', '08:30'))
        self.assertFalse(status_time_equivalent('8:3O', '08:30'))
        self.assertFalse(status_time_equivalent('', '08:30'))
        self.assertEqual(fallback_attempts('_status_time', 'eng'),
                         ({'scale': 2, 'pageseg': 7},
                          {'rectangle': {'left': 0, 'top': 0, 'width': 140, 'height': 132},
                           'scale': 2, 'pageseg': 7}))
        self.assertEqual(fallback_attempts('_status_time', 'chi_sim'),
                         fallback_attempts('_status_time', 'eng'))
        self.assertEqual(fallback_attempts('subject', 'chi_sim'), ({'pageseg': 6}, {'pageseg': 4, 'scale': 2}))
        self.assertEqual(fallback_attempts('subject', 'eng'), ())

    def test_machine_inbox_snippet_accepts_only_the_visible_preview_head(self):
        full = '关于项目评审 R7K2：投屏与麦克风联调共需要连续 10 分钟，应在开场演示之前完成。'
        self.assertTrue(preview_prefix_matches('关于项目评审 R7K2: 投屏与麦克风.…', full))
        self.assertTrue(preview_prefix_matches('关于项目评审 R7K2: 投屏与麦克风', full))
        self.assertTrue(field_matches('mail_inbox', 'row_1_snippet', 'chi_sim',
                                      '关于项目评审 R7K2: 投屏与麦克风.…', full))
        self.assertFalse(preview_prefix_matches('项目评审 R7K2 将于今天 09:00 在会.…', full))
        self.assertFalse(preview_prefix_matches('关于项目评审', full))
        self.assertFalse(preview_prefix_matches('', full))
        self.assertFalse(field_matches('mail_inbox', 'row_1_snippet', 'chi_sim', '关于项目评审', full))
        # Other Mail inbox fields keep the exact match.
        self.assertTrue(field_matches('mail_inbox', 'row_1_subject', 'chi_sim',
                                      '项目评审 R7K2 | 设备准备说明', '项目评审 R7K2｜设备准备说明'))
        self.assertFalse(field_matches('mail_inbox', 'row_1_subject', 'chi_sim',
                                       '项目评审 R7K2 | 设备准备说明', '项目评审 R7K2｜开场安排'))

    def test_machine_qa_reads_cjk_calendar_fields_with_chi_sim_only(self):
        import hashlib
        import gui_machine_qa
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'capture'
            (capture / 'images').mkdir(parents=True)
            (capture / 'ui').mkdir()
            raw = png()
            name = 'a' * 32
            (capture / 'images' / f'{name}.png').write_bytes(raw)
            xml = ('<root>'
                   '<node package="org.fossify.calendar" text="项目评审 R7K2" bounds="[0,0][1,1]"/>'
                   '<node package="org.fossify.calendar" text="09:00 AM" bounds="[0,0][1,1]"/>'
                   '</root>').encode()
            (capture / 'ui' / f'{name}.xml').write_bytes(xml)
            expected_visible = {'title': '项目评审 R7K2', 'start_at': '09:00 AM'}
            environment = {'resolution': 'Physical size: 1x1', 'density': 'Physical density: 420',
                           'font_scale': 'default', 'locale': 'en-US',
                           'theme': 'Night mode: no', 'timezone': 'Asia/Shanghai'}
            frame = {'packet_id': 'p004', 'frame_id': 'f1', 'png': f'images/{name}.png',
                     'sha256': hashlib.sha256(raw).hexdigest(), 'width': 1, 'height': 1,
                     'ui_dump': f'ui/{name}.xml', 'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                     'package': 'org.fossify.calendar', 'activity': 'org.fossify.calendar/.activities.EventActivity',
                     'android_time': '2026-09-14T08:30:44+08:00',
                     'clock_synced_at': '2026-09-14T08:30:44+08:00',
                     'device_env': dict(environment)}
            plan = {'episode_id': 'e1', 'source_assets': {'author': 'hash'},
                    'packets': [{'packet_id': 'p004', 'sequence': 1, 'source': 'calendar',
                                 'observed_at': '2026-09-14T08:30:44+08:00',
                                 'capture': {'view_id': 'event_editor', 'visible_assertions': ['title', 'start_at'],
                                             'expected_visible': dict(expected_visible),
                                             'frames': [{'frame_id': 'f1'}]}}]}
            plan_bytes = json.dumps(plan, ensure_ascii=False).encode()
            receipt = {'status': 'capture_complete', 'reset_verified': True, 'episode_id': 'e1',
                       'capture_run_id': 'run-1', 'source_assets': plan['source_assets'],
                       'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'frames': [frame],
                       'device': dict(environment)}
            (capture / 'private-plan.json').write_bytes(plan_bytes)
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            calls = []

            def fake_run_ocr(image, regions, language, pixel_checks=None):
                calls.append((language, [region['field'] for region in regions]))
                recognized = {'title': '项目评审 R7K2', 'start_at': '09:00 AM', '_status_time': '08:30'}
                return {'engine': 'tesseract.js@7.0.0', 'language': language,
                        'model_sha256': gui_machine_qa.OCR_MODELS[language],
                        'regions': [{'field': region['field'], 'text': recognized[region['field']],
                                     'confidence': 90, 'rectangle': region['rectangle'],
                                     'scale': region.get('scale', 1)} for region in regions],
                        'checks': []}

            with unittest.mock.patch('gui_machine_qa.run_ocr', side_effect=fake_run_ocr):
                report = gui_machine_qa.audit_capture(capture)
            self.assertEqual(report['status'], 'pass')
            self.assertEqual(report['frames'][0]['language'], 'chi_sim+eng')
            self.assertEqual(calls, [('chi_sim', ['title']), ('eng', ['start_at', '_status_time'])])
            self.assertEqual([field['language'] for field in report['frames'][0]['fields']],
                             ['chi_sim', 'eng', 'eng'])

    def test_machine_status_clock_binds_to_frame_android_time_across_minutes(self):
        import gui_machine_qa
        with tempfile.TemporaryDirectory() as tmp:
            capture, _, _ = machine_capture(Path(tmp), '2026-09-14T08:30:59+08:00',
                                            '2026-09-14T08:31:05+08:00')

            def fake_run_ocr(image, regions, language, pixel_checks=None):
                recognized = {'title': '项目评审 R7K2', 'start_at': '09:00 AM', '_status_time': '08:31'}
                return {'engine': 'tesseract.js@7.0.0', 'language': language,
                        'model_sha256': gui_machine_qa.OCR_MODELS[language],
                        'regions': [{'field': region['field'], 'text': recognized[region['field']],
                                     'confidence': 90, 'rectangle': region['rectangle'],
                                     'scale': region.get('scale', 1)} for region in regions],
                        'checks': []}

            with unittest.mock.patch('gui_machine_qa.run_ocr', side_effect=fake_run_ocr):
                report = gui_machine_qa.audit_capture(capture)
            self.assertEqual(report['status'], 'pass')
            status = report['frames'][0]['fields'][-1]
            self.assertEqual(status['field'], '_status_time')
            self.assertEqual(status['expected'], '08:31')
            # observed_at stays the packet flow time even when navigation crosses the minute.
            packet = json.loads((capture / 'private-plan.json').read_text())['packets'][0]
            self.assertEqual(packet['observed_at'], '2026-09-14T08:30:59+08:00')

    def test_machine_qa_rejects_missing_or_invalid_android_time(self):
        import gui_machine_qa
        for name, value in [('missing', None), ('not-a-string', 1790219465),
                            ('not-iso', '08:31'), ('naive', '2026-09-14T08:31:05')]:
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as tmp:
                    capture, receipt, frame = machine_capture(
                        Path(tmp), '2026-09-14T08:30:59+08:00', '2026-09-14T08:31:05+08:00')
                    if value is None:
                        frame.pop('android_time')
                    else:
                        frame['android_time'] = value
                    (capture / 'receipt.json').write_text(json.dumps(receipt))
                    with unittest.mock.patch('gui_machine_qa.run_ocr') as ocr:
                        report = gui_machine_qa.audit_capture(capture)
                    self.assertEqual(report['status'], 'reject')
                    self.assertIn('android_time', report['frames'][0]['reason'])
                    ocr.assert_not_called()

    def test_clock_time_parts_and_switch_use_original_pixels(self):
        fixture = ROOT / 'tests/fixtures/gui-clock-pixels/alarm.png'
        input_data = {'image': str(fixture), 'regions': [
            {'field': '_time_digits', 'rectangle': {'left': 84, 'top': 1143, 'width': 359, 'height': 159}},
            {'field': '_time_meridiem', 'rectangle': {'left': 438, 'top': 1190, 'width': 84, 'height': 112}, 'scale': 3}],
            'pixel_checks': [{'field': 'enabled', 'kind': 'switch_knob_left',
                              'rectangle': {'left': 850, 'top': 1302, 'width': 146, 'height': 126}}]}
        result = subprocess.run(['node', str(ROOT / 'scripts/gui_ocr.cjs')], input=json.dumps(input_data),
                                text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual([item['text'] for item in report['regions']], ['10:45', 'AM'])
        self.assertTrue(all(item['confidence'] >= 80 for item in report['regions']))
        self.assertGreaterEqual(report['checks'][0]['difference'], 50)

    def test_contact_pixel_regions_bind_formatted_phone_to_authority(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'ui').mkdir()
            xml = ('<root><node package="com.google.android.contacts" '
                   'resource-id="com.google.android.contacts:id/large_title" text="GuiPilotTwin" bounds="[10,20][200,60]"/>'
                   '<node package="com.google.android.contacts" text="555-0112" bounds="[10,70][200,100]"/>'
                   '<node package="com.google.android.contacts" text="target@example.test" bounds="[10,110][300,140]"/></root>').encode()
            (root / 'ui' / ('a' * 32 + '.xml')).write_bytes(xml)
            frame = {'package': 'com.google.android.contacts', 'ui_dump': 'ui/' + 'a' * 32 + '.xml',
                     'ui_dump_sha256': hashlib.sha256(xml).hexdigest()}
            packet = {'capture': {'view_id': 'contact_detail', 'visible_assertions': ['name', 'phone', 'email'],
                                  'expected_visible': {'name': 'GuiPilotTwin', 'phone': '5550112',
                                                       'email': 'target@example.test'}}}
            regions, expected, checks = frame_regions(root, frame, packet)
            self.assertEqual([item['field'] for item in regions], ['name', 'phone', 'email', '_status_time'])
            self.assertEqual(checks, [])
            self.assertEqual(regions[-1],
                             {'field': '_status_time',
                              'rectangle': {'left': 0, 'top': 0, 'width': 120, 'height': 132}})
            self.assertEqual(expected['phone'], '555-0112')
            packet['capture']['expected_visible']['phone'] = '5550113'
            with self.assertRaisesRegex(ValueError, 'unique UI-backed pixel region'):
                frame_regions(root, frame, packet)

    def test_publish_requires_machine_pixels_and_whitelists(self):
        import hashlib
        import shutil
        from gui_machine_qa import audit_capture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / 'capture'
            (capture / 'images').mkdir(parents=True)
            (capture / 'ui').mkdir()
            fixture = ROOT / 'tests/fixtures/gui-mail-machine'
            image = next((fixture / 'images').glob('*.png'))
            xml = next((fixture / 'ui').glob('*.xml'))
            shutil.copyfile(image, capture / 'images' / image.name)
            shutil.copyfile(xml, capture / 'ui' / xml.name)
            raw = image.read_bytes()
            frame = {'packet_id': 'p001', 'frame_id': 'f1', 'png': f'images/{image.name}',
                     'sha256': hashlib.sha256(raw).hexdigest(), 'width': 1080, 'height': 2400,
                     'ui_dump': f'ui/{xml.name}', 'ui_dump_sha256': hashlib.sha256(xml.read_bytes()).hexdigest(),
                     'package': 'com.gmailclone', 'activity': 'com.gmailclone/.MainActivity',
                     'android_time': '2026-09-25T10:00:08+08:00',
                     'clock_synced_at': '2026-09-25T10:00:00+08:00',
                     'device_env': {'resolution': 'Physical size: 1080x2400', 'density': 'Physical density: 420',
                                    'font_scale': 'default', 'locale': 'en-US', 'theme': 'Night mode: no',
                                    'timezone': 'Asia/Shanghai'}}
            receipt = {'status': 'capture_complete', 'reset_verified': True, 'episode_id': 'e1',
                       'capture_run_id': 'run-1', 'source_assets': {'author': 'hash'}, 'frames': [frame],
                       'device': {'baseline_version': 'sha256:' + 'a' * 64, 'resolution': 'Physical size: 1080x2400',
                                  'density': 'Physical density: 420', 'font_scale': 'default',
                                  'locale': 'en-US', 'theme': 'Night mode: no', 'timezone': 'GMT'}}
            plan = {'episode_id': 'e1', 'source_assets': receipt['source_assets'],
                    'public_rule': 'Synchronize the meeting time.',
                    'formal': {'oracle': {'expected_need_set': ['private answer']}},
                    'packets': [{'packet_id': 'p001', 'sequence': 1, 'source': 'mail',
                                 'observed_at': '2026-09-25T10:00:00+08:00', 'text_projection': 'private text',
                                 'capture': {'subject_ref': 'secret', 'view_id': 'mail_detail',
                                             'visible_assertions': ['subject', 'from', 'date', 'body'],
                                             'expected_visible': {'subject': 'GuiSyncUpdate', 'from': 'Organizer',
                                                                  'date': 'Sep 25', 'body': 'GuiSyncMeeting now 2026-09-25T11:30:00+08:00 to 2026-09-25T12:00:00+08:00.'},
                                             'frames': [{'frame_id': 'f1'}]}}]}
            plan_bytes = json.dumps(plan).encode()
            receipt['plan_sha256'] = hashlib.sha256(plan_bytes).hexdigest()
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            (capture / 'private-plan.json').write_bytes(plan_bytes)
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'machine pixel QA is missing'):
                publish(capture, root / 'release')
            machine = audit_capture(capture)
            self.assertEqual(machine['status'], 'pass')
            (capture / 'qa-machine.json').write_text(json.dumps(machine))
            receipt['status'] = 'pending_review'
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'capture did not pass machine checks'):
                publish(capture, root / 'release')
            receipt['status'] = 'capture_complete'
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            tampered = json.loads(json.dumps(machine))
            tampered['frames'][0]['fields'][0]['text'] = 'forged'
            (capture / 'qa-machine.json').write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'machine pixel QA'):
                publish(capture, root / 'release')
            (capture / 'qa-machine.json').write_text(json.dumps(machine))
            # Same rendered minute, wrong timezone: machine QA re-audit is stable and
            # the publish binding gate is the check that rejects.
            frame['android_time'] = '2026-09-25T10:00:08+00:00'
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'Android time'):
                publish(capture, root / 'release')
            frame['android_time'] = '2026-09-25T10:00:08+08:00'
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            public = publish(capture, root / 'release')
            encoded = json.dumps(public)
            self.assertNotIn('private text', encoded)
            self.assertNotIn('secret', encoded)
            self.assertNotIn('private answer', encoded)
            self.assertEqual((root / 'release/images' / image.name).read_bytes(), raw)
            self.assertEqual(verify_release(root / 'release')['frames'], 1)
            private_receipt = root / 'release.private/receipt.json'
            private_qa = root / 'release.private/qa.json'
            receipt_copy = json.loads(private_receipt.read_text())
            receipt_copy['status'] = 'pending_review'
            private_receipt.write_text(json.dumps(receipt_copy))
            original_qa = json.loads(private_qa.read_text())
            qa_copy = json.loads(json.dumps(original_qa))
            qa_copy['receipt_sha256'] = hashlib.sha256(private_receipt.read_bytes()).hexdigest()
            private_qa.write_text(json.dumps(qa_copy))
            with self.assertRaisesRegex(ValueError, 'published receipt is not machine-complete'):
                verify_release(root / 'release')
            private_receipt.write_text(json.dumps(receipt))
            private_qa.write_text(json.dumps(original_qa))
            (root / 'release/images' / image.name).write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'PNG hash'):
                verify_release(root / 'release')
            (root / 'release/images' / image.name).write_bytes(raw)
            (root / 'release/secret.txt').write_text('private')
            with self.assertRaisesRegex(ValueError, 'unexpected files'):
                verify_release(root / 'release')
            (root / 'release/secret.txt').unlink()
            (root / 'release.private/qa-machine.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                verify_release(root / 'release')

    def test_device_env_missing_or_drifted_frames_are_rejected(self):
        import hashlib
        from gui_machine_qa import audit_capture, audit_device_env
        environment = {'resolution': 'Physical size: 1x1', 'density': 'Physical density: 420',
                       'font_scale': 'default', 'locale': 'en-US',
                       'theme': 'Night mode: no', 'timezone': 'Asia/Shanghai'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / 'capture'
            (capture / 'images').mkdir(parents=True)
            frames = []
            for frame_id in ('f1', 'f2'):
                raw = png()
                name = frame_id + '.png'
                (capture / 'images' / name).write_bytes(raw)
                frames.append({'packet_id': 'p001', 'frame_id': frame_id, 'png': f'images/{name}',
                               'sha256': hashlib.sha256(raw).hexdigest(), 'width': 1, 'height': 1,
                               'device_env': dict(environment)})
            plan = {'episode_id': 'e1', 'source_assets': {'author': 'hash'},
                    'packets': [{'packet_id': 'p001', 'sequence': 1, 'source': 'mail',
                                 'observed_at': '2026-09-25T10:00:00+08:00',
                                 'capture': {'subject_ref': 'secret', 'view_id': 'mail_detail',
                                             'visible_assertions': ['subject'],
                                             'expected_visible': {'subject': 'GuiSyncUpdate'},
                                             'frames': [{'frame_id': 'f1'}, {'frame_id': 'f2'}]}}]}
            plan_bytes = json.dumps(plan).encode()
            receipt = {'status': 'capture_complete', 'reset_verified': True, 'episode_id': 'e1',
                       'capture_run_id': 'run-1', 'source_assets': plan['source_assets'],
                       'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'frames': frames,
                       'device': {**environment, 'timezone': 'GMT', 'baseline_version': 'sha256:' + 'a' * 64}}
            (capture / 'private-plan.json').write_bytes(plan_bytes)
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            accepted = audit_device_env(capture, receipt)
            self.assertEqual(accepted['status'], 'pass')
            self.assertEqual(accepted['episode'], environment)
            frames[1]['device_env'].pop('locale')
            missing = audit_device_env(capture, receipt)
            self.assertEqual(missing['status'], 'reject')
            self.assertIn('locale', missing['reason'])
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(audit_capture(capture)['status'], 'reject')
            frames[1]['device_env'] = dict(environment, density='Physical density: 480')
            drifted = audit_device_env(capture, receipt)
            self.assertEqual(drifted['status'], 'reject')
            self.assertIn('density', drifted['reason'])
            (capture / 'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(audit_capture(capture)['status'], 'reject')
            (capture / 'qa-machine.json').write_text(json.dumps(audit_capture(capture)))
            with self.assertRaisesRegex(ValueError, 'machine pixel QA'):
                publish(capture, root / 'release')
            for frame in frames:
                frame['device_env'] = {**environment, 'resolution': 'Physical size: 2x1'}
            mismatch = audit_device_env(capture, receipt)
            self.assertEqual(mismatch['status'], 'reject')
            self.assertIn('PNG size', mismatch['reason'])


MAIL_REF = 'mail.message.mail_001'
MAIL_EFFECTIVE = '2026-09-14T08:31:45+08:00'
MAIL_OBSERVED = '2026-09-14T08:31:48+08:00'


def mail_fixture_record(status='unread', ref=MAIL_REF):
    return {'app_id': 'mail', 'entity_type': 'mails', 'semantic_ref': ref,
            'data': {'headers': {'subject': 'GuiHistoryMail', 'from': 'Organizer'},
                     'body': 'history replay proof', 'status': status, 'attachments': []}}


def mail_replay_packet(observed_at=MAIL_OBSERVED, effective_at=MAIL_EFFECTIVE, ref=MAIL_REF,
                       target='read'):
    return {'packet_id': 'p010', 'observed_at': observed_at,
            'history_replays': [{'semantic_ref': ref, 'effective_at': effective_at,
                                 'set': {'status': target}}]}


class HistoryReplayTest(unittest.TestCase):
    def test_old_pilot_plans_never_call_the_device_replay(self):
        record = mail_fixture_record()
        records = [record]
        replayed = set()
        with unittest.mock.patch('gui_capture.apply_history_change') as replay:
            self.assertEqual(apply_history_replays('device', 'emulator-5554',
                                                   {'packet_id': 'p004'}, records, replayed), [])
            self.assertEqual(apply_history_replays(
                'device', 'emulator-5554', {'packet_id': 'p004', 'history_replays': []},
                records, replayed), [])
        replay.assert_not_called()
        self.assertEqual(record['data']['status'], 'unread')
        self.assertEqual(replayed, set())

    def test_replay_advances_the_fixture_before_navigation_and_records_the_receipt(self):
        record = mail_fixture_record()
        records = [record]
        replayed = set()
        with unittest.mock.patch('gui_capture.apply_history_change',
                                 return_value='replayed-hash') as replay:
            result = apply_history_replays('device', 'emulator-5554', mail_replay_packet(),
                                           records, replayed)
        replay.assert_called_once()
        called = replay.call_args.args
        self.assertEqual(called[:2], ('device', 'emulator-5554'))
        self.assertEqual(called[2]['semantic_ref'], MAIL_REF)
        self.assertEqual(called[2]['data']['status'], 'read')
        self.assertEqual(called[2]['data']['headers']['subject'], 'GuiHistoryMail')
        self.assertEqual(record['data']['status'], 'read')
        self.assertEqual(replayed, {MAIL_REF})
        self.assertEqual(result, [{'packet_id': 'p010', 'semantic_ref': MAIL_REF,
                                   'effective_at': MAIL_EFFECTIVE, 'set': {'status': 'read'},
                                   'function': 'apply_history_change', 'result': 'replayed-hash'}])

    def test_device_replay_failure_aborts_without_advancing_the_fixture(self):
        record = mail_fixture_record()
        records = [record]
        replayed = set()
        with unittest.mock.patch('gui_capture.apply_history_change',
                                 side_effect=RuntimeError('Mail history replay rejects the '
                                                          "'read' -> 'read' status transition")):
            with self.assertRaisesRegex(RuntimeError, 'history_replays\\[1\\] failed'):
                apply_history_replays('device', 'emulator-5554', mail_replay_packet(), records,
                                      replayed)
        self.assertEqual(record['data']['status'], 'unread')
        self.assertEqual(replayed, set())

    def test_malformed_or_duplicate_replays_abort_before_any_device_write(self):
        record = mail_fixture_record()
        records = [record]
        with unittest.mock.patch('gui_capture.apply_history_change') as replay:
            with self.assertRaisesRegex(RuntimeError, 'only allows the declared Mail'):
                apply_history_replays('device', 'emulator-5554',
                                      mail_replay_packet(target='archived'), records, set())
            with self.assertRaisesRegex(RuntimeError, 'not in the capture fixture'):
                apply_history_replays('device', 'emulator-5554',
                                      mail_replay_packet(ref='mail.message.mail_002'), records, set())
            with self.assertRaisesRegex(RuntimeError, 'not effective at the packet observed_at'):
                apply_history_replays('device', 'emulator-5554',
                                      mail_replay_packet(effective_at='2026-09-14T08:32:00+08:00'),
                                      records, set())
            with self.assertRaisesRegex(RuntimeError, 'effective_at must be an ISO-8601'):
                apply_history_replays('device', 'emulator-5554',
                                      mail_replay_packet(effective_at='08:31:45'), records, set())
            with self.assertRaisesRegex(RuntimeError, 'replays .* twice'):
                apply_history_replays('device', 'emulator-5554', mail_replay_packet(), records,
                                      {MAIL_REF})
            already_read = [mail_fixture_record(status='read')]
            with self.assertRaisesRegex(RuntimeError, "to start at 'unread'"):
                apply_history_replays('device', 'emulator-5554', mail_replay_packet(), already_read,
                                      set())
        replay.assert_not_called()


MAIL_PACKAGE = 'com.gmailclone'
MAIL_STATE_PATH = '/sdcard/Android/data/com.gmailclone/files/state.json'
DEVICE_ENVIRONMENT = {'resolution': 'Physical size: 1080x2400', 'density': 'Physical density: 420',
                      'font_scale': 'default', 'locale': 'en-US', 'theme': 'Night mode: no',
                      'timezone': 'Asia/Shanghai'}
DEVICE_WALL_CLOCK = '2026-09-14T08:30:44+08:00'


class FakeMailDevice:
    """Minimal adb double that persists the Mail state file across seed/replay/read."""
    def __init__(self, mails=()):
        self.mails = copy.deepcopy(list(mails))
        self.calls = []
        self.status_log = []

    def adb(self, serial, *args, timeout=30):
        self.calls.append(args)
        if args[:3] == ('shell', 'date', '-Is'):
            return DEVICE_WALL_CLOCK.encode()
        if args[:3] == ('shell', 'cat', MAIL_STATE_PATH):
            return json.dumps({'mails': self.mails}).encode()
        if args and args[0] == 'push':
            self.mails = json.loads(Path(args[1]).read_text())['mails']
            self.status_log.append([mail['status'] for mail in self.mails])
            return b''
        if args[:2] == ('exec-out', 'screencap'):
            return png()
        if args[:3] == ('shell', 'dumpsys', 'activity'):
            return b'topResumedActivity=com.gmailclone/.MainActivity'
        return b''

    def reset(self):
        self.mails = []

    def monkey_calls(self):
        return [args for args in self.calls if args[:2] == ('shell', 'monkey')]


def fake_ui_text(serial):
    xml = (f'<root><node package="{MAIL_PACKAGE}" text="GuiHistoryMail" visible-to-user="true" '
           f'bounds="[10,20][300,80]"/>'
           f'<node package="{MAIL_PACKAGE}" text="Organizer" visible-to-user="true" '
           f'bounds="[10,90][300,140]"/>'
           f'<node package="{MAIL_PACKAGE}" text="history replay proof" visible-to-user="true" '
           f'bounds="[10,150][600,200]"/></root>').encode()
    return ElementTree.fromstring(xml), xml


def inbox_fixture_records():
    return [
        {'app_id': 'mail', 'entity_type': 'mails', 'semantic_ref': 'mail.message.mail_001',
         'data': {'headers': {'subject': '项目评审 R7K2｜开场安排', 'from': '周宁'},
                  'body': '项目评审 R7K2 将于今天 09:00 在会议室 A 开始，09:30 结束。',
                  'status': 'unread', 'attachments': []}},
        {'app_id': 'mail', 'entity_type': 'mails', 'semantic_ref': 'mail.message.mail_002',
         'data': {'headers': {'subject': '项目评审 R7K2｜设备准备说明', 'from': '周宁'},
                  'body': '关于项目评审 R7K2：投屏与麦克风联调共需要连续 10 分钟。',
                  'status': 'unread', 'attachments': []}},
    ]


INBOX_ROWS = (('row_1', 'mail.message.mail_002'), ('row_2', 'mail.message.mail_001'))
INBOX_OBSERVED = '2026-09-14T08:31:35+08:00'
LATER_INBOX_OBSERVED = '2026-09-14T08:33:05+08:00'


def inbox_packet(packet_id, observed, replays=()):
    data_by_ref = {record['semantic_ref']: record['data'] for record in inbox_fixture_records()}
    expected = {}
    for prefix, ref in INBOX_ROWS:
        data = data_by_ref[ref]
        expected[f'{prefix}_sender'] = data['headers']['from']
        expected[f'{prefix}_subject'] = data['headers']['subject']
        expected[f'{prefix}_snippet'] = data['body']
    packet = {'packet_id': packet_id, 'sequence': 1, 'tag': 'gui', 'source': 'mail',
              'observed_at': observed, 'delivered_at': observed, 'text_event': f'{packet_id} summary',
              'capture': {'surface': 'app_screen', 'app_id': 'mail', 'view_id': 'mail_inbox',
                          'subject_ref': 'mail.message.mail_002',
                          'visible_assertions': list(expected), 'expected_visible': expected,
                          'frames': [{'frame_id': f'{packet_id}_f1'}], 'timeout_seconds': 5}}
    if replays:
        packet['history_replays'] = [dict(replay) for replay in replays]
    return packet


def inbox_plan(packets=None):
    return {'schema_version': 'pb-gui-private-capture-plan.v1', 'episode_id': 'ep_0004',
            'baseline_ref': 'init_state', 'device_timezone': 'Asia/Shanghai',
            'source_assets': {'gui_plan': {'sha256': 'a' * 64}},
            'fixture': {'episode_scope': 'ep_0004', 'common_records': [], 'world_deltas': [],
                        'ku_records': inbox_fixture_records()},
            'packets': packets or [inbox_packet('p009', INBOX_OBSERVED)],
            'eof_at': '2026-09-14T08:35:01+08:00'}


class FakeInboxDevice(FakeMailDevice):
    """FakeMailDevice plus an Inbox list rendered from the live mail state."""
    def __init__(self, mails=(), detail_only=False):
        super().__init__(mails)
        self.detail_only = detail_only

    def ui_text(self, serial):
        mails = self.mails[:1] if self.detail_only else self.mails
        rows = []
        for mail in mails:
            headers = mail['headers']
            rows.append(
                f'<node package="{MAIL_PACKAGE}" class="android.widget.FrameLayout" '
                f'bounds="[0,0][1080,300]">'
                f'<node package="{MAIL_PACKAGE}" class="android.widget.TextView" '
                f'text="{headers["from"]}" bounds="[10,10][200,50]"/>'
                f'<node package="{MAIL_PACKAGE}" class="android.widget.TextView" '
                f'text="{headers["subject"]}" bounds="[10,60][600,100]"/>'
                f'<node package="{MAIL_PACKAGE}" class="android.widget.TextView" '
                f'text="{mail["body"]}" bounds="[10,110][1000,150]"/>'
                f'</node>')
        xml = ('<root>' + ''.join(rows) + '</root>').encode()
        return ElementTree.fromstring(xml), xml


OBSERVATION_START = '2026-09-14T08:30:00+08:00'


def history_plan(history_replays=True, pb_clock=False):
    record = mail_fixture_record()
    packet = {'packet_id': 'p010', 'sequence': 1, 'tag': 'gui', 'source': 'mail',
              'observed_at': MAIL_OBSERVED, 'delivered_at': MAIL_OBSERVED,
              'text_event': 'p010 summary',
              'capture': {'surface': 'app_screen', 'app_id': 'mail', 'view_id': 'mail_detail',
                          'subject_ref': MAIL_REF,
                          'visible_assertions': ['subject', 'from', 'body'],
                          'expected_visible': {'subject': 'GuiHistoryMail', 'from': 'Organizer',
                                               'body': 'history replay proof'},
                          'frames': [{'frame_id': 'p010_f1'}], 'timeout_seconds': 5}}
    if history_replays:
        packet['history_replays'] = [{'semantic_ref': MAIL_REF, 'effective_at': MAIL_EFFECTIVE,
                                      'set': {'status': 'read'}}]
    plan = {'schema_version': 'pb-gui-private-capture-plan.v1', 'episode_id': 'ep_0004',
            'baseline_ref': 'init_state', 'device_timezone': 'Asia/Shanghai',
            'source_assets': {'gui_plan': {'sha256': 'a' * 64}},
            'fixture': {'episode_scope': 'ep_0004', 'common_records': [], 'world_deltas': [],
                        'ku_records': [record]},
            'packets': [packet], 'eof_at': '2026-09-14T08:35:01+08:00'}
    if pb_clock:
        plan['pb_clock_binding'] = {'observation_start': OBSERVATION_START,
                                    'decision_point': plan['eof_at'],
                                    'timezone': plan['device_timezone'],
                                    'source': 'PB history/cluster'}
        plan['initial_simulated_time'] = OBSERVATION_START
    return plan


class RunHistoryReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        instance = self.root / 'runtime/instances/i1'
        instance.mkdir(parents=True)
        (instance / 'instance.json').write_text(json.dumps(
            {'snapshot': 'init_state', 'baseline_version': 'sha256:' + 'b' * 64,
             'dataset_version': 'ds-1', 'adb_serial': 'emulator-5554', 'controller_port': 1,
             'runner_key': 'runner-key', 'ku_container': ''}))

    def run_capture(self, device, plan, name, calls=None):
        capture = self.root / name

        def fake_request(base, key, path, data=None):
            if calls is not None:
                calls.append(('request', path, data))
            if path == '/local/v1/device/status':
                return {'available': True, 'boot_completed': True}
            if path == '/local/v1/clock' and data is not None:
                return {'success': True, 'simulated_time': data['simulated_time']}
            return {}

        def fake_reset(instance):
            if calls is not None:
                calls.append(('reset', instance))
            return device.reset()

        with unittest.mock.patch.object(gui_capture, 'ROOT', self.root), \
                unittest.mock.patch.object(gui_capture, 'reset_instance',
                                           side_effect=fake_reset), \
                unittest.mock.patch.object(gui_capture, 'request', side_effect=fake_request), \
                unittest.mock.patch.object(gui_capture, 'adb', side_effect=device.adb), \
                unittest.mock.patch.object(gui_capture, 'ui_text',
                                           side_effect=getattr(device, 'ui_text', fake_ui_text)), \
                unittest.mock.patch.object(gui_capture, 'read_device_env',
                                           return_value=dict(DEVICE_ENVIRONMENT)), \
                unittest.mock.patch.object(gui_capture, 'set_device_time',
                                           side_effect=lambda serial, observed, tz: observed), \
                unittest.mock.patch.object(gui_capture, 'suppress_unrelated_notifications'):
            return gui_capture.run(copy.deepcopy(plan), 'i1', capture)

    def test_run_replays_history_before_navigation_and_records_it(self):
        device = FakeMailDevice()
        receipt = self.run_capture(device, history_plan(), 'capture-replay')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertTrue(receipt['reset_verified'])
        replays = receipt['history_replays']
        self.assertEqual([(replay['packet_id'], replay['semantic_ref'], replay['effective_at'],
                           replay['set'], replay['function']) for replay in replays],
                         [('p010', MAIL_REF, MAIL_EFFECTIVE, {'status': 'read'},
                           'apply_history_change')])
        self.assertEqual(device.status_log, [['unread'], ['read']])
        writes = [index for index, args in enumerate(device.calls) if args and args[0] == 'push']
        navigation = [index for index, args in enumerate(device.calls)
                      if args[:2] == ('shell', 'monkey')]
        self.assertTrue(writes and navigation and max(writes) < min(navigation))
        self.assertEqual(receipt['frames'][0]['readback'], replays[0]['result'])
        self.assertEqual(receipt['readback']['mail'], replays[0]['result'])

    def test_run_keeps_plans_without_history_replays_unchanged(self):
        device = FakeMailDevice()
        with unittest.mock.patch('gui_capture.apply_history_change') as replay:
            receipt = self.run_capture(device, history_plan(history_replays=False), 'capture-legacy')
        replay.assert_not_called()
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertNotIn('history_replays', receipt)
        self.assertEqual(device.status_log, [['unread']])

    def test_run_aborts_the_capture_when_the_history_replay_fails(self):
        device = FakeMailDevice()
        with unittest.mock.patch('gui_capture.apply_history_change',
                                 side_effect=RuntimeError("Mail history replay rejects the "
                                                          "'read' -> 'read' status transition")):
            receipt = self.run_capture(device, history_plan(), 'capture-abort')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('history_replays[1] failed', receipt['failure_reason'])
        self.assertEqual(device.monkey_calls(), [])
        self.assertNotIn('history_replays', receipt)
        self.assertTrue(receipt['reset_verified'])

    def test_run_sets_the_controller_clock_from_the_pb_binding_after_reset(self):
        device = FakeMailDevice()
        calls = []
        plan = history_plan(pb_clock=True)
        receipt = self.run_capture(device, plan, 'capture-clock', calls=calls)
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertEqual(receipt['clock_binding'], plan['pb_clock_binding'])
        self.assertEqual(receipt['environment']['simulated_time'], OBSERVATION_START)
        self.assertEqual(receipt['environment']['timezone'], 'Asia/Shanghai')
        clock = next(index for index, entry in enumerate(calls)
                     if entry[1] == '/local/v1/clock')
        self.assertEqual(calls[clock][2], {'simulated_time': OBSERVATION_START})
        reset = next(index for index, entry in enumerate(calls) if entry[0] == 'reset')
        advance = next(index for index, entry in enumerate(calls)
                       if entry[1] == '/local/v1/instance/advance_time')
        self.assertLess(reset, clock)
        self.assertLess(clock, advance)
        self.assertEqual(calls[advance][2], {'logical_time': MAIL_OBSERVED})
        self.assertTrue(receipt['reset_verified'])

    def test_run_keeps_pilot_plans_away_from_the_controller_clock(self):
        calls = []
        receipt = self.run_capture(FakeMailDevice(), history_plan(pb_clock=False),
                                   'capture-pilot-clock', calls=calls)
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertNotIn('clock_binding', receipt)
        self.assertNotIn('environment', receipt)
        self.assertEqual([entry for entry in calls if entry[1] == '/local/v1/clock'], [])

    def test_run_rejects_a_packet_before_the_pb_observation_start(self):
        device = FakeMailDevice()
        calls = []
        plan = history_plan(pb_clock=True)
        plan['pb_clock_binding']['observation_start'] = '2026-09-14T08:32:00+08:00'
        plan['initial_simulated_time'] = '2026-09-14T08:32:00+08:00'
        receipt = self.run_capture(device, plan, 'capture-clock-backwards', calls=calls)
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('precedes the PB observation_start', receipt['failure_reason'])
        self.assertEqual(device.calls, [])
        self.assertEqual(calls, [])
        self.assertFalse(receipt['reset_verified'])

    def test_run_rejects_a_packet_after_the_pb_decision_point(self):
        plan = history_plan(pb_clock=True)
        plan['packets'][0]['observed_at'] = '2026-09-14T08:35:02+08:00'
        plan['packets'][0]['delivered_at'] = '2026-09-14T08:35:02+08:00'
        receipt = self.run_capture(FakeMailDevice(), plan, 'capture-clock-after-eof')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('after the PB decision_point', receipt['failure_reason'])

    def test_run_rejects_a_timezone_mismatch(self):
        plan = history_plan(pb_clock=True)
        plan['pb_clock_binding']['timezone'] = 'UTC'
        receipt = self.run_capture(FakeMailDevice(), plan, 'capture-clock-timezone')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('does not match the plan timezone', receipt['failure_reason'])
        plan = history_plan(pb_clock=True)
        plan['packets'][0]['observed_at'] = '2026-09-14T00:31:48+00:00'
        plan['packets'][0]['delivered_at'] = '2026-09-14T00:31:48+00:00'
        receipt = self.run_capture(FakeMailDevice(), plan, 'capture-clock-offset')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('is not in timezone', receipt['failure_reason'])


CALENDAR_REF = 'calendar.event.cal_001'
CALENDAR_TITLE = '项目评审 R7K2'
CALENDAR_OBSERVED = '2026-09-14T08:30:44+08:00'
CALENDAR_EPISODE = 'ep_0004'
CALENDAR_DATA = {'title': CALENDAR_TITLE, 'start_at': '2026-09-14T09:00:00+08:00',
                 'end_at': '2026-09-14T09:30:00+08:00', 'location': '会议室 A',
                 'description': '项目评审 R7K2：开场演示后进入讨论。', 'reminder_minutes_before': -1,
                 'source': 'calendar'}
CALENDAR_ITEM_BOUNDS = '[10,300][700,360]'
CALENDAR_DAY_BOUNDS = '[600,900][750,1050]'
CALENDAR_DAY_DESC = '14 September'
CALENDAR_SEARCH_BOUNDS = '[900,100][1000,200]'


def bounds_center(bounds):
    x1, y1, x2, y2 = (int(value) for value in bounds.strip('[]').replace('][', ',').split(','))
    return (x1 + x2) // 2, (y1 + y2) // 2


def calendar_row():
    data = CALENDAR_DATA
    return {'id': 17, 'title': data['title'],
            'start_ts': int(datetime.fromisoformat(data['start_at']).timestamp()),
            'end_ts': int(datetime.fromisoformat(data['end_at']).timestamp()),
            'location': data['location'], 'description': data['description'],
            'reminder_1_minutes': data['reminder_minutes_before'], 'source': data['source'],
            'time_zone': 'Asia/Shanghai',
            'import_id': event_key(CALENDAR_EPISODE, CALENDAR_REF)}


def calendar_plan(navigation_mode=None, timeout=5):
    capture = {'surface': 'app_screen', 'app_id': 'calendar', 'view_id': 'event_editor',
               'subject_ref': CALENDAR_REF,
               'visible_assertions': ['title', 'location', 'description', 'start_at', 'end_at'],
               'expected_visible': {'title': CALENDAR_TITLE,
                                    'location': CALENDAR_DATA['location'],
                                    'description': CALENDAR_DATA['description'],
                                    'start_at': '09:00 AM', 'end_at': '09:30 AM'},
               'frames': [{'frame_id': 'p004_f1'}], 'timeout_seconds': timeout}
    if navigation_mode is not None:
        capture['navigation_mode'] = navigation_mode
    packet = {'packet_id': 'p004', 'sequence': 1, 'tag': 'gui', 'source': 'calendar',
              'observed_at': CALENDAR_OBSERVED, 'delivered_at': CALENDAR_OBSERVED,
              'text_event': 'p004 summary', 'capture': capture}
    record = {'app_id': 'calendar', 'entity_type': 'events', 'semantic_ref': CALENDAR_REF,
              'data': CALENDAR_DATA}
    return {'schema_version': 'pb-gui-private-capture-plan.v1', 'episode_id': CALENDAR_EPISODE,
            'baseline_ref': 'init_state', 'device_timezone': 'Asia/Shanghai',
            'source_assets': {'gui_plan': {'sha256': 'a' * 64}},
            'fixture': {'episode_scope': CALENDAR_EPISODE, 'common_records': [], 'world_deltas': [],
                        'ku_records': [record]},
            'packets': [packet], 'eof_at': '2026-09-14T08:35:01+08:00'}


def calendar_list_plan(description=None, navigation_mode='direct_event', timeout=5):
    """p003/p018 day-view list plan: an exact row assertion without the title tap."""
    expected = {'day_label': 'September 14 (Mon)', 'event_title': CALENDAR_TITLE,
                'event_time': '09:00 AM - 09:30 AM',
                'event_description': description or CALENDAR_DATA['description']}
    capture = {'surface': 'app_screen', 'app_id': 'calendar', 'view_id': 'calendar_list',
               'subject_ref': CALENDAR_REF, 'visible_assertions': list(expected),
               'expected_visible': expected, 'frames': [{'frame_id': 'p003_f1'}],
               'timeout_seconds': timeout}
    if navigation_mode is not None:
        capture['navigation_mode'] = navigation_mode
    packet = {'packet_id': 'p003', 'sequence': 1, 'tag': 'gui', 'source': 'calendar',
              'observed_at': '2026-09-14T08:30:30+08:00',
              'delivered_at': '2026-09-14T08:30:30+08:00',
              'text_event': 'p003 summary', 'capture': capture}
    record = {'app_id': 'calendar', 'entity_type': 'events', 'semantic_ref': CALENDAR_REF,
              'data': CALENDAR_DATA}
    return {'schema_version': 'pb-gui-private-capture-plan.v1', 'episode_id': CALENDAR_EPISODE,
            'baseline_ref': 'init_state', 'device_timezone': 'Asia/Shanghai',
            'source_assets': {'gui_plan': {'sha256': 'a' * 64}},
            'fixture': {'episode_scope': CALENDAR_EPISODE, 'common_records': [], 'world_deltas': [],
                        'ku_records': [record]},
            'packets': [packet], 'eof_at': '2026-09-14T08:35:01+08:00'}


class FakeCalendarDevice:
    """adb/uiautomator double for the real month -> day -> editor capture path.

    The month grid exposes the event date as a content description but never the
    event title; opening the day view makes the title tappable, and only then does
    the editor appear. The editor text mirrors the device: zero-padded 12-hour
    time and a title that is also a prefix of the description.
    """
    def __init__(self, item_count=1, wall_clock=DEVICE_WALL_CLOCK.encode()):
        self.item_count = item_count
        self.wall_clock = wall_clock
        self.rows = [calendar_row()]
        self.calls = []
        self.seeded = False
        self.search_open = False
        self.day_opened = False
        self.event_opened = False
        self.reminder_log = []

    def adb(self, serial, *args, timeout=30):
        self.calls.append(args)
        if args[:3] == ('shell', 'date', '-Is'):
            return self.wall_clock
        if len(args) == 2 and args[0] == 'shell' and 'sqlite3' in args[1]:
            sql = args[1]
            if 'INSERT' in sql:
                self.seeded = True
                return b'[]'
            if 'UPDATE events SET' in sql:
                for column in ('reminder_1_minutes', 'reminder_2_minutes',
                               'reminder_3_minutes', 'last_updated'):
                    match = re.search(rf'{column}=(-?\d+)', sql)
                    if match:
                        self.rows[0][column] = int(match.group(1))
                self.reminder_log.append(dict(self.rows[0]))
                return b'1'
            return json.dumps(self.rows if self.seeded else []).encode()
        if args[:2] == ('exec-out', 'screencap'):
            return png()
        if args[:3] == ('shell', 'dumpsys', 'activity'):
            return f'topResumedActivity={CALENDAR_PACKAGE}/.MainActivity'.encode()
        if args[:3] == ('shell', 'input', 'tap'):
            point = (int(args[3]), int(args[4]))
            if point == bounds_center(CALENDAR_SEARCH_BOUNDS):
                self.search_open = True
            elif point == bounds_center(CALENDAR_DAY_BOUNDS):
                self.day_opened = True
            elif point == bounds_center(CALENDAR_ITEM_BOUNDS):
                self.event_opened = True
            return b''
        return b''

    def reset(self):
        self.seeded = False
        self.search_open = False
        self.day_opened = False
        self.event_opened = False
        self.rows = [calendar_row()]

    def ui_text(self, serial):
        xml = self.editor_xml() if self.event_opened else self.day_xml() if self.day_opened \
            else self.month_xml()
        return ElementTree.fromstring(xml), xml.encode()

    def month_xml(self):
        nodes = [f'<node package="{CALENDAR_PACKAGE}" '
                 f'resource-id="{CALENDAR_PACKAGE}:id/top_toolbar_search_icon" text="" '
                 f'bounds="{CALENDAR_SEARCH_BOUNDS}"/>',
                 f'<node package="{CALENDAR_PACKAGE}" content-desc="{CALENDAR_DAY_DESC}" '
                 f'text="" bounds="{CALENDAR_DAY_BOUNDS}"/>']
        if self.search_open:
            for position in range(self.item_count):
                top = 300 + position * 100
                nodes.append(f'<node package="{CALENDAR_PACKAGE}" '
                             f'resource-id="{CALENDAR_PACKAGE}:id/event_item_title" '
                             f'text="{CALENDAR_TITLE}" '
                             f'bounds="[10,{top}][700,{top + 60}]"/>')
        return '<root>' + ''.join(nodes) + '</root>'

    def day_xml(self):
        data = CALENDAR_DATA
        rows = []
        for position in range(self.item_count):
            top = 300 + position * 100
            rows.append(
                f'<node package="{CALENDAR_PACKAGE}" '
                f'resource-id="{CALENDAR_PACKAGE}:id/event_item_holder" '
                f'bounds="[10,{top - 10}][1038,{top + 180}]">'
                f'<node package="{CALENDAR_PACKAGE}" '
                f'resource-id="{CALENDAR_PACKAGE}:id/event_item_title" text="{CALENDAR_TITLE}" '
                f'bounds="[10,{top}][700,{top + 60}]"/>'
                f'<node package="{CALENDAR_PACKAGE}" '
                f'resource-id="{CALENDAR_PACKAGE}:id/event_item_time" text="09:00 AM - 09:30 AM" '
                f'bounds="[10,{top + 70}][446,{top + 120}]"/>'
                f'<node package="{CALENDAR_PACKAGE}" '
                f'resource-id="{CALENDAR_PACKAGE}:id/event_item_description" '
                f'text="{data["description"]}" bounds="[10,{top + 130}][1038,{top + 180}]"/>'
                f'</node>')
        return ('<root>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/day_holder" '
                f'bounds="[0,300][1080,2337]">'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/top_value" '
                f'text="September 14 (Mon)" bounds="[147,321][933,463]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/day_events" '
                f'bounds="[0,463][1080,2337]">' + ''.join(rows) + '</node></node></root>')

    def editor_xml(self):
        data = CALENDAR_DATA
        return ('<root>'
                f'<node package="{CALENDAR_PACKAGE}" text="Edit Event" bounds="[10,10][220,60]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/event_title" '
                f'text="{CALENDAR_TITLE}" bounds="[10,100][700,160]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/event_location" '
                f'text="{data["location"]}" bounds="[10,200][700,260]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/event_description" '
                f'text="{data["description"]}" bounds="[10,280][900,420]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/event_start_time" '
                f'text="09:00 AM" bounds="[10,500][300,560]"/>'
                f'<node package="{CALENDAR_PACKAGE}" resource-id="{CALENDAR_PACKAGE}:id/event_end_time" '
                f'text="09:30 AM" bounds="[10,600][300,660]"/>'
                '</root>')

    def input_calls(self, action):
        return [args for args in self.calls if args[:3] == ('shell', 'input', action)]

    def monkey_calls(self):
        return [args for args in self.calls if args[:2] == ('shell', 'monkey')]


class RunCalendarDirectEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        instance = self.root / 'runtime/instances/i1'
        instance.mkdir(parents=True)
        (instance / 'instance.json').write_text(json.dumps(
            {'snapshot': 'init_state', 'baseline_version': 'sha256:' + 'b' * 64,
             'dataset_version': 'ds-1', 'adb_serial': 'emulator-5554', 'controller_port': 1,
             'runner_key': 'runner-key', 'ku_container': ''}))

    def run_capture(self, device, plan, name):
        capture = self.root / name

        def fake_request(base, key, path, data=None):
            if path == '/local/v1/device/status':
                return {'available': True, 'boot_completed': True}
            return {}

        with unittest.mock.patch.object(gui_capture, 'ROOT', self.root), \
                unittest.mock.patch.object(gui_capture, 'reset_instance',
                                           side_effect=lambda instance: device.reset()), \
                unittest.mock.patch.object(gui_capture, 'request', side_effect=fake_request), \
                unittest.mock.patch.object(gui_capture, 'adb', side_effect=device.adb), \
                unittest.mock.patch.object(gui_capture, 'ui_text', side_effect=device.ui_text), \
                unittest.mock.patch.object(gui_capture, 'read_device_env',
                                           return_value=dict(DEVICE_ENVIRONMENT)), \
                unittest.mock.patch.object(gui_capture, 'set_device_time',
                                           side_effect=lambda serial, observed, tz: observed), \
                unittest.mock.patch.object(gui_capture, 'suppress_unrelated_notifications'):
            return gui_capture.run(copy.deepcopy(plan), 'i1', capture)

    def test_pb_direct_event_taps_the_unique_event_item_without_input_text(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_plan(navigation_mode='direct_event'),
                                   'capture-direct-event')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertEqual(device.input_calls('text'), [])
        self.assertNotIn(('shell', 'input', 'tap') + tuple(str(value) for value in
                                                           bounds_center(CALENDAR_SEARCH_BOUNDS)),
                         device.calls)
        self.assertIn(('shell', 'input', 'tap') + tuple(str(value) for value in
                                                        bounds_center(CALENDAR_DAY_BOUNDS)),
                      device.calls)
        self.assertIn(('shell', 'input', 'tap') + tuple(str(value) for value in
                                                        bounds_center(CALENDAR_ITEM_BOUNDS)),
                      device.calls)
        self.assertEqual(device.monkey_calls(), [('shell', 'monkey', '-p', CALENDAR_PACKAGE, '1')])
        self.assertEqual([(frame['packet_id'], frame['frame_id']) for frame in receipt['frames']],
                         [('p004', 'p004_f1')])
        self.assertEqual(receipt['frames'][0]['navigation'],
                         ['calendar:direct-event-day', 'calendar:direct-event-item',
                          'wait:event_editor'])
        self.assertEqual(receipt['frames'][0]['ready']['visible_assertions'],
                         {field: True for field in
                          ('title', 'location', 'description', 'start_at', 'end_at')})

    def test_legacy_calendar_plan_keeps_the_title_search_path(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_plan(), 'capture-legacy-search')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertIn(('shell', 'input', 'text', CALENDAR_TITLE), device.calls)
        self.assertIn(('shell', 'input', 'tap') + tuple(str(value) for value in
                                                        bounds_center(CALENDAR_SEARCH_BOUNDS)),
                      device.calls)
        self.assertIn(('shell', 'input', 'tap') + tuple(str(value) for value in
                                                        bounds_center(CALENDAR_ITEM_BOUNDS)),
                      device.calls)
        self.assertEqual(receipt['frames'][0]['navigation'],
                         ['calendar:search-title', 'calendar:tap-event-editor',
                          'wait:event_editor'])

    def test_direct_event_missing_item_fails_closed(self):
        device = FakeCalendarDevice(item_count=0)
        receipt = self.run_capture(device,
                                   calendar_plan(navigation_mode='direct_event', timeout=.2),
                                   'capture-direct-missing')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('not unique on screen', receipt['failure_reason'])
        self.assertEqual(receipt['frames'], [])
        self.assertEqual(device.input_calls('text'), [])
        self.assertEqual(device.input_calls('tap'),
                         [('shell', 'input', 'tap') + tuple(str(value) for value in
                                                             bounds_center(CALENDAR_DAY_BOUNDS))])

    def test_direct_event_duplicate_items_fail_closed(self):
        device = FakeCalendarDevice(item_count=2)
        receipt = self.run_capture(device,
                                   calendar_plan(navigation_mode='direct_event', timeout=.2),
                                   'capture-direct-duplicate')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('not unique on screen', receipt['failure_reason'])
        self.assertEqual(receipt['frames'], [])
        self.assertEqual(device.input_calls('tap'),
                         [('shell', 'input', 'tap') + tuple(str(value) for value in
                                                             bounds_center(CALENDAR_DAY_BOUNDS))])

    def test_unknown_navigation_mode_fails_closed_before_navigation(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_plan(navigation_mode='search'),
                                   'capture-unknown-mode')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('unsupported navigation_mode', receipt['failure_reason'])
        self.assertEqual(receipt['frames'], [])
        self.assertEqual(device.input_calls('text'), [])
        self.assertEqual(device.input_calls('tap'), [])
        self.assertEqual(device.monkey_calls(), [])

    def test_calendar_list_stops_on_the_day_view_without_tapping_the_event(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_list_plan(), 'capture-calendar-list')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertFalse(device.event_opened)
        self.assertEqual(device.input_calls('text'), [])
        self.assertEqual([args for args in device.calls if args[:3] == ('shell', 'input', 'tap')],
                         [('shell', 'input', 'tap') + tuple(str(value) for value in
                                                            bounds_center(CALENDAR_DAY_BOUNDS))])
        self.assertEqual(receipt['frames'][0]['navigation'],
                         ['calendar:direct-event-day', 'wait:calendar_list'])
        self.assertEqual(receipt['frames'][0]['ready']['visible_assertions'],
                         {'day_label': True, 'event_title': True, 'event_time': True,
                          'event_description': True})

    def test_calendar_list_wrong_event_row_fails_closed(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_list_plan(description='别的描述', timeout=.2),
                                   'capture-calendar-list-wrong')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('calendar day list not visible', receipt['failure_reason'])
        self.assertFalse(device.event_opened)
        self.assertEqual(receipt['frames'], [])

    def test_calendar_list_requires_direct_navigation(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_list_plan(navigation_mode=None),
                                   'capture-calendar-list-search')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('navigation_mode', receipt['failure_reason'])
        self.assertEqual(device.input_calls('text'), [])
        self.assertEqual(device.monkey_calls(), [])

    def calendar_history_plan(self):
        plan = calendar_plan(navigation_mode='direct_event')
        packet = plan['packets'][0]
        packet['observed_at'] = CALENDAR_HISTORY_OBSERVED
        packet['delivered_at'] = CALENDAR_HISTORY_OBSERVED
        packet['history_replays'] = [{'semantic_ref': CALENDAR_REF,
                                      'effective_at': CALENDAR_HISTORY_EFFECTIVE,
                                      'set': {'calendar_reminder_minutes': [10, -1, -1],
                                              'updated_at': CALENDAR_HISTORY_EFFECTIVE}}]
        return plan

    def test_run_replays_the_calendar_reminder_before_navigation_and_aligns_readback(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, self.calendar_history_plan(), 'capture-calendar-history')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertTrue(receipt['reset_verified'])
        self.assertTrue(device.reminder_log)
        self.assertEqual(device.reminder_log[-1]['reminder_1_minutes'], 10)
        self.assertEqual(device.reminder_log[-1]['last_updated'],
                         int(datetime.fromisoformat(CALENDAR_HISTORY_EFFECTIVE).timestamp() * 1000))
        self.assertEqual(receipt['history_replays'], [{
            'packet_id': 'p004', 'semantic_ref': CALENDAR_REF,
            'effective_at': CALENDAR_HISTORY_EFFECTIVE,
            'set': {'calendar_reminder_minutes': [10, -1, -1],
                    'updated_at': CALENDAR_HISTORY_EFFECTIVE},
            'function': 'apply_history_change',
            'result': {'semantic_ref': CALENDAR_REF,
                       'import_id': event_key(CALENDAR_EPISODE, CALENDAR_REF),
                       'reminder_1_minutes': 10, 'reminder_2_minutes': -1,
                       'reminder_3_minutes': -1,
                       'last_updated': int(datetime.fromisoformat(
                           CALENDAR_HISTORY_EFFECTIVE).timestamp() * 1000)}}])
        updates = [index for index, args in enumerate(device.calls)
                   if 'UPDATE events SET' in args[-1]]
        navigation = [index for index, args in enumerate(device.calls)
                      if args[:2] == ('shell', 'monkey')]
        self.assertTrue(updates and navigation and max(updates) < min(navigation))
        self.assertEqual(receipt['frames'][0]['readback']['reminder_1_minutes'], 10)
        self.assertEqual(receipt['readback']['calendar'][0]['reminder_1_minutes'], 10)
        self.assertEqual(device.input_calls('text'), [])

    def test_run_aborts_when_the_calendar_replay_fails(self):
        device = FakeCalendarDevice()
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change',
                                        side_effect=RuntimeError('Calendar history replay must '
                                                                 'affect exactly one row, affected 0')):
            receipt = self.run_capture(device, self.calendar_history_plan(),
                                       'capture-calendar-history-failed')
        self.assertEqual(receipt['status'], 'capture_failed')
        self.assertIn('history_replays[1] failed', receipt['failure_reason'])
        self.assertIn('affected 0', receipt['failure_reason'])
        self.assertEqual(device.monkey_calls(), [])
        self.assertEqual(receipt['frames'], [])
        self.assertTrue(receipt['reset_verified'])

    def test_run_leaves_plans_without_calendar_history_unchanged(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_plan(navigation_mode='direct_event'),
                                   'capture-calendar-nohistory')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertNotIn('history_replays', receipt)
        self.assertEqual(device.rows[0]['reminder_1_minutes'], -1)

    def test_each_frame_samples_the_device_clock_before_screencap(self):
        device = FakeCalendarDevice()
        receipt = self.run_capture(device, calendar_plan(navigation_mode='direct_event'),
                                   'capture-clock-order')
        self.assertEqual(receipt['status'], 'capture_complete', receipt['failure_reason'])
        self.assertEqual(receipt['frames'][0]['android_time'], DEVICE_WALL_CLOCK)
        self.assertIsNotNone(datetime.fromisoformat(
            receipt['frames'][0]['android_time']).utcoffset())
        dates = [index for index, args in enumerate(device.calls)
                 if args[:3] == ('shell', 'date', '-Is')]
        screenshots = [index for index, args in enumerate(device.calls)
                       if args[:2] == ('exec-out', 'screencap')]
        self.assertEqual(len(dates), 1)
        self.assertEqual(len(screenshots), 1)
        self.assertEqual(dates[0] + 1, screenshots[0])

    def test_missing_or_invalid_device_clock_fails_the_capture_closed(self):
        cases = (('missing', b'', 'unavailable'),
                 ('non-iso', b'not-a-timestamp', 'ISO-8601'),
                 ('naive', b'2026-09-14T08:30:44', 'timezone'))
        for label, wall_clock, message in cases:
            with self.subTest(wall_clock=wall_clock):
                device = FakeCalendarDevice(wall_clock=wall_clock)
                receipt = self.run_capture(device, calendar_plan(navigation_mode='direct_event'),
                                           f'capture-clock-{label}')
                self.assertEqual(receipt['status'], 'capture_failed')
                self.assertIn(message, receipt['failure_reason'])
                self.assertEqual(receipt['frames'], [])
                self.assertEqual([args for args in device.calls
                                  if args[:2] == ('exec-out', 'screencap')], [])


CALENDAR_HISTORY_EFFECTIVE = '2026-09-14T08:31:00+08:00'
CALENDAR_HISTORY_OBSERVED = '2026-09-14T08:31:35+08:00'


def calendar_history_record(minutes=(10, -1, -1), updated_at=CALENDAR_HISTORY_EFFECTIVE,
                            ref=CALENDAR_REF, entity='events', data=None):
    if data is None:
        data = {'calendar_reminder_minutes': list(minutes), 'updated_at': updated_at}
    record = {'semantic_ref': ref, 'entity_type': entity, 'data': data}
    return record


class FakeEventTable:
    """events.db double: identity lookup, a real UPDATE, changed-row count and readback."""
    def __init__(self, identities=1, affected=1, apply_update=True):
        self.rows = [{'id': index + 1,
                      'import_id': event_key(CALENDAR_EPISODE, CALENDAR_REF),
                      'reminder_1_minutes': -1, 'reminder_2_minutes': -1,
                      'reminder_3_minutes': -1, 'last_updated': 0}
                     for index in range(identities)]
        self.affected = affected
        self.apply_update = apply_update
        self.calls = []

    def adb(self, serial, *args, timeout=30):
        self.calls.append(args)
        sql = args[-1]
        if 'SELECT id FROM events WHERE import_id' in sql:
            return json.dumps([{'id': row['id']} for row in self.rows]).encode()
        if 'UPDATE events SET' in sql:
            if self.apply_update:
                for column in ('reminder_1_minutes', 'reminder_2_minutes',
                               'reminder_3_minutes', 'last_updated'):
                    match = re.search(rf'{column}=(-?\d+)', sql)
                    if match:
                        for row in self.rows:
                            row[column] = int(match.group(1))
            return str(self.affected).encode()
        if 'SELECT reminder_1_minutes' in sql:
            columns = ('reminder_1_minutes', 'reminder_2_minutes', 'reminder_3_minutes',
                       'last_updated')
            return json.dumps([{column: row[column] for column in columns}
                               for row in self.rows]).encode()
        raise AssertionError(sql)


class CalendarHistoryChangeTest(unittest.TestCase):
    """Strict Calendar reminder replay: one import_id, one row, full readback."""

    def apply(self, table, record, effective_at=CALENDAR_HISTORY_EFFECTIVE):
        return gui_calendar.apply_history_change(table.adb, 'emulator-5554', CALENDAR_EPISODE,
                                                 record, effective_at)

    def test_applies_the_declared_reminder_and_reads_all_columns_back(self):
        table = FakeEventTable()
        result = self.apply(table, calendar_history_record())
        expected_ms = int(datetime.fromisoformat(CALENDAR_HISTORY_EFFECTIVE).timestamp() * 1000)
        self.assertEqual(result, {'semantic_ref': CALENDAR_REF,
                                 'import_id': event_key(CALENDAR_EPISODE, CALENDAR_REF),
                                 'reminder_1_minutes': 10, 'reminder_2_minutes': -1,
                                 'reminder_3_minutes': -1, 'last_updated': expected_ms})
        self.assertEqual(table.rows[0]['reminder_1_minutes'], 10)
        self.assertEqual(table.rows[0]['last_updated'], expected_ms)
        update = next(args[-1] for args in table.calls if 'UPDATE events SET' in args[-1])
        self.assertIn(f'WHERE import_id=', update)
        self.assertIn(event_key(CALENDAR_EPISODE, CALENDAR_REF), update)
        self.assertIn('reminder_1_minutes=10,reminder_2_minutes=-1,reminder_3_minutes=-1', update)
        self.assertIn(';SELECT changes();', update)
        reads = [index for index, args in enumerate(table.calls)
                 if 'SELECT reminder_1_minutes' in args[-1]]
        updates = [index for index, args in enumerate(table.calls)
                   if 'UPDATE events SET' in args[-1]]
        self.assertTrue(reads and updates and max(updates) < min(reads))

    def test_unknown_fields_entities_and_wrong_types_never_touch_the_device(self):
        table = FakeEventTable()
        invalid = (
            (calendar_history_record(data={'calendar_reminder_minutes': [10, -1, -1],
                                           'updated_at': CALENDAR_HISTORY_EFFECTIVE,
                                           'status': 'read'}), 'unknown fields'),
            (calendar_history_record(data={'calendar_reminder_minutes': [10, -1, -1]}),
             'misses declared fields'),
            (calendar_history_record(minutes=(10, -1)), 'three-element integer list'),
            (calendar_history_record(minutes=(10, -1, True)), 'three-element integer list'),
            (calendar_history_record(minutes='10,-1,-1'), 'three-element integer list'),
            (calendar_history_record(entity='mails'), 'rejects entity type'),
            (calendar_history_record(ref=''), 'nonempty semantic_ref'),
            (calendar_history_record(updated_at='08:31:00'), 'not an ISO-8601 timestamp'),
        )
        for record, message in invalid:
            with self.assertRaisesRegex(RuntimeError, message):
                self.apply(table, record)
        with self.assertRaisesRegex(RuntimeError, 'must carry a UTC offset'):
            self.apply(table, calendar_history_record(), effective_at='2026-09-14T08:31:00')
        with self.assertRaisesRegex(RuntimeError, 'does not match the declared effective_at'):
            self.apply(table, calendar_history_record(updated_at='2026-09-14T08:30:00+08:00'))
        self.assertEqual(table.calls, [])

    def test_missing_duplicate_identity_and_wrong_affected_rows_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'missing or ambiguous'):
            self.apply(FakeEventTable(identities=0), calendar_history_record())
        with self.assertRaisesRegex(RuntimeError, 'missing or ambiguous'):
            self.apply(FakeEventTable(identities=2), calendar_history_record())
        with self.assertRaisesRegex(RuntimeError, 'exactly one row, affected 0'):
            self.apply(FakeEventTable(affected=0), calendar_history_record())
        with self.assertRaisesRegex(RuntimeError, 'exactly one row, affected 2'):
            self.apply(FakeEventTable(affected=2), calendar_history_record())

    def test_readback_that_differs_from_the_declared_change_fails_closed(self):
        table = FakeEventTable(apply_update=False)
        with self.assertRaisesRegex(RuntimeError, 'readback .* differs from the declared change'):
            self.apply(table, calendar_history_record())


def calendar_replay_packet(ref=CALENDAR_REF, effective_at=CALENDAR_HISTORY_EFFECTIVE,
                           observed_at=CALENDAR_HISTORY_OBSERVED, updates=None):
    if updates is None:
        updates = {'calendar_reminder_minutes': [10, -1, -1], 'updated_at': effective_at}
    return {'packet_id': 'p009', 'observed_at': observed_at,
            'history_replays': [{'semantic_ref': ref, 'effective_at': effective_at,
                                 'set': updates}]}


class CalendarCaptureReplayTest(unittest.TestCase):
    """gui_capture side: classify, validate and advance the Calendar fixture."""

    def setUp(self):
        self.plan = {'episode_id': CALENDAR_EPISODE, 'device_timezone': 'Asia/Shanghai'}
        self.records = [{'app_id': 'calendar', 'entity_type': 'events',
                         'semantic_ref': CALENDAR_REF, 'data': dict(CALENDAR_DATA)}]

    def test_replay_advances_the_fixture_and_records_the_readback(self):
        replayed = set()
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change',
                                        return_value={'reminder_1_minutes': 10}) as apply:
            result = gui_capture.apply_calendar_history_replays('device', 'emulator-5554', self.plan,
                                                                calendar_replay_packet(),
                                                                self.records, replayed)
        apply.assert_called_once()
        called = apply.call_args.args
        self.assertEqual(called[:3], ('device', 'emulator-5554', CALENDAR_EPISODE))
        self.assertEqual(called[3]['data'], {'calendar_reminder_minutes': [10, -1, -1],
                                             'updated_at': CALENDAR_HISTORY_EFFECTIVE})
        self.assertEqual(called[4], CALENDAR_HISTORY_EFFECTIVE)
        self.assertEqual(self.records[0]['data']['reminder_minutes_before'], 10)
        self.assertEqual(replayed, {CALENDAR_REF})
        self.assertEqual(result, [{'packet_id': 'p009', 'semantic_ref': CALENDAR_REF,
                                   'effective_at': CALENDAR_HISTORY_EFFECTIVE,
                                   'set': {'calendar_reminder_minutes': [10, -1, -1],
                                           'updated_at': CALENDAR_HISTORY_EFFECTIVE},
                                   'function': 'apply_history_change',
                                   'result': {'reminder_1_minutes': 10}}])

    def test_malformed_cross_entity_and_duplicate_replays_abort_before_any_write(self):
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change') as apply:
            with self.assertRaisesRegex(RuntimeError, 'only allows the declared Calendar'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan,
                    calendar_replay_packet(updates={'status': 'read'}), self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'three-element integer list'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan,
                    calendar_replay_packet(updates={'calendar_reminder_minutes': [10, -1],
                                                    'updated_at': CALENDAR_HISTORY_EFFECTIVE}),
                    self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'not a Calendar capture fixture'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan,
                    calendar_replay_packet(ref='mail.message.mail_001'), self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'not effective at the packet observed_at'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan,
                    calendar_replay_packet(effective_at='2026-09-14T08:32:00+08:00',
                                           observed_at=CALENDAR_HISTORY_OBSERVED),
                    self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'effective_at must be an ISO-8601'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan,
                    calendar_replay_packet(effective_at='08:31:00'), self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'updated_at must be a nonempty'):
                gui_capture.apply_calendar_history_replays(
                    'device', 'd', self.plan, calendar_replay_packet(
                        updates={'calendar_reminder_minutes': [10, -1, -1], 'updated_at': 0}),
                    self.records, set())
            with self.assertRaisesRegex(RuntimeError, 'replays .* twice'):
                gui_capture.apply_calendar_history_replays('device', 'd', self.plan,
                                                           calendar_replay_packet(), self.records,
                                                           {CALENDAR_REF})
        apply.assert_not_called()
        self.assertEqual(self.records[0]['data']['reminder_minutes_before'], -1)

    def test_device_failure_aborts_without_advancing_the_fixture(self):
        records = copy.deepcopy(self.records)
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change',
                                        side_effect=RuntimeError('Calendar history replay must '
                                                                 'affect exactly one row, affected 0')):
            with self.assertRaisesRegex(RuntimeError, r'history_replays\[1\] failed'):
                gui_capture.apply_calendar_history_replays('device', 'd', self.plan,
                                                           calendar_replay_packet(), records, set())
        self.assertEqual(records[0]['data']['reminder_minutes_before'], -1)

    def test_packet_dispatcher_keeps_mail_only_and_calendar_only_paths(self):
        mail_records = [{'app_id': 'mail', 'entity_type': 'mails',
                         'semantic_ref': 'mail.message.mail_001',
                         'data': {'headers': {'subject': 'GuiHistoryMail'}, 'status': 'unread'}}]
        mail_packet = {'packet_id': 'p010', 'observed_at': MAIL_OBSERVED,
                       'history_replays': [{'semantic_ref': 'mail.message.mail_001',
                                            'effective_at': MAIL_EFFECTIVE,
                                            'set': {'status': 'read'}}]}
        with unittest.mock.patch.object(gui_capture, 'apply_history_change',
                                        return_value='mail-hash') as mail_apply, \
                unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change') as cal:
            result = gui_capture.apply_packet_history_replays('device', 'd', self.plan, mail_packet,
                                                              self.records, mail_records, set())
        mail_apply.assert_called_once()
        cal.assert_not_called()
        self.assertEqual(result[0]['result'], 'mail-hash')
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change',
                                        return_value={'reminder_1_minutes': 10}) as cal:
            result = gui_capture.apply_packet_history_replays('device', 'd', self.plan,
                                                              calendar_replay_packet(), self.records,
                                                              mail_records, set())
        cal.assert_called_once()
        self.assertEqual(result[0]['semantic_ref'], CALENDAR_REF)

    def test_packet_dispatcher_orders_mixed_changes_by_effective_at(self):
        mail_records = [{'app_id': 'mail', 'entity_type': 'mails',
                         'semantic_ref': 'mail.message.mail_001',
                         'data': {'headers': {'subject': 'GuiHistoryMail'}, 'status': 'unread'}}]
        order = []
        packet = {'packet_id': 'p015', 'observed_at': '2026-09-14T08:33:05+08:00',
                  'history_replays': [
                      {'semantic_ref': CALENDAR_REF, 'effective_at': '2026-09-14T08:32:00+08:00',
                       'set': {'calendar_reminder_minutes': [10, -1, -1],
                               'updated_at': '2026-09-14T08:32:00+08:00'}},
                      {'semantic_ref': 'mail.message.mail_001',
                       'effective_at': '2026-09-14T08:31:45+08:00', 'set': {'status': 'read'}}]}
        with unittest.mock.patch.object(gui_capture, 'calendar_apply_history_change',
                                        side_effect=lambda *args: order.append('calendar') or {}), \
                unittest.mock.patch.object(gui_capture, 'apply_history_change',
                                           side_effect=lambda *args: order.append('mail') or 'hash'):
            gui_capture.apply_packet_history_replays('device', 'd', self.plan, packet,
                                                     self.records, mail_records, set())
        self.assertEqual(order, ['mail', 'calendar'])

    def test_packet_dispatcher_refuses_unknown_entities(self):
        with self.assertRaisesRegex(RuntimeError, 'not in the capture fixture'):
            gui_capture.apply_packet_history_replays('device', 'd', self.plan,
                                                     calendar_replay_packet(ref='clock.alarm.a1'),
                                                     self.records, [], set())


if __name__ == '__main__':
    unittest.main()
