#!/usr/bin/env python3
"""Round-4 QA / pre-release gate for completed PB GUI device captures.

This gate is the only door from a finished PB GUI capture to a private
release-ready report. It fails closed unless every link of the chain holds:

* the round-3 gate plan is ``pb-gui-capture-gate.v1`` with
  ``status=ready_for_device_capture`` and every target still
  ``pending_device_capture`` (the pre-capture artifact was not edited after
  gating);
* the backend apply receipt is ``pb-backend-apply-receipt.v1`` with
  ``status=ok`` and ``device_applied=true``, its ``episode_scope`` matches the
  gate scope field by field, and its bytes match the gate binding;
* the capture directory carries ``receipt.json`` + ``private-plan.json``: the
  plan is the compiled ``pb-gui-private-capture-plan.v1`` produced by
  ``pb_gui_capture_compile.py`` and its ``source_assets`` must bind the exact
  GUI plan (the gate's ``gui_plan`` sha256), backend plan, backend receipt and
  gate plan of the chain; the receipt is ``capture_complete`` with
  ``reset_verified=true`` and binds the compiled plan bytes + source assets,
  with every executable gate target present and hashed while every other gate
  target is explicitly declared unsupported;
* ``gui_machine_qa.audit_capture`` (imported, never re-implemented) returns
  ``status=pass`` and every captured GUI target has a pass frame bound to the
  capture receipt; an optional stored ``qa-machine.json`` / ``machine-qa.json``
  must equal a fresh audit run.

The emitted ``pb-gui-release-report.v1`` is private and carries hashes, scope
and frame counts only: it never copies PNGs, never calls ``gui_publish.py``,
never generates Gold and never re-declares a pre-existing PB screenshot as a
device capture.

Usage::

    python3 scripts/pb_gui_release_gate.py preflight --capture-dir CAPTURE \
        --gate-plan GATE --backend-receipt RECEIPT --output REPORT
    python3 scripts/pb_gui_release_gate.py validate --report REPORT \
        [--capture-dir CAPTURE --gate-plan GATE --backend-receipt RECEIPT]

``validate`` re-checks a stored report structurally (private keys and local
absolute paths included); when all three inputs are given it rebuilds the
report and refuses one whose recorded hashes or bindings no longer match.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gui_machine_qa as machine_qa
import pb_backend_plan as pb
import pb_gui_capture_gate as capture_gate

REPORT_SCHEMA = 'pb-gui-release-report.v1'
VALIDATION_SCHEMA = 'pb-gui-release-report-validation.v1'
RELEASE_STATUS = 'release_ready'
CAPTURE_RECEIPT_SCHEMA = '1.0'
GATE_SCHEMA = capture_gate.GATE_SCHEMA
GUI_PLAN_SCHEMA = capture_gate.GUI_PLAN_SCHEMA
BACKEND_RECEIPT_SCHEMA = capture_gate.RECEIPT_SCHEMA
CAPTURE_PLAN_SCHEMA = 'pb-gui-private-capture-plan.v1'
BACKEND_PLAN_SCHEMA = 'pb-backend-plan.v1'
QA_REPORT_NAMES = ('qa-machine.json', 'machine-qa.json')
IDENTITY_FIELDS = ('cluster_id', 'episode_id', 'passive_id')
SCOPE_FIELDS = IDENTITY_FIELDS + ('world_variant_id',)
SCOPE_OPTIONAL_FIELDS = ('track', 'decision_point', 'timezone', 'world_status', 'world_hash')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string, got {value!r}')
    return value


def require_hex64(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError(f'{label} must be 64 lowercase hex characters, got {value!r}')
    return value


def require_positive_int(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f'{label} must be a positive integer, got {value!r}')
    return value


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object, got {type(value).__name__}')
    return value


def relative_png(value, label):
    text = capture_gate.relative_ref(value, label)
    if not text.endswith('.png'):
        raise ValueError(f'{label} must be a .png path, got {text!r}')
    return text


def load_capture_document(path, expected_schema, label):
    if not path.is_file():
        raise ValueError(f'{label} not found: {path}')
    document = pb.load_json(path, label)
    require_object(document, label)
    if document.get('schema_version') != expected_schema:
        raise ValueError(f'{label} schema_version must be {expected_schema!r}, '
                         f'got {document.get("schema_version")!r}')
    return document


def check_scope_binding(gate_scope, receipt_scope):
    """Every scope value the gate recorded must still equal the receipt's."""
    require_object(gate_scope, 'gate plan episode_scope')
    require_object(receipt_scope, 'backend receipt episode_scope')
    for field in SCOPE_FIELDS:
        gate_value = require_text(gate_scope.get(field), f'gate plan episode_scope {field}')
        receipt_value = require_text(receipt_scope.get(field),
                                     f'backend receipt episode_scope {field}')
        if gate_value != receipt_value:
            raise ValueError(f'{field} mismatch: gate plan {gate_value!r} vs backend receipt '
                             f'episode_scope {receipt_value!r}')
    for field in SCOPE_OPTIONAL_FIELDS:
        gate_value = gate_scope.get(field)
        if gate_value is None:
            continue
        require_text(gate_value, f'gate plan episode_scope {field}')
        if receipt_scope.get(field) != gate_value:
            raise ValueError(f'{field} mismatch: gate plan {gate_value!r} vs backend receipt '
                             f'episode_scope {receipt_scope.get(field)!r}')


