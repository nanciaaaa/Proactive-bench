import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.gui_g4_audit import audit


ROOT = Path(__file__).parents[1]
VERIFIED_RELEASE = {'frames': [{}], 'manifest_sha256': '0' * 64}


def build_candidates(count):
    return [{'candidate_id': f'cand-{index:02d}', 'release_dir': f'gui-release-{index:02d}'}
            for index in range(count)]


class G4CandidateAuditTest(unittest.TestCase):
    def assert_audit_rejects(self, spec, tmp, message):
        spec_path = Path(tmp) / 'spec.json'
        spec_path.write_text(json.dumps(spec))
        runtime_root = Path(tmp) / 'runtime'
        runtime_root.mkdir(exist_ok=True)
        with self.assertRaisesRegex(ValueError, message):
            audit(spec_path, runtime_root)

    def audit_with_mocked_release(self, candidate_count):
        candidates = build_candidates(candidate_count)
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': candidate_count,
                'splits': {'all': [item['candidate_id'] for item in candidates]},
                'candidates': candidates}
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / 'spec.json'
            spec_path.write_text(json.dumps(spec))
            runtime_root = Path(tmp) / 'runtime'
            runtime_root.mkdir()
            with mock.patch('scripts.gui_g4_audit.verify_release',
                            return_value=VERIFIED_RELEASE) as verify_release:
                report = audit(spec_path, runtime_root)
                self.assertEqual(verify_release.call_count, candidate_count)
        return report

    def test_candidate_subset_is_machine_publishable(self):
        report = audit(ROOT / 'contracts/gui-g4-candidates.json', ROOT / 'runtime')
        self.assertEqual(report['status'], 'pass')
        self.assertGreater(report['candidate_count'], 0)
        self.assertTrue(all(item['status'] == 'pass' for item in report['candidates']))

    def test_candidate_contract_is_json_and_split_complete(self):
        spec = json.loads((ROOT / 'contracts/gui-g4-candidates.json').read_text())
        self.assertEqual(spec['candidate_count'], len(spec['candidates']))
        candidate_ids = [item['candidate_id'] for item in spec['candidates']]
        split_ids = [item for values in spec['splits'].values() for item in values]
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)))
        self.assertEqual(len(split_ids), len(set(split_ids)))
        self.assertEqual(set(split_ids), set(candidate_ids))

    def test_single_candidate_count_is_not_rejected_by_size(self):
        report = self.audit_with_mocked_release(1)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['candidate_count'], 1)

    def test_seventeen_candidate_count_is_not_rejected_by_size(self):
        report = self.audit_with_mocked_release(17)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['candidate_count'], 17)

    def test_empty_candidate_subset_is_rejected(self):
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': 0,
                'splits': {}, 'candidates': []}
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_audit_rejects(spec, tmp, 'must not be empty')

    def test_candidate_count_mismatch_is_rejected(self):
        candidates = build_candidates(1)
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': 2,
                'splits': {'all': [item['candidate_id'] for item in candidates]},
                'candidates': candidates}
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_audit_rejects(spec, tmp, 'candidate_count')

    def test_duplicate_candidate_ids_are_rejected(self):
        candidates = build_candidates(1)
        candidates.append(dict(candidates[0]))
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': 2,
                'splits': {'all': [candidates[0]['candidate_id']]}, 'candidates': candidates}
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_audit_rejects(spec, tmp, 'uniqueness')

    def test_duplicate_split_membership_is_rejected(self):
        candidates = build_candidates(2)
        candidate_ids = [item['candidate_id'] for item in candidates]
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': 2,
                'splits': {'first': candidate_ids, 'second': candidate_ids[:1]},
                'candidates': candidates}
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_audit_rejects(spec, tmp, 'exactly once')

    def test_incomplete_splits_are_rejected(self):
        candidates = build_candidates(2)
        spec = {'schema_version': '1.0', 'track': 'gui', 'candidate_count': 2,
                'splits': {'all': [candidates[0]['candidate_id']]}, 'candidates': candidates}
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_audit_rejects(spec, tmp, 'exactly once')


if __name__ == '__main__':
    unittest.main()
