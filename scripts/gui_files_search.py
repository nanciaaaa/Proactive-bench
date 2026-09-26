"""Frozen Files pilot Search and private Need scoring; never hand the plan to an agent."""
import hashlib
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

from gui_publish import verify_release


class FilesSearch:
    def __init__(self, release_dir):
        release_dir = Path(release_dir)
        verify_release(release_dir)
        capture_dir = release_dir.with_name(release_dir.name + '.private')
        raw = (capture_dir / 'private-plan.json').read_bytes()
        plan = json.loads(raw)
        receipt = json.loads((capture_dir / 'receipt.json').read_text())
        if ('formal' not in plan or receipt.get('status') != 'capture_complete'
                or receipt.get('reset_verified') is not True
                or receipt.get('plan_sha256') != hashlib.sha256(raw).hexdigest()
                or receipt.get('episode_id') != plan['episode_id']
                or receipt.get('source_assets') != plan['source_assets']
                or receipt.get('freeze') != {'success': True, 'frozen': True}):
            raise ValueError('Files Search requires a verified frozen formal capture')
        for name, digest in plan['source_assets'].items():
            snapshot = capture_dir / 'private-assets' / f'{name}.json'
            if not snapshot.is_file() or snapshot.is_symlink() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
                raise ValueError('Files Search source snapshot changed')
        expected_frames = [(p['packet_id'], f['frame_id']) for p in plan['packets'] for f in p['capture']['frames']]
        if [(f['packet_id'], f['frame_id']) for f in receipt['frames']] != expected_frames:
            raise ValueError('Files Search capture frames are incomplete')
        records = [r for r in plan['fixture']['ku_records'] if r['app_id'] == 'files']
        readback = receipt.get('readback', {}).get('files')
        if not records or not isinstance(readback, list) or len(readback) != len(records):
            raise ValueError('Files frozen readback is incomplete')
        for record, observed in zip(records, readback):
            data = record['data']
            expected = {'path': '/sdcard/' + data['path'],
                        'sha256': hashlib.sha256(data['content'].encode()).hexdigest(),
                        'modified_at': int(datetime.fromisoformat(data['modified_at']).timestamp())}
            if observed != expected:
                raise ValueError('Files frozen readback differs from private World')
        self.records = records
        self.public_refs = {f'f{i:03d}': record for i, record in enumerate(records, 1)}
        goal = json.loads((capture_dir / 'private-assets/cluster_base.json').read_text())['goal']
        self.target_ref = next(ref for ref, record in self.public_refs.items()
                               if record['semantic_ref'] == goal['semantic_ref'])
        self.oracle = plan['formal']['oracle']
        self.as_of = plan['eof_at']
        self.queries = 0
        self.ledger = []
        self.unlocked = False
        self.submission = None

    def search(self, action_id, args=None):
        if self.submission is not None:
            raise ValueError('run already finished')
        if self.queries >= 6:
            return {'status': 'budget_exhausted', 'queries_used': 6}
        self.queries += 1
        args = args if isinstance(args, dict) else None
        response = {'evidence_id': f'e{len(self.ledger) + 1:03d}', 'action_id': action_id,
                    'source': 'files', 'as_of': self.as_of, 'status': 'invalid_action'}
        if action_id == 'files.list_download' and args == {}:
            response.update({'operation': 'list_files', 'status': 'ok',
                             'coverage': {'scope': 'episode_declared_Download_files', 'complete': True},
                             'items': [{'file_ref': ref, 'name': r['data']['path'][9:],
                                        'size': len(r['data']['content'].encode()),
                                        'modified_at': r['data']['modified_at']} for ref, r in self.public_refs.items()]})
            self.unlocked = True
        elif action_id == 'files.read_text' and args is not None and set(args) == {'file_ref'}:
            record = self.public_refs.get(args['file_ref']) if isinstance(args['file_ref'], str) else None
            if record is not None and self.unlocked:
                response.update({'operation': 'read_text', 'status': 'ok',
                                 'file_ref': args['file_ref'], 'content': record['data']['content']})
            else:
                response['status'] = 'not_unlocked'
        self.ledger.append(response)
        return response

    def finish(self, submission):
        if self.submission is not None:
            raise ValueError('run already finished')
        if (not isinstance(submission, dict) or set(submission) != {'need_set', 'evidence_ids'}
                or not isinstance(submission['need_set'], list)
                or not isinstance(submission['evidence_ids'], list)
                or any(not isinstance(e, str) for e in submission['evidence_ids'])):
            raise ValueError('invalid final submission')
        self.submission = submission
        return {'status': 'submitted', 'queries_used': self.queries}

    def grade(self):
        if self.submission is None:
            raise ValueError('run has no final submission')
        ids = self.submission['evidence_ids']
        seen = {e['evidence_id']: e for e in self.ledger}
        valid = len(ids) == len(set(ids)) and all(e in seen and seen[e]['status'] == 'ok' for e in ids)
        content_refs = [seen[e] for e in ids if e in seen and seen[e].get('operation') == 'read_text']
        grounded = valid and any(e.get('file_ref') == self.target_ref for e in content_refs)
        expected = [{**need, 'target': self.public_refs[self.target_ref]['data']['path']}
                    for need in self.oracle['expected_need_set']]
        correct = self.submission['need_set'] == expected
        return {'need_set_correct': correct,
                'evidence_refs_valid': valid, 'decision_grounded': correct and grounded,
                'queries_used': self.queries}


def main():
    parser = argparse.ArgumentParser(description='Internal Files pilot Search host')
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--private-result', type=Path, required=True)
    args = parser.parse_args()
    session = FilesSearch(args.release_dir)
    print(json.dumps({'status': 'ready', 'actions': ['files.list_download', 'files.read_text'],
                      'search_budget': 6}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = session.search(None, {})
                print(json.dumps(response), flush=True)
                continue
            if request.get('type') == 'finish':
                response = session.finish(request.get('submission'))
                private = {'ledger': session.ledger, 'submission': session.submission,
                           'grade': session.grade()}
                if args.private_result.exists():
                    raise FileExistsError('private result already exists')
                args.private_result.write_text(json.dumps(private, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps(response), flush=True)
                return
            response = session.search(request.get('action_id'), request.get('args'))
        except json.JSONDecodeError:
            response = session.search(None, {})
        except (ValueError, TypeError) as exc:
            response = {'status': 'invalid_request', 'error': str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
