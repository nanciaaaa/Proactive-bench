"""Audit the GUI G4 candidate subset and publish rejection counts.

The blueprint names roughly 8–16 candidates as a suggested scale, not a gate;
this audit therefore enforces structural invariants only (non-empty, exact
``candidate_count``, unique ``candidate_id``, complete and duplicate-free
``splits``) plus a full release replay for every published candidate.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_publish import verify_release

ROOT = Path(__file__).resolve().parents[1]


def audit(spec_path, runtime_root):
    spec = json.loads(Path(spec_path).read_text())
    candidates = spec['candidates']
    ids = {item['candidate_id'] for item in candidates}
    split_ids = [candidate_id for values in spec.get('splits', {}).values() for candidate_id in values]
    if not candidates:
        raise ValueError('G4 candidate subset must not be empty')
    if spec.get('candidate_count') != len(candidates) or len(ids) != len(candidates):
        raise ValueError('G4 candidate_count or candidate_id uniqueness is invalid')
    if len(split_ids) != len(set(split_ids)) or set(split_ids) != ids:
        raise ValueError('G4 splits must cover every candidate exactly once')
    results = []
    for item in candidates:
        release = Path(runtime_root) / item['release_dir']
        verified = verify_release(release)
        results.append({**item, 'status': 'pass', 'frames': verified['frames'],
                        'manifest_sha256': verified['manifest_sha256']})
    rejected = []
    for directory in sorted(Path(runtime_root).glob('gui-*')):
        receipt_path = directory / 'receipt.json'
        if not receipt_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text())
        status = receipt.get('status')
        if status in ('capture_failed', 'reject'):
            rejected.append({'capture_dir': directory.name, 'status': status,
                             'reason': receipt.get('failure_reason')})
    return {'schema_version': '1.0', 'track': 'gui', 'candidate_count': len(results),
            'status': 'pass', 'candidates': results,
            'rejection_summary': {'capture_failed': sum(x['status'] == 'capture_failed' for x in rejected),
                                  'reject': sum(x['status'] == 'reject' for x in rejected),
                                  'records': rejected}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, default=ROOT / 'contracts/gui-g4-candidates.json')
    parser.add_argument('--runtime-root', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.spec, args.runtime_root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'candidate_count': report['candidate_count'],
                      'rejection_summary': {k: v for k, v in report['rejection_summary'].items() if k != 'records'}}))