def check_backend_receipt(receipt, receipt_path, gate):
    if receipt.get('schema_version') != BACKEND_RECEIPT_SCHEMA:
        raise ValueError(f'backend receipt schema_version must be {BACKEND_RECEIPT_SCHEMA!r}, '
                         f'got {receipt.get("schema_version")!r}')
    _, scope = capture_gate.check_receipt(receipt)
    check_scope_binding(gate['episode_scope'], scope)
    actual = pb.digest(receipt_path)
    recorded = gate['bindings']['backend_receipt']['sha256']
    if recorded != actual:
        raise ValueError('backend receipt sha256 does not match the gate plan binding; the gate '
                         'plan or the receipt is stale')
    return actual


def check_capture_gate(gate, gate_path):
    capture_gate.validate_gate_plan(gate)
    actual = pb.digest(gate_path)
    return actual


def load_capture_inputs(capture_dir):
    plan_path = capture_dir / 'private-plan.json'
    receipt_path = capture_dir / 'receipt.json'
    plan = load_capture_document(plan_path, CAPTURE_PLAN_SCHEMA, 'capture private-plan.json')
    receipt = load_capture_document(receipt_path, CAPTURE_RECEIPT_SCHEMA, 'capture receipt.json')
    if receipt.get('status') != 'capture_complete':
        raise ValueError(f"capture receipt status must be 'capture_complete', "
                         f"got {receipt.get('status')!r}")
    if receipt.get('reset_verified') is not True:
        raise ValueError('capture receipt reset_verified must be true; reset residue or a failed '
                         'capture cannot be released')
    require_text(receipt.get('capture_run_id'), 'capture receipt capture_run_id')
    return plan, plan_path, receipt


def check_capture_source_assets(plan, gate, gate_sha, receipt, receipt_sha):
    """Bind the compiled capture plan provenance back to the gate chain.

    ``pb_gui_capture_compile.py`` compiles the bridge GUI plan into the device
    capture plan, so the capture plan bytes are *not* the gated GUI plan. The
    chain is closed through ``source_assets``: the compiled plan must name the
    exact GUI plan sha256 the gate bound, the exact backend plan/receipt the
    gate bound and the exact gate plan supplied here.
    """
    assets = require_object(plan.get('source_assets'),
                            'capture private-plan.json source_assets')
    schemas = (('gui_plan', GUI_PLAN_SCHEMA), ('backend_plan', BACKEND_PLAN_SCHEMA),
               ('backend_receipt', BACKEND_RECEIPT_SCHEMA), ('gate_plan', GATE_SCHEMA))
    shas = {}
    for name, schema in schemas:
        entry = require_object(assets.get(name), f'capture private-plan.json source_assets.{name}')
        if entry.get('schema_version') != schema:
            raise ValueError(f'capture private-plan.json source_assets.{name}.schema_version must be '
                             f'{schema!r}, got {entry.get("schema_version")!r}')
        shas[name] = require_hex64(entry.get('sha256'),
                                   f'capture private-plan.json source_assets.{name}.sha256')
    gate_gui = require_object(gate['bindings'].get('gui_plan'), 'gate plan bindings.gui_plan')
    if shas['gui_plan'] != gate_gui['sha256']:
        raise ValueError('capture private-plan.json source_assets.gui_plan sha256 does not match the '
                         'gate plan gui_plan binding; the capture was not compiled from the gated GUI '
                         'plan')
    gate_receipt = require_object(gate['bindings'].get('backend_receipt'),
                                  'gate plan bindings.backend_receipt')
    if shas['backend_receipt'] != receipt_sha or shas['backend_receipt'] != gate_receipt['sha256']:
        raise ValueError('capture private-plan.json source_assets.backend_receipt sha256 does not '
                         'match the backend receipt/gate binding; the capture provenance is stale')
    if shas['backend_plan'] != receipt['plan_binding'].get('plan_sha256') or \
            shas['backend_plan'] != gate_receipt.get('plan_sha256'):
        raise ValueError('capture private-plan.json source_assets.backend_plan sha256 does not match '
                         'the backend plan bound by the receipt/gate')
    if shas['gate_plan'] != gate_sha:
        raise ValueError('capture private-plan.json source_assets.gate_plan sha256 does not match '
                         'the supplied gate plan; the capture was compiled against another gate')
    return shas


