#!/usr/bin/env python3
"""Read-only frozen Contacts/Messages Search for the formal contact-update pilot."""
import argparse
import json
import sys
from pathlib import Path

from gui_publish import verify_release


class ContactSearch:
    def __init__(self, release_dir):
        release_dir = Path(release_dir)
        verify_release(release_dir)
        private = release_dir.with_name(release_dir.name + '.private')
        plan = json.loads((private / 'private-plan.json').read_text())
        receipt = json.loads((private / 'receipt.json').read_text())
        goal = json.loads((private / 'private-assets/cluster_base.json').read_text())['goal']
        if (goal.get('type') != 'sync_contact_identity'
                or receipt.get('freeze') != {'success': True, 'frozen': True}
                or receipt.get('reset_verified') is not True):
            raise ValueError('contact Search requires a frozen, reset, published capture')
        records = {record['semantic_ref']: record for record in plan['fixture']['ku_records']}
        target = records.get(goal.get('semantic_ref'))
        message = records.get(goal.get('source_message_ref'))
        contacts = [record for record in plan['fixture']['ku_records'] if record['app_id'] == 'contacts']
        rows = receipt.get('readback', {}).get('contacts')
        if (target is None or message is None or len(contacts) < 1 or not isinstance(rows, list)
                or len(rows) != len(contacts)):
            raise ValueError('contact target, source message, or Contacts readback is incomplete')
        if (message['data'].get('sender') != goal['source_sender']
                or message['data'].get('body') != goal['approved_body']):
            raise ValueError('frozen source message differs from private World')
        if any(row.get('display_name') != record['data']['name'] or row.get('aggregation_mode') != 3
               for row, record in zip(rows, contacts)):
            raise ValueError('frozen Contacts readback differs from private World')
        self.contacts = contacts
        self.target = target
        self.message = message
        self.target_ref = 'c001'
        self.message_ref = 'm001'
        self.oracle = plan['formal']['oracle']['expected_need_set']
        self.as_of = plan['eof_at']
        self.queries = 0
        self.ledger = []
        self.contacts_found = False
        self.message_found = False
        self.submission = None

    def search(self, action_id, args=None):
        if self.submission is not None:
            raise ValueError('run already finished')
        if self.queries >= 6:
            return {'status': 'budget_exhausted', 'queries_used': 6}
        self.queries += 1
        response = {'evidence_id': f'e{len(self.ledger) + 1:03d}', 'action_id': action_id,
                    'source': 'contacts' if str(action_id).startswith('contacts.') else 'messages',
                    'as_of': self.as_of, 'status': 'invalid_action'}
        if action_id == 'contacts.find_contact' and args == {'name': self.target['data']['name']}:
            self.contacts_found = True
            response.update(status='ok', operation='find_contact', contacts=[
                {'contact_ref': f'c{index:03d}', 'name': record['data']['name'],
                 'phone': record['data']['phone'], **({'email': record['data']['email']} if 'email' in record['data'] else {})}
                for index, record in enumerate(self.contacts, 1)])
        elif action_id == 'contacts.read_contact' and args == {'contact_ref': self.target_ref} and self.contacts_found:
            data = self.target['data']
            response.update(status='ok', operation='read_contact', contact_ref=self.target_ref,
                            name=data['name'], phone=data['phone'], **({'email': data['email']} if 'email' in data else {}))
        elif action_id == 'messages.find_conversation' and args == {
                'sender': self.message['data']['sender'], 'body': self.message['data']['body']}:
            self.message_found = True
            response.update(status='ok', operation='find_conversation', conversation_ref=self.message_ref,
                            sender=self.message['data']['sender'], body=self.message['data']['body'])
        elif action_id == 'messages.read_conversation' and args == {'conversation_ref': self.message_ref} and self.message_found:
            data = self.message['data']
            response.update(status='ok', operation='read_conversation', conversation_ref=self.message_ref,
                            sender=data['sender'], sender_phone=data['sender_phone'], body=data['body'])
        self.ledger.append(response)
        return response

    def finish(self, submission):
        if self.submission is not None:
            raise ValueError('run already finished')
        if (not isinstance(submission, dict) or set(submission) != {'need_set', 'evidence_ids'}
                or not isinstance(submission['need_set'], list)
                or not isinstance(submission['evidence_ids'], list)
                or any(not isinstance(item, str) for item in submission['evidence_ids'])):
            raise ValueError('invalid final submission')
        self.submission = submission
        return {'status': 'submitted', 'queries_used': self.queries}

    def grade(self):
        if self.submission is None:
            raise ValueError('run has no final submission')
        evidence = {item['evidence_id']: item for item in self.ledger}
        ids = self.submission['evidence_ids']
        valid = len(ids) == len(set(ids)) and all(item in evidence and evidence[item]['status'] == 'ok' for item in ids)
        grounded = valid and any(evidence[item].get('operation') == 'read_contact'
                                  and evidence[item].get('contact_ref') == self.target_ref for item in ids)
        expected = [{**need, 'target': self.target['data'].get('email', self.target['data']['name'])}
                    for need in self.oracle]
        correct = self.submission['need_set'] == expected
        return {'need_set_correct': correct, 'evidence_refs_valid': valid,
                'decision_grounded': correct and grounded, 'queries_used': self.queries}


def main():
    parser = argparse.ArgumentParser(description='Internal contact-update Search host')
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--private-result', type=Path, required=True)
    args = parser.parse_args()
    session = ContactSearch(args.release_dir)
    print(json.dumps({'status': 'ready', 'actions': ['contacts.find_contact', 'contacts.read_contact',
                                                      'messages.find_conversation', 'messages.read_conversation'],
                      'search_budget': 6}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = session.search(None, {})
            elif request.get('type') == 'finish':
                response = session.finish(request.get('submission'))
                if args.private_result.exists():
                    raise FileExistsError('private result already exists')
                args.private_result.write_text(json.dumps({'ledger': session.ledger,
                    'submission': session.submission, 'grade': session.grade()}, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps(response), flush=True)
                return
            else:
                response = session.search(request.get('action_id'), request.get('args'))
        except json.JSONDecodeError:
            response = session.search(None, {})
        except (ValueError, TypeError) as exc:
            response = {'status': 'invalid_request', 'error': str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
