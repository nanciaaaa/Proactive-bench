import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from scripts.gui_contact_search import ContactSearch


class ContactSearchTest(unittest.TestCase):
    def make_release(self, root, variant):
        release = root / f'release-{variant}'
        private = root / f'release-{variant}.private'
        private.mkdir()
        (private / 'private-assets').mkdir()
        target_phone = '13800138000' if variant == 'a' else '13900139000'
        anchor_phone = '13700137000' if variant == 'a' else '13800138000'
        plan = {
            'episode_id': f'formal-{variant}',
            'eof_at': '2026-09-25T10:01:00+08:00',
            'fixture': {'ku_records': [
                {'app_id': 'contacts', 'entity_type': 'contacts', 'semantic_ref': 'contact-01',
                 'data': {'name': 'GuiPilotTwin', 'phone': target_phone, 'email': 'target@example.test'}},
                {'app_id': 'contacts', 'entity_type': 'contacts', 'semantic_ref': 'message-lookup-01',
                 'data': {'name': 'GuiPilotTwin', 'phone': anchor_phone}},
                {'app_id': 'messages', 'entity_type': 'messages', 'semantic_ref': 'message-01',
                 'data': {'sender': 'GuiPilotTwin', 'sender_phone': '13800138000',
                          'body': 'Your contact update is ready.'}},
            ]},
            'formal': {'oracle': {'expected_need_set': [] if variant == 'a' else [
                {'type': 'update_contact_phone', 'target': 'contact-01', 'phone': '13800138000'}]}},
        }
        goal = {'type': 'sync_contact_identity', 'semantic_ref': 'contact-01',
                'source_message_ref': 'message-01', 'source_sender': 'GuiPilotTwin',
                'approved_phone': '13800138000', 'approved_body': 'Your contact update is ready.'}
        receipt = {'freeze': {'success': True, 'frozen': True}, 'reset_verified': True,
                    'readback': {'contacts': [
                        {'_id': 1, 'contact_id': 1, 'display_name': 'GuiPilotTwin', 'aggregation_mode': 3},
                        {'_id': 2, 'contact_id': 2, 'display_name': 'GuiPilotTwin', 'aggregation_mode': 3},
                    ]}}
        (private / 'private-plan.json').write_text(json.dumps(plan))
        (private / 'receipt.json').write_text(json.dumps(receipt))
        (private / 'private-assets' / 'cluster_base.json').write_text(json.dumps({'goal': goal}))
        return release

    def test_opposite_worlds_use_same_route_and_grade_private_need(self):
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.gui_contact_search.verify_release'):
            root = Path(tmp)
            public_routes = []
            for variant, expected in [('a', []), ('b', [{'type': 'update_contact_phone',
                                                          'target': 'target@example.test',
                                                          'phone': '13800138000'}])]:
                session = ContactSearch(self.make_release(root, variant))
                find = session.search('contacts.find_contact', {'name': 'GuiPilotTwin'})
                read = session.search('contacts.read_contact', {'contact_ref': 'c001'})
                message = session.search('messages.find_conversation', {
                    'sender': 'GuiPilotTwin', 'body': 'Your contact update is ready.'})
                conversation = session.search('messages.read_conversation', {'conversation_ref': 'm001'})
                public_routes.append([find['action_id'], read['action_id'], message['action_id'], conversation['action_id']])
                self.assertEqual(session.finish({'need_set': expected,
                                                 'evidence_ids': [read['evidence_id'], conversation['evidence_id']]}),
                                 {'status': 'submitted', 'queries_used': 4})
                self.assertEqual(session.grade(), {'need_set_correct': True, 'evidence_refs_valid': True,
                                                   'decision_grounded': True, 'queries_used': 4})
            self.assertEqual(public_routes[0], public_routes[1])

    def test_frozen_readback_and_budget_guard(self):
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.gui_contact_search.verify_release'):
            session = ContactSearch(self.make_release(Path(tmp), 'b'))
            for _ in range(6):
                self.assertEqual(session.search('unknown', {})['status'], 'invalid_action')
            self.assertEqual(session.search('contacts.find_contact', {})['status'], 'budget_exhausted')


if __name__ == '__main__':
    unittest.main()