def check_capture_bindings(plan, plan_sha, capture_receipt, gate_scope):
    plan_scope = plan.get('episode_scope')
    if plan_scope is not None:
        require_object(plan_scope, 'capture private-plan.json episode_scope')
    for field in IDENTITY_FIELDS:
        plan_value = require_text(plan.get(field), f'capture private-plan.json {field}')
        if plan_scope is not None and plan_scope.get(field) != plan_value:
            raise ValueError(f'capture private-plan.json episode_scope {field} '
                             f'{plan_scope.get(field)!r} does not match its top-level {field} '
                             f'{plan_value!r}')
        if plan_value != gate_scope[field]:
            raise ValueError(f'capture private-plan.json {field} {plan_value!r} does not match the '
                             f'gated episode_scope {gate_scope[field]!r}')
    world = plan.get('world_variant_id')
    if world is not None:
        require_text(world, 'capture private-plan.json world_variant_id')
        if plan_scope is not None and plan_scope.get('world_variant_id') not in (None, world):
            raise ValueError(f'capture private-plan.json episode_scope world_variant_id '
                             f'{plan_scope.get("world_variant_id")!r} does not match its top-level '
                             f'world_variant_id {world!r}')
        if world != gate_scope['world_variant_id']:
            raise ValueError(f'capture private-plan.json world_variant_id {world!r} does not match '
                             f'the gated episode_scope {gate_scope["world_variant_id"]!r}')
    if plan_scope is not None:
        for field in ('track', 'decision_point', 'timezone'):
            value = plan_scope.get(field)
            if value is None:
                continue
            require_text(value, f'capture private-plan.json episode_scope {field}')
            gate_value = gate_scope.get(field)
            if gate_value is not None and gate_value != value:
                raise ValueError(f'capture private-plan.json episode_scope {field} {value!r} does '
                                 f'not match the gated episode_scope {gate_value!r}')
    if capture_receipt.get('episode_id') != plan.get('episode_id'):
        raise ValueError('capture receipt episode_id does not match the capture plan')
    if capture_receipt.get('plan_sha256') != plan_sha:
        raise ValueError('capture receipt plan_sha256 does not match the capture private-plan.json '
                         'bytes; the plan changed after collection')
    source_assets = plan.get('source_assets')
    if not isinstance(source_assets, dict) or not source_assets:
        raise ValueError('capture private-plan.json needs a nonempty source_assets object')
    if capture_receipt.get('source_assets') != source_assets:
        raise ValueError('capture receipt source_assets do not match the capture plan provenance')


def plan_packets(plan):
    packets = plan.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('capture private-plan.json carries no packets')
    seen = set()
    for position, packet in enumerate(packets, start=1):
        require_object(packet, f'capture plan packet #{position}')
        packet_id = require_text(packet.get('packet_id'), f'capture plan packet #{position} packet_id')
        if packet_id in seen:
            raise ValueError(f'capture plan packet_id {packet_id!r} is duplicated')
        if packet.get('tag') != 'gui':
            raise ValueError(f'capture plan packet {packet_id!r} must be tag=gui; the compiled '
                             f'capture plan carries only executable GUI packets')
        seen.add(packet_id)
    return packets


def unsupported_packets(plan):
    """Return the compiled plan's declared-unsupported gate targets by id."""
    entries = plan.get('unsupported_packets', [])
    if not isinstance(entries, list):
        raise ValueError('capture private-plan.json unsupported_packets must be a list')
    unsupported = {}
    for position, entry in enumerate(entries, start=1):
        require_object(entry, f'capture plan unsupported packet #{position}')
        packet_id = require_text(entry.get('packet_id'),
                                 f'capture plan unsupported packet #{position} packet_id')
        if packet_id in unsupported:
            raise ValueError(f'capture plan unsupported packet_id {packet_id!r} is duplicated')
        require_positive_int(entry.get('sequence'),
                             f'capture plan unsupported packet {packet_id!r} sequence')
        require_text(entry.get('source'), f'capture plan unsupported packet {packet_id!r} source')
        require_text(entry.get('reason'), f'capture plan unsupported packet {packet_id!r} reason')
        unsupported[packet_id] = entry
    return unsupported


