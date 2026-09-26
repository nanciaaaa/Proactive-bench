import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gui_files_search import FilesSearch, main
from gui_formal import compile_formal_plan


class FilesSearchTest(unittest.TestCase):
    def make_capture(self, root, variant):
        plan = compile_formal_plan(ROOT / f'datasets/gui-files-formal-pilot/manifest-{variant}.json')
        raw = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode()
        (root / 'private-plan.json').write_bytes(raw)
        assets = root / 'private-assets'
        assets.mkdir()
        for name, path in plan['source_paths'].items():
            (assets / f'{name}.json').write_bytes(Path(path).read_bytes())
        record = plan['fixture']['ku_records'][0]
        data = record['data']
        receipt = {'status': 'capture_complete', 'reset_verified': True,
                   'plan_sha256': hashlib.sha256(raw).hexdigest(), 'episode_id': plan['episode_id'],
                   'source_assets': plan['source_assets'], 'freeze': {'success': True, 'frozen': True},
                   'frames': [{'packet_id': p['packet_id'], 'frame_id': f['frame_id']}
                              for p in plan['packets'] for f in p['capture']['frames']],
                   'readback': {'files': [{'path': '/sdcard/' + data['path'],
                                          'sha256': hashlib.sha256(data['content'].encode()).hexdigest(),
                                          'modified_at': int(datetime.fromisoformat(data['modified_at']).timestamp())}]}}
        (root / 'receipt.json').write_text(json.dumps(receipt))
        return receipt

    def test_opposite_worlds_require_the_same_public_search_route(self):
        public_listings = []
        for variant, expected_content, need in [('a', 'GuiPilotContentV1', []),
                                                ('b', 'GuiPilotContentV2', [{'type': 'restore_file_content',
                                                                          'target': 'Download/GuiPilotProof.txt',
                                                                          'content': 'GuiPilotContentV1'}])]:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp, patch('gui_files_search.verify_release'):
                root = Path(tmp)
                private = root / 'release.private'
                private.mkdir()
                self.make_capture(private, variant)
                session = FilesSearch(root / 'release')
                self.assertEqual(session.search('files.read_text', {'file_ref': 'f001'})['status'], 'not_unlocked')
                listing = session.search('files.list_download', {})
                public_listings.append({k: v for k, v in listing.items() if k != 'evidence_id'})
                self.assertEqual(listing['items'][0]['file_ref'], 'f001')
                self.assertNotIn('content', listing['items'][0])
                detail = session.search('files.read_text', {'file_ref': 'f001'})
                self.assertEqual(detail['content'], expected_content)
                self.assertEqual(session.finish({'need_set': need, 'evidence_ids': [detail['evidence_id']]}),
                                 {'status': 'submitted', 'queries_used': 3})
                self.assertEqual(session.grade(), {'need_set_correct': True, 'evidence_refs_valid': True,
                                                   'decision_grounded': True, 'queries_used': 3})
        self.assertEqual(public_listings[0], public_listings[1])

    def test_budget_and_frozen_readback_guard(self):
        with tempfile.TemporaryDirectory() as tmp, patch('gui_files_search.verify_release'):
            root = Path(tmp)
            private = root / 'release.private'
            private.mkdir()
            receipt = self.make_capture(private, 'b')
            session = FilesSearch(root / 'release')
            for _ in range(6):
                self.assertEqual(session.search('unknown', {})['status'], 'invalid_action')
            self.assertEqual(session.search('files.list_download', {})['status'], 'budget_exhausted')
            receipt['readback']['files'][0]['sha256'] = '0' * 64
            (private / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'readback differs'):
                FilesSearch(root / 'release')

    def test_jsonl_host_keeps_grade_private_and_counts_malformed_query(self):
        with tempfile.TemporaryDirectory() as tmp, patch('gui_files_search.verify_release'):
            root = Path(tmp)
            private_dir = root / 'release.private'
            private_dir.mkdir()
            self.make_capture(private_dir, 'a')
            private = root / 'grade.json'
            request = '\n'.join(['not-json', json.dumps({'action_id': 'files.list_download', 'args': {}}),
                                 json.dumps({'action_id': 'files.read_text', 'args': {'file_ref': 'f001'}}),
                                 json.dumps({'type': 'finish', 'submission': {'need_set': [], 'evidence_ids': ['e003']}})]) + '\n'
            output = io.StringIO()
            with patch.object(sys, 'argv', ['gui_files_search.py', '--release-dir', str(root / 'release'),
                                            '--private-result', str(private)]), patch('sys.stdin', io.StringIO(request)), \
                    redirect_stdout(output):
                main()
            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([r['status'] for r in responses], ['ready', 'invalid_action', 'ok', 'ok', 'submitted'])
            self.assertEqual(responses[-1]['queries_used'], 3)
            self.assertNotIn('need_set_correct', output.getvalue())
            self.assertTrue(json.loads(private.read_text())['grade']['decision_grounded'])


if __name__ == '__main__':
    unittest.main()
