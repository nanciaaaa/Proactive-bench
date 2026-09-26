import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gui_appointment_search import AppointmentSearch
from gui_calendar import event_key
from gui_contract import timestamp


class AppointmentSearchTest(unittest.TestCase):
    def test_frozen_readback_and_private_need(self):
        with tempfile.TemporaryDirectory() as tmp, patch('gui_appointment_search.verify_release'):
            release = Path(tmp) / 'release'
            private = Path(tmp) / 'release.private'
            (private / 'private-assets').mkdir(parents=True)
            start, end = '2026-09-25T10:30:00+08:00', '2026-09-25T11:00:00+08:00'
            data = {'title': 'Meeting', 'start_at': start, 'end_at': end,
                    'location': 'Online', 'description': 'Review', 'reminder_minutes_before': 30, 'source': 'manual'}
            record = {'app_id': 'calendar', 'semantic_ref': 'meeting-01', 'data': data}
            plan = {'episode_id': 'e1', 'device_timezone': 'Asia/Shanghai', 'eof_at': '2026-09-25T12:00:00+08:00',
                    'fixture': {'ku_records': [record]}, 'formal': {'oracle': {'expected_need_set': [
                        {'type': 'update_calendar_event', 'target': 'meeting-01',
                         'start_at': '2026-09-25T11:30:00+08:00', 'end_at': '2026-09-25T12:00:00+08:00'}]}}}
            row = {'id': 17, 'title': 'Meeting', 'start_ts': int(timestamp(start).timestamp()),
                   'end_ts': int(timestamp(end).timestamp()), 'location': 'Online', 'description': 'Review',
                   'reminder_1_minutes': 30, 'source': 'manual', 'time_zone': 'Asia/Shanghai',
                   'import_id': event_key('e1', 'meeting-01')}
            receipt = {'freeze': {'success': True, 'frozen': True}, 'reset_verified': True,
                       'readback': {'calendar': [row]}}
            (private / 'private-plan.json').write_text(json.dumps(plan))
            (private / 'receipt.json').write_text(json.dumps(receipt))
            (private / 'private-assets/cluster_base.json').write_text(json.dumps({'goal': {
                'type': 'sync_event_time', 'semantic_ref': 'meeting-01'}}))
            session = AppointmentSearch(release)
            self.assertEqual(session.search('calendar.read_event', {'event_ref': 'c001'})['status'], 'invalid_action')
            self.assertEqual(session.search('calendar.find_event', {'title': 'Meeting'})['status'], 'ok')
            evidence = session.search('calendar.read_event', {'event_ref': 'c001'})
            self.assertEqual(evidence['start_at'], start)
            need = [{'type': 'update_calendar_event', 'target': 'Meeting',
                     'start_at': '2026-09-25T11:30:00+08:00', 'end_at': '2026-09-25T12:00:00+08:00'}]
            session.finish({'need_set': need, 'evidence_ids': [evidence['evidence_id']]})
            self.assertTrue(session.grade()['decision_grounded'])
            row['start_ts'] += 60
            (private / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'readback differs'):
                AppointmentSearch(release)


if __name__ == '__main__':
    unittest.main()