def declared_frames(plan, targets):
    """Bind gate targets to the compiled plan and return the ordered frame binding.

    Every gate target must be accounted for exactly once: either as an
    executable capture packet with declared frames, or as an explicitly
    unsupported packet with a nonempty reason. Gate targets may never silently
    disappear from the compiled plan, and executable/unsupported packets may
    never name a packet the gate did not target.
    """
    packets = plan_packets(plan)
    by_id = {packet['packet_id']: packet for packet in packets}
    unsupported = unsupported_packets(plan)
    target_ids = [target['packet_id'] for target in targets]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError('gate plan carries duplicate target packet_ids')
    overlap = set(by_id) & set(unsupported)
    if overlap:
        raise ValueError(f'capture plan packets {sorted(overlap)} are both executable and '
                         f'unsupported; refusing an ambiguous capture plan')
    missing = set(target_ids) - set(by_id) - set(unsupported)
    if missing:
        raise ValueError(f'gate targets {sorted(missing)} are neither captured nor declared '
                         f'unsupported in the capture plan')
    extra = (set(by_id) | set(unsupported)) - set(target_ids)
    if extra:
        raise ValueError(f'capture plan declares packets {sorted(extra)} that are not gate targets')
    expected = []
    for target in targets:
        packet_id = target['packet_id']
        packet = by_id.get(packet_id)
        if packet is None:
            entry = unsupported[packet_id]
            for field in ('sequence', 'source'):
                if entry.get(field) != target.get(field):
                    raise ValueError(f'gate target {packet_id!r} {field} {target.get(field)!r} '
                                     f'differs from the unsupported capture plan entry '
                                     f'{entry.get(field)!r}')
            continue
        for field in ('sequence', 'source', 'observed_at'):
            if packet.get(field) != target.get(field):
                raise ValueError(f'gate target {packet_id!r} {field} {target.get(field)!r} differs '
                                 f'from the capture plan packet {packet.get(field)!r}')
        capture = packet.get('capture')
        if not isinstance(capture, dict):
            raise ValueError(f'capture plan packet {packet_id!r} needs a capture block; the plan '
                             f'was not prepared for device capture')
        frames = capture.get('frames')
        if not isinstance(frames, list) or not frames:
            raise ValueError(f'capture plan packet {packet_id!r} capture.frames must be a nonempty '
                             f'list')
        frame_ids = set()
        for item in frames:
            require_object(item, f'capture plan packet {packet_id!r} frame')
            frame_id = require_text(item.get('frame_id'),
                                    f'capture plan packet {packet_id!r} frame_id')
            if frame_id in frame_ids:
                raise ValueError(f'capture plan packet {packet_id!r} frame_id {frame_id!r} is '
                                 f'duplicated')
            frame_ids.add(frame_id)
            expected.append((packet_id, frame_id))
    return expected


def check_capture_frames(capture_dir, capture_receipt, expected, targets):
    frames = capture_receipt.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('capture receipt carries no frames')
    declared_originals = {target['screenshot_sha256'] for target in targets
                          if target['screenshot_declared']}
    bindings, seen_ids, seen_png = [], set(), set()
    for position, frame in enumerate(frames, start=1):
        require_object(frame, f'capture receipt frame #{position}')
        packet_id = require_text(frame.get('packet_id'), f'capture receipt frame #{position} packet_id')
        frame_id = require_text(frame.get('frame_id'), f'capture receipt frame #{position} frame_id')
        if (packet_id, frame_id) in seen_ids:
            raise ValueError(f'capture receipt frame {packet_id!r}/{frame_id!r} is duplicated')
        seen_ids.add((packet_id, frame_id))
        png = relative_png(frame.get('png'), f'capture receipt frame {packet_id!r}/{frame_id!r} png')
        if png in seen_png:
            raise ValueError(f'capture receipt png {png!r} is reused by more than one frame')
        seen_png.add(png)
        sha256 = require_hex64(frame.get('sha256'),
                               f'capture receipt frame {packet_id!r}/{frame_id!r} sha256')
        width = require_positive_int(frame.get('width'),
                                     f'capture receipt frame {packet_id!r}/{frame_id!r} width')
        height = require_positive_int(frame.get('height'),
                                      f'capture receipt frame {packet_id!r}/{frame_id!r} height')
        if sha256 in declared_originals:
            raise ValueError(f'capture frame {packet_id!r}/{frame_id!r} is byte-identical to a '
                             f'pre-existing PB screenshot; refusing to re-publish the original as a '
                             f'device capture')
        path = capture_dir / png
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(capture_dir) or not path.is_file():
                raise ValueError(f'capture frame png is missing or escapes the capture directory: '
                                 f'{png!r}')
            file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f'capture frame png is unavailable: {error}') from None
        if file_sha256 != sha256:
            raise ValueError(f'capture frame png hash does not match the receipt: {png!r}')
        bindings.append({'packet_id': packet_id, 'frame_id': frame_id, 'png': png, 'sha256': sha256,
                         'width': width, 'height': height, 'status': 'pass'})
    ordered = list(expected)
    observed = [(frame['packet_id'], frame['frame_id']) for frame in frames]
    if observed != ordered:
        raise ValueError('capture receipt frame binding is incomplete or reordered: expected '
                         f'{ordered}, got {observed}')
    return bindings


