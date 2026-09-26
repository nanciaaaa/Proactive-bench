#!/usr/bin/env python3
"""Machine check that two formal Worlds have identical pre-EOF App pixels.

Machine capture pairs bind every UI snapshot to its receipt hash and only
exempt per-pixel differences inside the intersection of whitelisted
rectangles whose resource-id, text, content-desc, class and package match.
Release-package comparison is strict unless the caller explicitly binds both
sides to their original capture directories; only then may per-pixel
differences inside that same two-sided intersection be exempted.
"""
import argparse
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path
from xml.etree import ElementTree

from gui_capture import png_size
from gui_contract import assert_public_equivalent
from gui_machine_qa import audit_capture, KNOWN_RULES, OCR_MODELS, RULE
from gui_publish import verify_release

VOLATILE_RESOURCE_IDS = {'com.google.android.apps.messaging:id/searchbar'}


def rgb_rows(raw):
    width, height = png_size(raw)
    channels = 3 if raw[25] == 2 else 4
    stride = width * channels
    offset, compressed = 8, bytearray()
    while offset < len(raw):
        size = struct.unpack('>I', raw[offset:offset + 4])[0]
        if raw[offset + 4:offset + 8] == b'IDAT':
            compressed.extend(raw[offset + 8:offset + 8 + size])
        offset += size + 12
    scanlines = zlib.decompress(compressed)
    previous = bytearray(stride)
    rows = []
    for row in range(height):
        start = row * (stride + 1)
        mode = scanlines[start]
        data = bytearray(scanlines[start + 1:start + 1 + stride])
        for x in range(stride):
            left = data[x - channels] if x >= channels else 0
            above = previous[x]
            upper_left = previous[x - channels] if x >= channels else 0
            if mode == 1:
                predictor = left
            elif mode == 2:
                predictor = above
            elif mode == 3:
                predictor = (left + above) // 2
            elif mode == 4:
                p = left + above - upper_left
                distances = (abs(p - left), abs(p - above), abs(p - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                predictor = 0
            data[x] = (data[x] + predictor) & 255
        previous = data
        rows.append(bytes(data if channels == 3 else (byte for x in range(0, stride, 4) for byte in data[x:x + 3])))
    return width, height, rows


def whitelist_rectangles(root):
    rectangles = []
    for node in root.iter('node'):
        if node.attrib.get('resource-id') not in VOLATILE_RESOURCE_IDS:
            continue
        bounds = node.attrib.get('bounds', '')
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if match:
            rectangles.append(tuple(map(int, match.groups())))
    return rectangles


def semantic_rectangles(root):
    rectangles = []
    for node in root.iter('node'):
        if node.attrib.get('resource-id') in VOLATILE_RESOURCE_IDS:
            match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.attrib.get('bounds', ''))
            if match:
                semantics = tuple(tuple(descendant.attrib.get(key, '') for key in
                                        ('resource-id', 'text', 'content-desc', 'class', 'package'))
                                  for descendant in node.iter('node'))
                rectangles.append((tuple(map(int, match.groups())), semantics))
    return rectangles


def bound_volatile_rectangles(capture_dir, frame):
    """Read a frame's capture UI XML after hash-checking it against its receipt."""
    capture_dir = Path(capture_dir)
    ui_name = frame.get('ui_dump')
    expected = frame.get('ui_dump_sha256')
    if not isinstance(ui_name, str) or not re.fullmatch(r'ui/[0-9a-f]{32}\.xml', ui_name):
        raise ValueError('capture frame has no bound UI snapshot')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('capture frame has no UI snapshot hash')
    path = capture_dir / ui_name
    if path.is_symlink() or not path.resolve().is_relative_to(capture_dir.resolve()):
        raise ValueError('capture UI snapshot escapes capture directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('capture UI snapshot hash mismatch')
    return semantic_rectangles(ElementTree.fromstring(raw))


def intersect_rectangles(left, right):
    """Return the two-sided intersection of trusted rectangles, never their union."""
    intersections = []
    for left_rect in left:
        for right_rect in right:
            rectangle = (max(left_rect[0], right_rect[0]), max(left_rect[1], right_rect[1]),
                         min(left_rect[2], right_rect[2]), min(left_rect[3], right_rect[3]))
            if rectangle[0] < rectangle[2] and rectangle[1] < rectangle[3]:
                intersections.append(rectangle)
    return intersections


def bound_capture_frames(capture_dir, private_dir, released):
    """Bind released frames to one capture directory via receipt bytes, files and UI dumps."""
    capture_dir = Path(capture_dir)
    if not capture_dir.is_dir():
        raise ValueError('capture directory is missing')
    capture_bytes = (capture_dir / 'receipt.json').read_bytes()
    if capture_bytes != (Path(private_dir) / 'receipt.json').read_bytes():
        raise ValueError('capture receipt differs from published private receipt')
    receipt = json.loads(capture_bytes)
    bound = {}
    for frame in receipt.get('frames', []):
        key = (frame.get('packet_id'), frame.get('frame_id'))
        if key in bound or not all(isinstance(part, str) and part for part in key):
            raise ValueError('capture receipt frames are not uniquely keyed')
        name = frame.get('png')
        if not isinstance(name, str) or not re.fullmatch(r'images/[A-Za-z0-9_-]+\.png', name):
            raise ValueError('capture frame has invalid PNG path')
        path = capture_dir / name
        if path.is_symlink() or not path.resolve().is_relative_to(capture_dir.resolve()):
            raise ValueError('capture PNG escapes capture directory')
        if hashlib.sha256(path.read_bytes()).hexdigest() != frame.get('sha256'):
            raise ValueError('capture PNG hash mismatch')
        bound[key] = (frame, bound_volatile_rectangles(capture_dir, frame))
    if set(bound) != {(packet_id, frame_id) for packet_id, frame_id, _, _ in released}:
        raise ValueError('capture and release frames differ')
    for packet_id, frame_id, png, sha256 in released:
        captured = bound[(packet_id, frame_id)][0]
        if captured['png'] != png or captured['sha256'] != sha256:
            raise ValueError('capture frame does not match released frame')
    return bound


def differs_outside(left_row, right_row, y, width, rectangles):
    for x in range(width):
        if any(left <= x < right and top <= y < bottom
               for left, top, right, bottom in rectangles):
            continue
        start = x * 3
        if left_row[start:start + 3] != right_row[start:start + 3]:
            return True
    return False


def audit_pair(left_dir, right_dir):
    directories = [Path(left_dir), Path(right_dir)]
    plans = [json.loads((directory / 'private-plan.json').read_text()) for directory in directories]
    assert_public_equivalent(plans)
    receipts = [json.loads((directory / 'receipt.json').read_text()) for directory in directories]
    machine = [json.loads((directory / 'qa-machine.json').read_text()) for directory in directories]
    def verified(directory, q):
        if q.get('rule_version') not in KNOWN_RULES:
            return False
        fresh = audit_capture(directory)
        old = json.loads(json.dumps(q))
        old['rule_version'] = RULE
        old.setdefault('ocr_models', OCR_MODELS)
        for frame in old['frames']:
            frame.setdefault('language', 'eng')
            frame.setdefault('ocr_model_sha256', OCR_MODELS['eng'])
            for field in frame['fields']:
                field.setdefault('scale', 1)
        return old == fresh and q['status'] == 'pass'

    if any(not verified(directory, q)
           or r['status'] != 'capture_complete' or not r['reset_verified']
           for directory, q, r in zip(directories, machine, receipts)):
        raise ValueError('both Worlds need accepted machine frames and reset receipts')
    if len(receipts[0]['frames']) != len(receipts[1]['frames']):
        raise ValueError('counterfactual frame counts differ')
    pairs = []
    for left, right in zip(receipts[0]['frames'], receipts[1]['frames']):
        if (left['packet_id'], left['frame_id'], left['source'], left['observed_at']) != (
                right['packet_id'], right['frame_id'], right['source'], right['observed_at']):
            raise ValueError('counterfactual frame metadata differs')
        images = [(directory / frame['png']).read_bytes() for directory, frame in zip(directories, (left, right))]
        if any(hashlib.sha256(raw).hexdigest() != frame['sha256'] for raw, frame in zip(images, (left, right))):
            raise ValueError('counterfactual PNG hash mismatch')
        a_width, a_height, a_rows = rgb_rows(images[0])
        b_width, b_height, b_rows = rgb_rows(images[1])
        if (a_width, a_height) != (b_width, b_height) or (a_width, a_height) != (1080, 2400):
            raise ValueError('counterfactual screen geometry differs')
        volatile = [intersection for left_rect, left_semantics in bound_volatile_rectangles(directories[0], left)
                    for right_rect, right_semantics in bound_volatile_rectangles(directories[1], right)
                    if left_semantics == right_semantics
                    for intersection in intersect_rectangles([left_rect], [right_rect])]
        changed = [y for y in range(a_height) if a_rows[y] != b_rows[y]]
        app_changed = [y for y in changed if 132 <= y < 2337]
        comparable_changed = [y for y in changed if differs_outside(a_rows[y], b_rows[y], y, a_width, volatile)]
        comparable_app_changed = [y for y in comparable_changed if 132 <= y < 2337]
        pairs.append({'packet_id': left['packet_id'], 'frame_id': left['frame_id'],
                      'png_sha256': [left['sha256'], right['sha256']],
                      'changed_rows': changed, 'app_changed_rows': app_changed,
                      'comparable_changed_rows': comparable_changed,
                      'comparable_app_changed_rows': comparable_app_changed,
                      'volatile_intersection': [list(rectangle) for rectangle in volatile],
                      'exempted_rows': [y for y in app_changed if y not in comparable_app_changed],
                      'status': 'pass' if not comparable_app_changed else 'reject'})
    return {'schema_version': '1.0', 'rule_version': 'app-pixels-1080x2400-v2',
            'status': 'pass' if all(pair['status'] == 'pass' for pair in pairs) else 'reject',
            'pairs': pairs}


def audit_release_pair(left_dir, right_dir, left_capture=None, right_capture=None):
    releases = [Path(left_dir), Path(right_dir)]
    for release in releases:
        verify_release(release)
    if (left_capture is None) != (right_capture is None):
        raise ValueError('volatile exemption needs both capture directories')
    captures = None if left_capture is None else [Path(left_capture), Path(right_capture)]
    private = [release.with_name(release.name + '.private') for release in releases]
    plans = [json.loads((directory / 'private-plan.json').read_text()) for directory in private]
    assert_public_equivalent(plans)

    def as_dict(value):
        return value if isinstance(value, dict) else {}

    formals = [as_dict(plan.get('formal')) for plan in plans]
    oracles = [as_dict(formal.get('oracle')) for formal in formals]
    needs = [oracle.get('expected_need_set') for oracle in oracles]
    if (any(not isinstance(need, list) for need in needs)
            or any(not isinstance(formal.get('variant_id'), str) or not formal['variant_id'] for formal in formals)
            or formals[0]['variant_id'] == formals[1]['variant_id']
            or any(oracle.get('episode_id') != plan.get('episode_id')
                   or oracle.get('variant_id') != formal['variant_id']
                   for oracle, plan, formal in zip(oracles, plans, formals))
            or sum(not need for need in needs) != 1):
        raise ValueError('GUI Track P/A needs opposite, correctly bound private Need Sets')
    manifests = [json.loads((release / 'manifest.json').read_text()) for release in releases]
    public = [{key: value for key, value in manifest.items() if key != 'packets'} for manifest in manifests]
    if public[0] != public[1]:
        raise ValueError('counterfactual public rules differ')
    packets = [manifest['packets'] for manifest in manifests]
    if len(packets[0]) != len(packets[1]):
        raise ValueError('counterfactual packet counts differ')
    bound = None
    if captures is not None:
        released = [[(packet['packet_id'], frame['frame_id'], frame['screenshot_ref'], frame['sha256'])
                     for packet in side for frame in packet['frames']] for side in packets]
        bound = [bound_capture_frames(capture, private_dir, released_frames)
                 for capture, private_dir, released_frames in zip(captures, private, released)]
    pairs = []
    for left, right in zip(*packets):
        if ({key: value for key, value in left.items() if key != 'frames'} !=
                {key: value for key, value in right.items() if key != 'frames'}
                or len(left['frames']) != len(right['frames'])):
            raise ValueError('counterfactual public packets differ')
        for left_frame, right_frame in zip(left['frames'], right['frames']):
            if left_frame['frame_id'] != right_frame['frame_id']:
                raise ValueError('counterfactual frame identity differs')
            images = [(release / frame['screenshot_ref']).read_bytes()
                      for release, frame in zip(releases, (left_frame, right_frame))]
            left_width, left_height, left_rows = rgb_rows(images[0])
            right_width, right_height, right_rows = rgb_rows(images[1])
            if (left_width, left_height) != (right_width, right_height) or (left_width, left_height) != (1080, 2400):
                raise ValueError('counterfactual screen geometry differs')
            strict_changed = [row for row in range(132, 2337) if left_rows[row] != right_rows[row]]
            pair = {'packet_id': left['packet_id'], 'frame_id': left_frame['frame_id'],
                    'png_sha256': [left_frame['sha256'], right_frame['sha256']], 'app_changed_rows': strict_changed,
                    'status': 'reject' if strict_changed else 'pass'}
            if bound is not None:
                key = (left['packet_id'], left_frame['frame_id'])
                volatile = [intersection for left_rect, left_semantics in bound[0][key][1]
                            for right_rect, right_semantics in bound[1][key][1]
                            if left_semantics == right_semantics
                            for intersection in intersect_rectangles([left_rect], [right_rect])]
                app_changed = [row for row in strict_changed
                               if differs_outside(left_rows[row], right_rows[row], row, left_width, volatile)]
                pair['app_changed_rows'] = app_changed
                pair['volatile_intersection'] = [list(rectangle) for rectangle in volatile]
                pair['exempted_rows'] = [row for row in strict_changed if row not in app_changed]
                pair['status'] = 'reject' if app_changed else 'pass'
            pairs.append(pair)
    accepted = bool(pairs) and all(pair['status'] == 'pass' for pair in pairs)
    return {'schema_version': '1.0',
            'rule_version': 'gui-release-pixels-v2' if bound is not None else 'gui-release-pixels-v1',
            'status': 'pass' if accepted else 'reject',
            'gui_track_passive_class': 'active_required' if accepted else 'unverified', 'pairs': pairs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--releases', action='store_true')
    parser.add_argument('--left-capture', type=Path)
    parser.add_argument('--right-capture', type=Path)
    args = parser.parse_args()
    if (args.left_capture is None) != (args.right_capture is None):
        parser.error('--left-capture and --right-capture must be given together')
    if not args.releases and args.left_capture is not None:
        parser.error('--left-capture and --right-capture require --releases')
    if args.releases:
        result = audit_release_pair(args.left, args.right, args.left_capture, args.right_capture)
    else:
        result = audit_pair(args.left, args.right)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'frames': len(result['pairs'])}))
    raise SystemExit(result['status'] != 'pass')
