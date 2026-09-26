#!/usr/bin/env python3
"""Read-only frozen Calendar Search for the machine-published appointment pilot."""
import argparse
import json
import sys
from pathlib import Path

from gui_calendar import event_key
from gui_contract import timestamp
from gui_publish import verify_release


class AppointmentSearch:
    def __init__(self, release_dir):
        release_dir = Path(release_dir)
        verify_release(release_dir)
        private = release_dir.with_name(release_dir.name + '.private')
        plan = json.loads((private / 'private-plan.json').read_text())
        receipt = json.loads((private / 'receipt.json').read_text())
        goal = json.loads((private / 'private-assets/cluster_base.json').read_text())['goal']
        if (goal['type'] != 'sync_event_time' or receipt.get('freeze') != {'success': True, 'frozen': True}
                or receipt.get('reset_verified') is not True):
            raise ValueError('appointment Search requires a frozen, reset, published capture')
        records = [record for record in plan['fixture']['ku_records']
                   if record['app_id'] == 'calendar' and record['semantic_ref'] == goal['semantic_ref']]
        rows = receipt.get('readback', {}).get('calendar')
        if len(records) != 1 or not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('appointment target or Calendar readback is incomplete')
        record, row = records[0], rows[0]
        data = record['data']
        expected = {'title': data['title'], 'start_ts': int(timestamp(data['start_at']).timestamp()),
                    'end_ts': int(timestamp(data['end_at']).timestamp()), 'location': data['location'],
                    'description': data['description'], 'reminder_1_minutes': data['reminder_minutes_before'],
                    'source': data['source'], 'time_zone': plan['device_timezone'],
                    'import_id': event_key(plan['episode_id'], record['semantic_ref'])}
        if not isinstance(row, dict) or not isinstance(row.get('id'), int) or set(row) != set(expected) | {'id'} \
                or any(row[field] != value for field, value in expected.items()):
            raise ValueError('frozen Calendar readback differs from private World')
        self.title = data['title']
        self.start_at, self.end_at = data['start_at'], data['end_at']
        self.oracle = plan['formal']['oracle']['expected_need_set']
        self.as_of = plan['eof_at']
        self.queries = 0
        self.ledger = []
        self.found = False
        self.submission = None

    def search(self, action_id, args=None):
        if self.submission is not None:
            raise ValueError('run already finished')
        if self.queries >= 6:
            return {'status': 'budget_exhausted', 'queries_used': 6}
        self.queries += 1
        response = {'evidence_id': f'e{len(self.ledger) + 1:03d}', 'action_id': action_id,
                    'source': 'calendar', 'as_of': self.as_of, 'status': 'invalid_action'}
        if action_id == 'calendar.find_event' and args == {'title': self.title}:
            self.found = True
            response.update(status='ok', operation='find_event', event_ref='c001', title=self.title)
        elif action_id == 'calendar.read_event' and args == {'event_ref': 'c001'} and self.found:
            response.update(status='ok', operation='read_event', event_ref='c001', title=self.title,
                            start_at=self.start_at, end_at=self.end_at)
        self.ledger.append(response)
        return response

    def finish(self, submission):
        if self.submission is not None:
            raise ValueError('run already finished')
        if (not isinstance(submission, dict) or set(submission) != {'need_set', 'evidence_ids'}
                or not isinstance(submission['need_set'], list) or not isinstance(submission['evidence_ids'], list)
                or any(not isinstance(item, str) for item in submission['evidence_ids'])):
            raise ValueError('invalid final submission')
        self.submission = submission
        return {'status': 'submitted', 'queries_used': self.queries}

    def grade(self):
        if self.submission is None:
            raise ValueError('run has no final submission')
        ids = self.submission['evidence_ids']
        ledger = {item['evidence_id']: item for item in self.ledger}
        valid = len(ids) == len(set(ids)) and all(item in ledger and ledger[item]['status'] == 'ok' for item in ids)
        grounded = valid and any(ledger[item].get('operation') == 'read_event' for item in ids)
        expected = [{**need, 'target': self.title} for need in self.oracle]
        correct = self.submission['need_set'] == expected
        return {'need_set_correct': correct, 'evidence_refs_valid': valid,
                'decision_grounded': correct and grounded, 'queries_used': self.queries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--private-result', type=Path, required=True)
    args = parser.parse_args()
    session = AppointmentSearch(args.release_dir)
    print(json.dumps({'status': 'ready', 'actions': ['calendar.find_event', 'calendar.read_event'],
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