def check_machine_qa(capture_dir, capture_receipt, plan_sha, expected, frame_bindings):
    try:
        audit = machine_qa.audit_capture(capture_dir)
    except Exception as error:
        raise ValueError(f'machine QA audit_capture failed: {error}') from None
    require_object(audit, 'machine QA report')
    if audit.get('status') != 'pass':
        raise ValueError(f"machine QA status must be 'pass', got {audit.get('status')!r}: "
                         f"{audit.get('reason')}")
    rule = audit.get('rule_version')
    if rule not in machine_qa.KNOWN_RULES:
        raise ValueError(f'machine QA rule_version {rule!r} is not a known pixel rule')
    model = audit.get('ocr_model_sha256')
    if model not in machine_qa.OCR_MODELS.values():
        raise ValueError(f'machine QA ocr_model_sha256 {model!r} is not a known OCR model')
    if audit.get('plan_sha256') != plan_sha:
        raise ValueError('machine QA plan_sha256 does not match the capture private-plan.json bytes')
    if audit.get('capture_run_id') != capture_receipt.get('capture_run_id'):
        raise ValueError('machine QA capture_run_id does not match the capture receipt')
    qa_frames = audit.get('frames')
    if not isinstance(qa_frames, list) or not qa_frames:
        raise ValueError('machine QA report carries no frames')
    index = {}
    for position, frame in enumerate(qa_frames, start=1):
        require_object(frame, f'machine QA frame #{position}')
        key = (require_text(frame.get('packet_id'), f'machine QA frame #{position} packet_id'),
               require_text(frame.get('frame_id'), f'machine QA frame #{position} frame_id'))
        if key in index:
            raise ValueError(f'machine QA frame {key[0]!r}/{key[1]!r} is duplicated')
        index[key] = frame
    for binding in frame_bindings:
        key = (binding['packet_id'], binding['frame_id'])
        qa_frame = index.get(key)
        if qa_frame is None:
            raise ValueError(f'machine QA has no frame for capture frame {key[0]!r}/{key[1]!r}')
        if qa_frame.get('status') != 'pass':
            raise ValueError(f'machine QA frame {key[0]!r}/{key[1]!r} status is '
                             f'{qa_frame.get("status")!r}, not pass')
        if qa_frame.get('png') != binding['png'] or qa_frame.get('sha256') != binding['sha256']:
            raise ValueError(f'machine QA frame {key[0]!r}/{key[1]!r} does not bind the capture '
                             f'receipt png/sha256')
    if len(qa_frames) != len(frame_bindings):
        raise ValueError('machine QA frame count does not match the capture receipt')
    for packet_id in sorted({packet_id for packet_id, _ in expected}):
        passes = [frame for frame in qa_frames if frame.get('packet_id') == packet_id
                  and frame.get('status') == 'pass']
        if not passes:
            raise ValueError(f'GUI capture target {packet_id!r} has no machine QA pass frame')
    stored = []
    for name in QA_REPORT_NAMES:
        path = capture_dir / name
        if not path.is_file():
            continue
        document = pb.load_json(path, f'stored machine QA report {name}')
        if document != audit:
            raise ValueError(f'stored machine QA report {name} differs from a fresh audit_capture '
                             f'run; refusing to release a changed or rejected capture')
        stored.append(name)
    return audit, stored


