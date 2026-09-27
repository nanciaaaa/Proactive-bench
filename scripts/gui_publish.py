#!/usr/bin/env python3
"""Publish only machine-accepted original PNGs and public packet metadata."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from gui_capture import png_size
from gui_contract import timestamp
from gui_machine_qa import audit_capture, RULE as MACHINE_RULE, KNOWN_RULES, MODEL_SHA256, OCR_MODELS


def publish(capture_dir, release_dir):
    capture_dir, release_dir = Path(capture_dir), Path(release_dir)
    private_dir = release_dir.with_name(release_dir.name + '.private')
    receipt = json.loads((capture_dir / 'receipt.json').read_text())
    plan_bytes = (capture_dir / 'private-plan.json').read_bytes()
    plan = json.loads(plan_bytes)
    if receipt['status'] != 'capture_complete' or not receipt.get('reset_verified'):
        raise ValueError('capture did not pass machine checks and reset')
    if receipt['episode_id'] != plan['episode_id'] or receipt['source_assets'] != plan['source_assets']:
        raise ValueError('receipt and plan provenance mismatch')
    if receipt.get('plan_sha256') != hashlib.sha256(plan_bytes).hexdigest():
        raise ValueError('capture plan changed after collection')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', receipt.get('device', {}).get('baseline_version', '')):
        raise ValueError('missing device baseline version')
    frames = receipt['frames']
    expected = [(p['packet_id'], f['frame_id']) for p in plan['packets'] for f in p['capture']['frames']]
    if [(f['packet_id'], f['frame_id']) for f in frames] != expected:
        raise ValueError('missing or reordered frame')
    if not (capture_dir / 'qa-machine.json').is_file():
        raise ValueError('machine pixel QA is missing')
    machine_bytes = (capture_dir / 'qa-machine.json').read_bytes()
    machine = json.loads(machine_bytes)
    if machine != audit_capture(capture_dir) or machine['status'] != 'pass':
        raise ValueError('machine pixel QA is missing, changed, or rejected')
    if machine.get('device_env', {}).get('status') != 'pass':
        raise ValueError('machine QA device environment is missing or rejected')
    paths = set()
    for index, frame in enumerate(frames):
        packet = next(p for p in plan['packets'] if p['packet_id'] == frame['packet_id'])
        fields = packet['capture']['visible_assertions']
        if {item['field'] for item in machine['frames'][index]['fields']} != set(fields) | {'_status_time'}:
            raise ValueError('machine QA fields do not match plan')
        observed = timestamp(packet['observed_at'])
        for clock_field in ('clock_synced_at', 'android_time'):
            actual = timestamp(frame.get(clock_field))
            if actual.utcoffset() != observed.utcoffset() or abs((actual - observed).total_seconds()) > 30:
                raise ValueError('Android time or timezone differs from observation')
        name = frame.get('png')
        if not isinstance(name, str) or not re.fullmatch(r'images/[A-Za-z0-9_-]+\.png', name) or name in paths:
            raise ValueError('invalid or duplicate PNG path')
        paths.add(name)
        path = capture_dir / name
        if path.is_symlink() or not path.resolve().is_relative_to(capture_dir.resolve()):
            raise ValueError('PNG escapes capture directory')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != frame['sha256'] or png_size(raw) != (frame['width'], frame['height']):
            raise ValueError('PNG integrity check failed')
    if release_dir.exists() or private_dir.exists():
        raise FileExistsError('release or private QA bundle already exists')
    release_dir.mkdir(parents=True, exist_ok=False)
    private_dir.mkdir()
    (release_dir / 'images').mkdir()
    public = []
    for packet in plan['packets']:
        related = [f for f in frames if f['packet_id'] == packet['packet_id']]
        for frame in related:
            shutil.copyfile(capture_dir / frame['png'], release_dir / frame['png'])
        public.append({'packet_id': packet['packet_id'], 'sequence': packet['sequence'], 'type': 'gui',
                       'source': packet['source'], 'observed_at': packet['observed_at'],
                       **({'delivered_at': packet['delivered_at']} if 'delivered_at' in packet else {}),
                       'frames': [{'frame_id': f['frame_id'], 'screenshot_ref': f['png'], 'sha256': f['sha256']}
                                  for f in related]})
    manifest = {'schema_version': '1.0', 'track': 'gui',
                **({'public_rule': plan['public_rule']} if 'public_rule' in plan else {}), 'packets': public}
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    (release_dir / 'manifest.json').write_bytes(raw)
    for frame in frames:
        if hashlib.sha256((release_dir / frame['png']).read_bytes()).hexdigest() != frame['sha256']:
            raise RuntimeError('published PNG differs from machine-accepted original')
    receipt_bytes = (capture_dir / 'receipt.json').read_bytes()
    (private_dir / 'receipt.json').write_bytes(receipt_bytes)
    (private_dir / 'private-plan.json').write_bytes(plan_bytes)
    (private_dir / 'qa-machine.json').write_bytes(machine_bytes)
    qa = {'schema_version': '1.0', 'episode_id': receipt['episode_id'],
          'capture_run_id': receipt.get('capture_run_id'),
          'public_manifest_sha256': hashlib.sha256(raw).hexdigest(),
          'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
          'private_plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
          'machine_qa_sha256': hashlib.sha256(machine_bytes).hexdigest(),
          'machine_rule_version': MACHINE_RULE, 'ocr_model_sha256': MODEL_SHA256,
          'source_assets': plan['source_assets'],
          'machine_frames': [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                              'png': frame['png'], 'sha256': frame['sha256']}
                             for frame in frames]}
    (private_dir / 'qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2) + '\n')
    (release_dir / 'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest() + '\n')
    verify_release(release_dir)
    return manifest


def verify_release(release_dir):
    release_dir = Path(release_dir)
    private_dir = release_dir.with_name(release_dir.name + '.private')
    raw = (release_dir / 'manifest.json').read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()
    if (release_dir / 'manifest.sha256').read_text().strip() != manifest_hash:
        raise ValueError('published manifest hash mismatch')
    manifest = json.loads(raw)
    qa = json.loads((private_dir / 'qa.json').read_text())
    if qa['public_manifest_sha256'] != manifest_hash:
        raise ValueError('private QA does not bind public manifest')
    plan_bytes = (private_dir / 'private-plan.json').read_bytes()
    receipt_bytes = (private_dir / 'receipt.json').read_bytes()
    machine_bytes = (private_dir / 'qa-machine.json').read_bytes()
    receipt = json.loads(receipt_bytes)
    if receipt.get('status') != 'capture_complete' or receipt.get('reset_verified') is not True:
        raise ValueError('published receipt is not machine-complete')
    if (hashlib.sha256(plan_bytes).hexdigest() != qa['private_plan_sha256']
            or hashlib.sha256(receipt_bytes).hexdigest() != qa['receipt_sha256']
            or hashlib.sha256(machine_bytes).hexdigest() != qa['machine_qa_sha256']):
        raise ValueError('private plan or receipt hash mismatch')
    machine = json.loads(machine_bytes)
    if (machine['status'] != 'pass' or machine['rule_version'] != qa['machine_rule_version']
            or machine['ocr_model_sha256'] != qa['ocr_model_sha256']
            or machine['rule_version'] not in KNOWN_RULES or machine['ocr_model_sha256'] != MODEL_SHA256):
        raise ValueError('machine QA rule or model mismatch')
    if machine['rule_version'] == MACHINE_RULE and machine.get('ocr_models') != OCR_MODELS:
        raise ValueError('machine OCR model set mismatch')
    if machine['rule_version'] == MACHINE_RULE and machine.get('device_env', {}).get('status') != 'pass':
        raise ValueError('machine QA device environment is missing or rejected')
    plan = json.loads(plan_bytes)
    if plan['source_assets'] != qa['source_assets']:
        raise ValueError('private source asset hashes mismatch')
    public_frames = {(p['packet_id'], f['frame_id']): f for p in manifest['packets'] for f in p['frames']}
    if len(public_frames) != len(qa['machine_frames']) or len(machine['frames']) != len(qa['machine_frames']):
        raise ValueError('release frame count mismatch')
    for accepted, audited in zip(qa['machine_frames'], machine['frames']):
        key = (accepted['packet_id'], accepted['frame_id'])
        frame = public_frames.get(key)
        if (not frame or frame['screenshot_ref'] != accepted['png'] or frame['sha256'] != accepted['sha256']
                or any(audited.get(field) != accepted.get(field) for field in ('packet_id', 'frame_id', 'png', 'sha256'))
                or audited['status'] != 'pass' or not all(item['passed'] for item in audited['fields'])):
            raise ValueError('release frame binding mismatch')
        path = release_dir / accepted['png']
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != accepted['sha256']:
            raise ValueError('released PNG hash mismatch')
    expected_files = {'manifest.json', 'manifest.sha256'} | {f['screenshot_ref'] for f in public_frames.values()}
    actual_files = {str(path.relative_to(release_dir)) for path in release_dir.rglob('*') if path.is_file() or path.is_symlink()}
    if actual_files != expected_files or any(path.is_symlink() for path in release_dir.rglob('*')):
        raise ValueError('public release contains unexpected files')
    return {'packets': len(manifest['packets']), 'frames': len(public_frames), 'manifest_sha256': manifest_hash}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('capture_dir', type=Path, nargs='?')
    parser.add_argument('release_dir', type=Path, nargs='?')
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_release(args.verify)))
    else:
        if args.capture_dir is None or args.release_dir is None:
            parser.error('capture_dir and release_dir required for publishing')
        result = publish(args.capture_dir, args.release_dir)
        print(json.dumps({'packets': len(result['packets']), 'manifest': str(args.release_dir / 'manifest.json')}))