def build_release_report(capture_dir, gate_path, receipt_path):
    capture_dir = Path(capture_dir).expanduser().resolve()
    gate_path = Path(gate_path).expanduser().resolve()
    receipt_path = Path(receipt_path).expanduser().resolve()
    if not capture_dir.is_dir():
        raise ValueError(f'capture directory not found: {capture_dir}')
    if not gate_path.is_file():
        raise ValueError(f'gate plan not found: {gate_path}')
    if not receipt_path.is_file():
        raise ValueError(f'backend receipt not found: {receipt_path}')
    gate = pb.load_json(gate_path, 'gate plan')
    require_object(gate, 'gate plan')
    gate_sha = check_capture_gate(gate, gate_path)
    scope = gate['episode_scope']
    receipt = pb.load_json(receipt_path, 'backend receipt')
    require_object(receipt, 'backend receipt')
    receipt_sha = check_backend_receipt(receipt, receipt_path, gate)
    plan, plan_path, capture_receipt = load_capture_inputs(capture_dir)
    plan_sha = pb.digest(plan_path)
    check_capture_source_assets(plan, gate, gate_sha, receipt, receipt_sha)
    check_capture_bindings(plan, plan_sha, capture_receipt, scope)
    targets = gate['targets']
    expected = declared_frames(plan, targets)
    captured_ids = {packet_id for packet_id, _ in expected}
    captured_targets = [target for target in targets if target['packet_id'] in captured_ids]
    frame_bindings = check_capture_frames(capture_dir, capture_receipt, expected, targets)
    audit, stored = check_machine_qa(capture_dir, capture_receipt, plan_sha, expected,
                                     frame_bindings)
    by_source = {}
    for target in captured_targets:
        by_source[target['source']] = by_source.get(target['source'], 0) + 1
    report = {
        'schema_version': REPORT_SCHEMA,
        'status': RELEASE_STATUS,
        'command': 'preflight',
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'release_ready': True,
        'episode_scope': {
            'cluster_id': scope['cluster_id'],
            'episode_id': scope['episode_id'],
            'passive_id': scope['passive_id'],
            'world_variant_id': scope['world_variant_id'],
            **{field: scope[field] for field in ('track', 'decision_point', 'timezone')
               if scope.get(field) is not None},
        },
        'bindings': {
            'gate_plan': {'schema_version': GATE_SCHEMA, 'sha256': gate_sha, 'status': gate['status'],
                          'targets': len(targets)},
            'backend_receipt': {'schema_version': BACKEND_RECEIPT_SCHEMA, 'sha256': receipt_sha,
                                'status': 'ok', 'device_applied': True,
                                'applied_at': receipt.get('applied_at')},
            'private_plan': {'schema_version': CAPTURE_PLAN_SCHEMA, 'sha256': plan_sha},
            'capture_receipt': {'schema_version': CAPTURE_RECEIPT_SCHEMA,
                                'sha256': pb.digest(capture_dir / 'receipt.json'),
                                'status': 'capture_complete', 'reset_verified': True,
                                'capture_run_id': capture_receipt['capture_run_id'],
                                'frames': len(frame_bindings)},
            'machine_qa': {'schema_version': audit.get('schema_version'), 'status': 'pass',
                           'rule_version': audit['rule_version'],
                           'ocr_model_sha256': audit['ocr_model_sha256'],
                           'frames': len(audit['frames']), 'stored_reports': stored},
        },
        'machine_qa': {'rule_version': audit['rule_version'],
                       'ocr_model_sha256': audit['ocr_model_sha256'],
                       'ocr_models': audit.get('ocr_models')},
        'counts': {
            'targets': len(captured_targets),
            'gate_targets': len(targets),
            'unsupported_targets': len(targets) - len(captured_targets),
            'frames': len(frame_bindings),
            'pass_frames': len(frame_bindings),
            'declared_screenshot_targets': sum(1 for target in captured_targets
                                               if target['screenshot_declared']),
            'derived_screenshot_targets': sum(1 for target in captured_targets
                                              if not target['screenshot_declared']),
            'by_source': by_source,
        },
        'frames': frame_bindings,
        'checks': {'private_fields_emitted': 0, 'absolute_paths_emitted': 0, 'images_copied': 0,
                   'images_rewritten': 0, 'device_writes': 0, 'gold_generated': 0,
                   'publish_invoked': 0},
    }
    capture_gate.assert_private_free(report, 'release report')
    capture_gate.assert_no_absolute_paths(report, 'release report')
    return report


def validate_report(report):
    require_object(report, 'release report')
    if report.get('schema_version') != REPORT_SCHEMA:
        raise ValueError(f'release report schema_version must be {REPORT_SCHEMA!r}, '
                         f'got {report.get("schema_version")!r}')
    if report.get('status') != RELEASE_STATUS:
        raise ValueError(f'release report status must be {RELEASE_STATUS!r}, '
                         f'got {report.get("status")!r}')
    if report.get('release_ready') is not True:
        raise ValueError('release report release_ready must be true')
    capture_gate.assert_private_free(report, 'release report')
    capture_gate.assert_no_absolute_paths(report, 'release report')
    scope = require_object(report.get('episode_scope'), 'release report episode_scope')
    for field in SCOPE_FIELDS:
        require_text(scope.get(field), f'release report episode_scope {field}')
    bindings = require_object(report.get('bindings'), 'release report bindings')
    shas = {}
    for name, schema in (('gate_plan', GATE_SCHEMA), ('backend_receipt', BACKEND_RECEIPT_SCHEMA),
                         ('private_plan', CAPTURE_PLAN_SCHEMA),
                         ('capture_receipt', CAPTURE_RECEIPT_SCHEMA)):
        entry = require_object(bindings.get(name), f'release report bindings.{name}')
        if entry.get('schema_version') != schema:
            raise ValueError(f'release report bindings.{name}.schema_version must be {schema!r}, '
                             f'got {entry.get("schema_version")!r}')
        shas[name] = require_hex64(entry.get('sha256'), f'release report bindings.{name}.sha256')
    if bindings['gate_plan'].get('status') != capture_gate.GATE_STATUS:
        raise ValueError('release report must bind a gate plan that was ready for device capture')
    if bindings['backend_receipt'].get('status') != 'ok' or \
            bindings['backend_receipt'].get('device_applied') is not True:
        raise ValueError('release report must bind a backend receipt with status=ok and '
                         'device_applied=true')
    capture_binding = bindings['capture_receipt']
    if capture_binding.get('status') != 'capture_complete' or \
            capture_binding.get('reset_verified') is not True:
        raise ValueError('release report must bind a capture receipt that is complete and reset')
    require_text(capture_binding.get('capture_run_id'),
                 'release report bindings.capture_receipt.capture_run_id')
    machine_binding = require_object(bindings.get('machine_qa'), 'release report bindings.machine_qa')
    if machine_binding.get('status') != 'pass':
        raise ValueError('release report must bind machine QA status=pass')
    rule = machine_binding.get('rule_version')
    if rule not in machine_qa.KNOWN_RULES:
        raise ValueError(f'release report machine QA rule_version {rule!r} is not a known pixel rule')
    model = machine_binding.get('ocr_model_sha256')
    if model not in machine_qa.OCR_MODELS.values():
        raise ValueError(f'release report machine QA ocr_model_sha256 {model!r} is not a known '
                         f'OCR model')
    require_positive_int(machine_binding.get('frames'), 'release report bindings.machine_qa.frames')
    stored = machine_binding.get('stored_reports')
    if not isinstance(stored, list) or any(name not in QA_REPORT_NAMES for name in stored):
        raise ValueError('release report bindings.machine_qa.stored_reports must list stored QA '
                         'report names')
    machine = require_object(report.get('machine_qa'), 'release report machine_qa')
    if machine.get('rule_version') != rule or machine.get('ocr_model_sha256') != model:
        raise ValueError('release report machine_qa rule/model does not match its binding')
    models = machine.get('ocr_models')
    if not isinstance(models, dict) or any(not HEX64.fullmatch(str(value))
                                           for value in models.values()) or model not in models.values():
        raise ValueError('release report machine_qa.ocr_models must carry the hashed OCR models')
    frames = report.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('release report carries no frames')
    seen_ids, seen_png = set(), set()
    for position, frame in enumerate(frames, start=1):
        require_object(frame, f'release report frame #{position}')
        packet_id = require_text(frame.get('packet_id'), f'release report frame #{position} packet_id')
        frame_id = require_text(frame.get('frame_id'), f'release report frame #{position} frame_id')
        if (packet_id, frame_id) in seen_ids:
            raise ValueError(f'release report frame {packet_id!r}/{frame_id!r} is duplicated')
        seen_ids.add((packet_id, frame_id))
        png = relative_png(frame.get('png'), f'release report frame #{position} png')
        if png in seen_png:
            raise ValueError(f'release report png {png!r} is reused by more than one frame')
        seen_png.add(png)
        require_hex64(frame.get('sha256'), f'release report frame #{position} sha256')
        require_positive_int(frame.get('width'), f'release report frame #{position} width')
        require_positive_int(frame.get('height'), f'release report frame #{position} height')
        if frame.get('status') != 'pass':
            raise ValueError(f'release report frame {packet_id!r}/{frame_id!r} status must be pass')
    counts = require_object(report.get('counts'), 'release report counts')
    targets = len({packet_id for packet_id, _ in seen_ids})
    expected_counts = {'targets': targets, 'frames': len(frames), 'pass_frames': len(frames)}
    for field, value in expected_counts.items():
        if counts.get(field) != value:
            raise ValueError(f'release report counts.{field} is {counts.get(field)!r}, expected '
                             f'{value!r}')
    unsupported = counts.get('unsupported_targets')
    if not isinstance(unsupported, int) or isinstance(unsupported, bool) or unsupported < 0:
        raise ValueError('release report counts.unsupported_targets must be a nonnegative integer')
    if counts.get('gate_targets') != targets + unsupported:
        raise ValueError('release report counts.gate_targets must equal the released targets plus '
                         'the declared unsupported targets')
    if counts.get('declared_screenshot_targets', -1) + counts.get('derived_screenshot_targets', -1) \
            != targets:
        raise ValueError('release report screenshot target counts do not add up to the targets')
    by_source = counts.get('by_source')
    if not isinstance(by_source, dict) or not by_source or \
            any(not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in by_source.values()) or sum(by_source.values()) != targets:
        raise ValueError('release report counts.by_source does not add up to the targets')
    checks = require_object(report.get('checks'), 'release report checks')
    for field in ('private_fields_emitted', 'absolute_paths_emitted', 'images_copied',
                  'images_rewritten', 'device_writes', 'gold_generated', 'publish_invoked'):
        if checks.get(field) != 0:
            raise ValueError(f'release report checks.{field} must be 0, got {checks.get(field)!r}')
    return {'schema_version': VALIDATION_SCHEMA, 'status': 'ok',
            'cluster_id': scope['cluster_id'], 'episode_id': scope['episode_id'],
            'passive_id': scope['passive_id'], 'world_variant_id': scope['world_variant_id'],
            'targets': targets, 'gate_targets': counts['gate_targets'],
            'unsupported_targets': unsupported, 'frames': len(frames), 'rule_version': rule,
            'ocr_model_sha256': model, 'gate_plan_sha256': shas['gate_plan'],
            'backend_receipt_sha256': shas['backend_receipt'],
            'private_plan_sha256': shas['private_plan'],
            'capture_receipt_sha256': shas['capture_receipt']}


def comparable_document(document):
    clone = copy.deepcopy(document)
    clone.pop('generated_at', None)
    return clone


def command_preflight(args):
    output = Path(args.output).expanduser().resolve()
    if output.is_dir():
        raise ValueError(f'--output is a directory: {output}')
    report = build_release_report(args.capture_dir, args.gate_plan, args.backend_receipt)
    target = pb.write_json(output, report)
    print(json.dumps({'status': 'ok', 'command': 'preflight', 'release_ready': True,
                      'cluster_id': report['episode_scope']['cluster_id'],
                      'episode_id': report['episode_scope']['episode_id'],
                      'passive_id': report['episode_scope']['passive_id'],
                      'world_variant_id': report['episode_scope']['world_variant_id'],
                      'targets': report['counts']['targets'],
                      'gate_targets': report['counts']['gate_targets'],
                      'unsupported_targets': report['counts']['unsupported_targets'],
                      'frames': report['counts']['frames'],
                      'rule_version': report['machine_qa']['rule_version'],
                      'ocr_model_sha256': report['machine_qa']['ocr_model_sha256'],
                      'output': str(target)}, ensure_ascii=False))
    return 0


def command_validate(args):
    report_path = Path(args.report).expanduser().resolve()
    report = pb.load_json(report_path, 'release report')
    summary = validate_report(report)
    has_inputs = any(value is not None for value in (args.capture_dir, args.gate_plan,
                                                     args.backend_receipt))
    if has_inputs and not all(value is not None for value in (args.capture_dir, args.gate_plan,
                                                              args.backend_receipt)):
        raise ValueError('validate needs --capture-dir, --gate-plan and --backend-receipt together '
                         'to re-verify the recorded hashes, or none of them for a structural check')
    summary['report'] = str(report_path)
    summary['hashes_verified'] = has_inputs
    if has_inputs:
        rebuilt = build_release_report(args.capture_dir, args.gate_plan, args.backend_receipt)
        if comparable_document(rebuilt) != comparable_document(report):
            raise ValueError('release report does not match a rebuild from the supplied capture '
                             'dir, gate plan and backend receipt; the recorded hashes/bindings are '
                             'stale')
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_gui_release_gate.py',
        description='Round-4 QA/pre-release gate: verify a completed PB GUI capture against the '
                    'round-3 gate plan and the backend receipt, then emit a private '
                    'release-ready report (fail closed).')
    subparsers = parser.add_subparsers(dest='command', required=True)
    preflight = subparsers.add_parser(
        'preflight', help='verify the capture chain and write the private release report')
    preflight.add_argument('--capture-dir', type=Path, required=True,
                           help='completed capture directory with receipt.json/private-plan.json')
    preflight.add_argument('--gate-plan', type=Path, required=True,
                           help='round-3 pb-gui-capture-gate.v1 plan')
    preflight.add_argument('--backend-receipt', type=Path, required=True,
                           help='round-2 pb-backend-apply-receipt.v1 success receipt')
    preflight.add_argument('--output', type=Path, required=True,
                           help='write the private pb-gui-release-report.v1 here')
    validate = subparsers.add_parser(
        'validate', help='re-check a stored release report; with all inputs also re-verify hashes')
    validate.add_argument('--report', type=Path, required=True, help='release report JSON')
    validate.add_argument('--capture-dir', type=Path,
                          help='capture directory to rebuild and compare against')
    validate.add_argument('--gate-plan', type=Path, help='gate plan to rebuild and compare against')
    validate.add_argument('--backend-receipt', type=Path,
                          help='backend receipt to rebuild and compare against')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'preflight':
            return command_preflight(args)
        return command_validate(args)
    except ValueError as error:
        print(f'pb_gui_release_gate.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
