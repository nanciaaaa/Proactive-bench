#!/usr/bin/env python3
"""Round-3 pre-capture gate between the private PB GUI plan and the device receipt.

This gate is the only door from offline planning to device capture in the PB GUI
alignment pipeline. It reads the round-1 private plan produced by
``pb_gui_bridge.py`` and the round-2 success receipt produced by
``pb_backend_apply.py`` and refuses to hand the capture side any target that is
not already backed by readback device state:

* the GUI plan and the receipt must describe the same episode scope
  (``cluster_id`` / ``episode_id`` / ``passive_id`` and, when the plan carries
  one, the world variant);
* the receipt must be ``pb-backend-apply-receipt.v1`` with ``status=ok`` and
  ``device_applied=true``;
* every ``tag=gui`` packet source must have at least one receipt record for the
  same Calendar/Mail/Clock source whose adapter matches the known contract and
  whose seed and readback both executed; a source without a supported adapter is
  never counted as supported;
* the declared ``screenshot_ref`` / ``screenshot_sha256`` pair is carried as a
  pending capture target only: the gate never copies, rewrites or re-hashes PB
  images, never writes a device and never claims completion (``capture_state``
  stays ``pending_device_capture``);
* private GT/Need/Oracle fields and local absolute paths abort the run.

The emitted gate plan is a private operational artifact: it binds the input
hashes, the required adapter/readback functions and the ordered capture targets,
and it contains no public-release material.

Usage::

    python3 scripts/pb_gui_capture_gate.py preflight --gui-plan plan.json \
        --backend-receipt receipt.json --output gate.json
    python3 scripts/pb_gui_capture_gate.py validate --gate-plan gate.json \
        [--gui-plan plan.json --backend-receipt receipt.json]

``validate`` re-checks a stored gate plan structurally; when both inputs are
given it rebuilds the gate from them and refuses a plan whose recorded hashes or
bindings no longer match.
"""
import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pb_backend_plan as pb

GATE_SCHEMA = 'pb-gui-capture-gate.v1'
VALIDATION_SCHEMA = 'pb-gui-capture-gate-validation.v1'
GATE_STATUS = 'ready_for_device_capture'
PENDING_CAPTURE = 'pending_device_capture'
GUI_PLAN_SCHEMA = 'pb-gui-bridge.plan.v1'
RECEIPT_SCHEMA = 'pb-backend-apply-receipt.v1'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
ABSOLUTE_PREFIX = re.compile(r'^(?:/|[A-Za-z]:[\\/]|\\\\)')
IDENTITY_FIELDS = ('cluster_id', 'episode_id', 'passive_id')
ADAPTER_FIELDS = ('module', 'seed', 'read')


def private_paths(document):
    """Private GT/Need/Oracle keys, including ``*_ref`` spellings."""
    found = list(pb.private_key_paths(document))
    for path, key in pb.walk_keys(document):
        normalized = pb.normalize(key)
        if normalized.endswith('ref') and normalized[:-3] in pb.PRIVATE_KEY_NAMES:
            found.append(path)
    return found


def assert_private_free(document, label):
    leaks = private_paths(document)
    if leaks:
        raise ValueError(f'{label} carries private GT/Need/Oracle fields: {leaks}')


def assert_no_absolute_paths(document, label):
    absolutes = pb.absolute_value_paths(document)
    if absolutes:
        raise ValueError(f'{label} carries local absolute paths: {absolutes}')


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value


def require_hex64(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError(f'{label} must be 64 lowercase hex characters, got {value!r}')
    return value


def relative_ref(value, label):
    text = require_text(value, label)
    if ABSOLUTE_PREFIX.match(text):
        raise ValueError(f'{label} must be bundle-relative, got {text!r}')
    if '..' in text.replace('\\', '/').split('/'):
        raise ValueError(f'{label} must not traverse outside its bundle, got {text!r}')
    return text


def adapter_contract(source):
    entry = pb.ADAPTERS[source]
    return {'module': entry['module'], 'seed': entry['seed'], 'read': entry['read']}


def load_document(path, expected_schema, label):
    document = pb.load_json(Path(path).expanduser().resolve(), label)
    if not isinstance(document, dict):
        raise ValueError(f'{label} must be a JSON object')
    if document.get('schema_version') != expected_schema:
        raise ValueError(f'{label} schema_version must be {expected_schema!r}, '
                         f'got {document.get("schema_version")!r}')
    return document


def check_scope(gui_plan, scope):
    """The GUI plan identity must match the receipt episode_scope exactly."""
    for field in IDENTITY_FIELDS:
        plan_value = require_text(gui_plan.get(field), f'GUI plan {field}')
        scope_value = require_text(scope.get(field), f'backend receipt episode_scope {field}')
        if plan_value != scope_value:
            raise ValueError(f'{field} mismatch: GUI plan {plan_value!r} vs backend receipt '
                             f'episode_scope {scope_value!r}')
    world = gui_plan.get('world_variant_id')
    if world is not None:
        plan_world = require_text(world, 'GUI plan world_variant_id')
        scope_world = require_text(scope.get('world_variant_id'),
                                   'backend receipt episode_scope world_variant_id')
        if plan_world != scope_world:
            raise ValueError(f'world_variant_id mismatch: GUI plan {plan_world!r} vs backend receipt '
                             f'episode_scope {scope_world!r}')
    for field in ('track', 'decision_point'):
        plan_value = gui_plan.get(field)
        if plan_value is None:
            continue
        require_text(plan_value, f'GUI plan {field}')
        scope_value = scope.get(field)
        if scope_value is not None and scope_value != plan_value:
            raise ValueError(f'{field} mismatch: GUI plan {plan_value!r} vs backend receipt '
                             f'episode_scope {scope_value!r}')


def project_targets(gui_plan):
    """Project every ``tag=gui`` packet into a pending capture target."""
    packets = gui_plan.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('GUI plan carries no packets')
    gui_dir = gui_plan.get('screenshot_dir')
    targets, last_sequence = [], 0
    for position, packet in enumerate(packets, start=1):
        if not isinstance(packet, dict):
            raise ValueError(f'GUI plan packet #{position} must be an object')
        if packet.get('tag') != 'gui':
            continue
        packet_id = require_text(packet.get('packet_id'), f'GUI plan packet #{position} packet_id')
        sequence = packet.get('sequence')
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError(f'GUI packet {packet_id!r} sequence must be a positive integer')
        if sequence <= last_sequence:
            raise ValueError(f'GUI packet {packet_id!r} sequence must strictly increase')
        last_sequence = sequence
        source = require_text(packet.get('source'), f'GUI packet {packet_id!r} source')
        observed_at = require_text(packet.get('observed_at'), f'GUI packet {packet_id!r} observed_at')
        if pb.maybe_timestamp(observed_at) is None:
            raise ValueError(f'GUI packet {packet_id!r} observed_at must be an ISO-8601 timestamp '
                             f'with a UTC offset')
        has_ref, has_sha = 'screenshot_ref' in packet, 'screenshot_sha256' in packet
        if has_ref != has_sha:
            raise ValueError(f'GUI packet {packet_id!r} must declare screenshot_ref and '
                             f'screenshot_sha256 together')
        if has_ref:
            screenshot_ref = relative_ref(packet['screenshot_ref'],
                                          f'GUI packet {packet_id!r} screenshot_ref')
            screenshot_sha = require_hex64(packet['screenshot_sha256'],
                                           f'GUI packet {packet_id!r} screenshot_sha256')
            declared = True
        else:
            if gui_dir is None:
                raise ValueError(f'GUI packet {packet_id!r} declares no screenshot pair and the GUI '
                                 f'plan carries no screenshot_dir to place the pending target')
            directory = relative_ref(gui_dir, 'GUI plan screenshot_dir').rstrip('/')
            screenshot_ref = f'{directory}/{packet_id}.png'
            screenshot_sha, declared = None, False
        if Path(screenshot_ref).name != f'{packet_id}.png':
            raise ValueError(f'GUI packet {packet_id!r} screenshot_ref must end in '
                             f'{packet_id}.png, got {screenshot_ref!r}')
        targets.append({
            'packet_id': packet_id,
            'sequence': sequence,
            'source': source,
            'observed_at': observed_at,
            'screenshot_ref': screenshot_ref,
            'screenshot_sha256': screenshot_sha,
            'screenshot_declared': declared,
            'capture_state': PENDING_CAPTURE,
        })
    if not targets:
        raise ValueError('GUI plan declares no tag=gui packets to capture')
    return targets


def check_receipt(receipt):
    if receipt.get('status') != 'ok':
        raise ValueError(f"backend receipt status must be 'ok', got {receipt.get('status')!r}")
    if receipt.get('device_applied') is not True:
        raise ValueError('backend receipt device_applied must be true; refusing to gate device '
                         'capture before the backend seed/readback succeeded')
    assert_private_free(receipt, 'backend receipt')
    assert_no_absolute_paths(receipt, 'backend receipt')
    binding = receipt.get('plan_binding')
    if not isinstance(binding, dict):
        raise ValueError('backend receipt needs a plan_binding object')
    scope = binding.get('episode_scope')
    if not isinstance(scope, dict):
        raise ValueError('backend receipt plan_binding needs an episode_scope object')
    return binding, scope


def receipt_index(receipt):
    """Group receipt records by source, refusing any unsupported source."""
    records = receipt.get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('backend receipt carries no seed/readback records')
    index = {}
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f'backend receipt record #{position} must be an object')
        source = record.get('source')
        if source not in pb.SUPPORTED_SOURCES:
            raise ValueError(f'backend receipt record #{position} source {source!r} has no supported '
                             f'adapter {list(pb.SUPPORTED_SOURCES)}; an unsupported source cannot back '
                             f'a GUI packet')
        index.setdefault(source, []).append(record)
    return index


def readback_refs(records, source):
    """Return the semantic refs whose seed and readback actually executed."""
    contract = adapter_contract(source)
    refs = []
    for record in records:
        ref = require_text(record.get('semantic_ref'), f'backend receipt {source} record semantic_ref')
        if record.get('status') != 'ok':
            raise ValueError(f'backend receipt record {ref!r} status is {record.get("status")!r}, not ok')
        adapter = record.get('adapter')
        if not isinstance(adapter, dict):
            raise ValueError(f'backend receipt record {ref!r} needs an adapter object')
        for field in ADAPTER_FIELDS:
            if adapter.get(field) != contract[field]:
                raise ValueError(f'backend receipt record {ref!r} adapter {field} {adapter.get(field)!r} '
                                 f'does not match the {source} contract {contract[field]!r}')
        for section, function in (('seed', contract['seed']), ('readback', contract['read'])):
            payload = record.get(section)
            if not isinstance(payload, dict):
                raise ValueError(f'backend receipt record {ref!r} needs a {section} object')
            if payload.get('function') != function:
                raise ValueError(f'backend receipt record {ref!r} {section}.function '
                                 f'{payload.get("function")!r} does not match {function!r}')
            if pb.maybe_timestamp(payload.get('at')) is None:
                raise ValueError(f'backend receipt record {ref!r} {section}.at must be an ISO-8601 '
                                 f'timestamp with a UTC offset')
            if payload.get('result') is None:
                raise ValueError(f'backend receipt record {ref!r} has no {section} result; the record '
                                 f'is not a verified readback')
        refs.append(ref)
    return refs


def build_gate_plan(gui_plan_path, receipt_path):
    gui_plan = load_document(gui_plan_path, GUI_PLAN_SCHEMA, 'GUI plan')
    # The bridge plan legitimately carries its own absolute pb_root; what matters
    # is that no private field is consumed and that every copied value is later
    # scanned for leaks in the emitted gate plan.
    assert_private_free(gui_plan, 'GUI plan')
    receipt = load_document(receipt_path, RECEIPT_SCHEMA, 'backend receipt')
    binding, scope = check_receipt(receipt)
    check_scope(gui_plan, scope)
    targets = project_targets(gui_plan)
    index = receipt_index(receipt)
    verified = {source: readback_refs(records, source) for source, records in index.items()}
    adapter_bindings, used_sources = {}, []
    for source in sorted({target['source'] for target in targets}):
        if source not in pb.SUPPORTED_SOURCES:
            raise ValueError(f'GUI packet source {source!r} is unsupported: only '
                             f'{list(pb.SUPPORTED_SOURCES)} have seed/readback adapters; refusing to '
                             f'gate device capture')
        refs = verified.get(source) or []
        if not refs:
            raise ValueError(f'GUI packet source {source!r} has no readback backend record in the '
                             f'receipt; refusing to gate device capture')
        contract = adapter_contract(source)
        adapter_bindings[source] = {'module': contract['module'], 'seed_function': contract['seed'],
                                    'read_function': contract['read'], 'semantic_refs': refs,
                                    'record_count': len(refs), 'readback_verified': True}
        used_sources.append(source)
    for target in targets:
        contract = adapter_contract(target['source'])
        refs = verified[target['source']]
        target['adapter_binding'] = {'module': contract['module'], 'seed': contract['seed'],
                                     'read': contract['read']}
        target['readback_binding'] = {'semantic_refs': list(refs), 'record_count': len(refs),
                                      'verified': True}
    declared = sum(1 for target in targets if target['screenshot_declared'])
    by_source = {source: sum(1 for target in targets if target['source'] == source)
                 for source in used_sources}
    device = receipt.get('device') if isinstance(receipt.get('device'), dict) else {}
    gate = {
        'schema_version': GATE_SCHEMA,
        'status': GATE_STATUS,
        'command': 'preflight',
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'capture_state': PENDING_CAPTURE,
        'device_capture_started': False,
        'episode_scope': {
            'cluster_id': scope['cluster_id'],
            'episode_id': scope['episode_id'],
            'passive_id': scope['passive_id'],
            'world_variant_id': scope.get('world_variant_id'),
            'track': scope.get('track'),
            'decision_point': scope.get('decision_point'),
            'timezone': scope.get('timezone'),
            'world_status': scope.get('world_status'),
            'world_hash': scope.get('world_hash'),
        },
        'bindings': {
            'gui_plan': {'schema_version': GUI_PLAN_SCHEMA, 'sha256': pb.digest(gui_plan_path),
                         'screenshot_dir': gui_plan.get('screenshot_dir'),
                         'packets': len(gui_plan['packets']), 'gui_packets': len(targets)},
            'backend_receipt': {'schema_version': RECEIPT_SCHEMA, 'sha256': pb.digest(receipt_path),
                                'status': receipt['status'], 'device_applied': True,
                                'applied_at': receipt.get('applied_at'),
                                'device_serial': device.get('serial'),
                                'device_timezone': device.get('timezone'),
                                'plan_sha256': binding.get('plan_sha256'),
                                'records': len(receipt['records'])},
        },
        'adapter_bindings': adapter_bindings,
        'targets': targets,
        'counts': {'targets': len(targets), 'declared_screenshot_targets': declared,
                   'derived_screenshot_targets': len(targets) - declared, 'by_source': by_source},
        'checks': {
            'gui_packets': len(targets),
            'targets': len(targets),
            'sources_bound': len(used_sources),
            'sources_with_readback': len(used_sources),
            'sources_unsupported': 0,
            'readback_records_bound': sum(len(verified[source]) for source in used_sources),
            'private_fields_emitted': 0,
            'absolute_paths_emitted': 0,
            'images_copied': 0,
            'images_rewritten': 0,
            'device_writes': 0,
        },
    }
    assert_private_free(gate, 'gate plan')
    assert_no_absolute_paths(gate, 'gate plan')
    return gate


def validate_target(target, position, last_sequence):
    packet_id = require_text(target.get('packet_id'), f'gate plan target #{position} packet_id')
    sequence = target.get('sequence')
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError(f'gate plan target {packet_id!r} sequence must be a positive integer')
    if sequence <= last_sequence:
        raise ValueError(f'gate plan target {packet_id!r} sequence must strictly increase')
    source = require_text(target.get('source'), f'gate plan target {packet_id!r} source')
    if source not in pb.SUPPORTED_SOURCES:
        raise ValueError(f'gate plan target {packet_id!r} source {source!r} has no supported adapter')
    observed_at = require_text(target.get('observed_at'), f'gate plan target {packet_id!r} observed_at')
    if pb.maybe_timestamp(observed_at) is None:
        raise ValueError(f'gate plan target {packet_id!r} observed_at must be an ISO-8601 timestamp '
                         f'with a UTC offset')
    screenshot_ref = relative_ref(target.get('screenshot_ref'),
                                  f'gate plan target {packet_id!r} screenshot_ref')
    if Path(screenshot_ref).name != f'{packet_id}.png':
        raise ValueError(f'gate plan target {packet_id!r} screenshot_ref must end in '
                         f'{packet_id}.png')
    screenshot_sha = target.get('screenshot_sha256')
    declared = target.get('screenshot_declared')
    if not isinstance(declared, bool):
        raise ValueError(f'gate plan target {packet_id!r} screenshot_declared must be a boolean')
    if declared:
        require_hex64(screenshot_sha, f'gate plan target {packet_id!r} screenshot_sha256')
    elif screenshot_sha is not None:
        raise ValueError(f'gate plan target {packet_id!r} has a screenshot_sha256 without a declared '
                         f'screenshot pair')
    if target.get('capture_state') != PENDING_CAPTURE:
        raise ValueError(f'gate plan target {packet_id!r} capture_state must be {PENDING_CAPTURE!r}')
    contract = adapter_contract(source)
    adapter = target.get('adapter_binding')
    if not isinstance(adapter, dict):
        raise ValueError(f'gate plan target {packet_id!r} needs an adapter_binding object')
    for field in ADAPTER_FIELDS:
        if adapter.get(field) != contract[field]:
            raise ValueError(f'gate plan target {packet_id!r} adapter_binding {field} '
                             f'{adapter.get(field)!r} does not match {contract[field]!r}')
    readback = target.get('readback_binding')
    if not isinstance(readback, dict) or readback.get('verified') is not True:
        raise ValueError(f'gate plan target {packet_id!r} needs a verified readback_binding')
    refs = readback.get('semantic_refs')
    if not isinstance(refs, list) or not refs:
        raise ValueError(f'gate plan target {packet_id!r} readback_binding needs nonempty semantic_refs')
    for ref in refs:
        require_text(ref, f'gate plan target {packet_id!r} readback semantic_ref')
    if readback.get('record_count') != len(refs):
        raise ValueError(f'gate plan target {packet_id!r} readback_binding record_count does not match '
                         f'its semantic_refs')
    return source, sequence


def validate_gate_plan(gate):
    if not isinstance(gate, dict):
        raise ValueError('gate plan must be a JSON object')
    if gate.get('schema_version') != GATE_SCHEMA:
        raise ValueError(f'gate plan schema_version must be {GATE_SCHEMA!r}, '
                         f'got {gate.get("schema_version")!r}')
    if gate.get('status') != GATE_STATUS:
        raise ValueError(f'gate plan status must be {GATE_STATUS!r}, got {gate.get("status")!r}')
    if gate.get('capture_state') != PENDING_CAPTURE:
        raise ValueError(f'gate plan capture_state must be {PENDING_CAPTURE!r}')
    if gate.get('device_capture_started') is not False:
        raise ValueError('gate plan must not claim that device capture has started')
    assert_private_free(gate, 'gate plan')
    assert_no_absolute_paths(gate, 'gate plan')
    scope = gate.get('episode_scope')
    if not isinstance(scope, dict):
        raise ValueError('gate plan needs an episode_scope object')
    for field in IDENTITY_FIELDS:
        require_text(scope.get(field), f'gate plan episode_scope {field}')
    targets = gate.get('targets')
    if not isinstance(targets, list) or not targets:
        raise ValueError('gate plan declares no capture targets')
    last_sequence, by_source = 0, {}
    for position, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            raise ValueError(f'gate plan target #{position} must be an object')
        source, sequence = validate_target(target, position, last_sequence)
        last_sequence = sequence
        by_source[source] = by_source.get(source, 0) + 1
    bindings = gate.get('bindings')
    if not isinstance(bindings, dict):
        raise ValueError('gate plan needs a bindings object')
    shas = {}
    for name, label, schema in (('gui_plan', 'GUI plan', GUI_PLAN_SCHEMA),
                                ('backend_receipt', 'backend receipt', RECEIPT_SCHEMA)):
        entry = bindings.get(name)
        if not isinstance(entry, dict):
            raise ValueError(f'gate plan bindings.{name} must be an object')
        if entry.get('schema_version') != schema:
            raise ValueError(f'gate plan bindings.{name}.schema_version must be {schema!r}, '
                             f'got {entry.get("schema_version")!r}')
        shas[name] = require_hex64(entry.get('sha256'), f'gate plan bindings.{name}.sha256')
    receipt_binding = bindings['backend_receipt']
    if receipt_binding.get('status') != 'ok' or receipt_binding.get('device_applied') is not True:
        raise ValueError('gate plan must bind a successful backend receipt with device_applied=true')
    adapter_bindings = gate.get('adapter_bindings')
    if not isinstance(adapter_bindings, dict):
        raise ValueError('gate plan needs an adapter_bindings object')
    for source in sorted(by_source):
        entry = adapter_bindings.get(source)
        if not isinstance(entry, dict) or entry.get('readback_verified') is not True:
            raise ValueError(f'gate plan adapter_bindings.{source} must be a verified binding')
        contract = adapter_contract(source)
        for key, field in (('module', 'module'), ('seed', 'seed_function'), ('read', 'read_function')):
            if entry.get(field) != contract[key]:
                raise ValueError(f'gate plan adapter_bindings.{source}.{field} '
                                 f'{entry.get(field)!r} does not match {contract[key]!r}')
    return {'schema_version': VALIDATION_SCHEMA, 'status': 'ok', 'gate_status': gate['status'],
            'cluster_id': scope['cluster_id'], 'episode_id': scope['episode_id'],
            'passive_id': scope['passive_id'], 'targets': len(targets), 'by_source': by_source,
            'gui_plan_sha256': shas['gui_plan'], 'backend_receipt_sha256': shas['backend_receipt']}


def comparable_document(document):
    clone = copy.deepcopy(document)
    clone.pop('generated_at', None)
    return clone


def command_preflight(args):
    gui_plan_path = Path(args.gui_plan).expanduser().resolve()
    receipt_path = Path(args.backend_receipt).expanduser().resolve()
    if not gui_plan_path.is_file():
        raise ValueError(f'GUI plan not found: {gui_plan_path}')
    if not receipt_path.is_file():
        raise ValueError(f'backend receipt not found: {receipt_path}; a successful '
                         f'pb_backend_apply.py receipt is required to gate device capture')
    output = Path(args.output).expanduser().resolve()
    if output.is_dir():
        raise ValueError(f'--output is a directory: {output}')
    gate = build_gate_plan(gui_plan_path, receipt_path)
    target = pb.write_json(output, gate)
    print(json.dumps({'status': 'ok', 'command': 'preflight', 'gate_status': gate['status'],
                      'cluster_id': gate['episode_scope']['cluster_id'],
                      'episode_id': gate['episode_scope']['episode_id'],
                      'passive_id': gate['episode_scope']['passive_id'],
                      'targets': gate['counts']['targets'],
                      'declared_screenshot_targets': gate['counts']['declared_screenshot_targets'],
                      'derived_screenshot_targets': gate['counts']['derived_screenshot_targets'],
                      'by_source': gate['counts']['by_source'],
                      'backend_receipt_sha256': gate['bindings']['backend_receipt']['sha256'],
                      'output': str(target)}, ensure_ascii=False))
    return 0


def command_validate(args):
    gate_path = Path(args.gate_plan).expanduser().resolve()
    has_gui, has_receipt = args.gui_plan is not None, args.backend_receipt is not None
    if has_gui != has_receipt:
        raise ValueError('validate needs --gui-plan and --backend-receipt together to re-verify the '
                         'recorded hashes, or neither for a structural check')
    gate = pb.load_json(gate_path, 'gate plan')
    summary = validate_gate_plan(gate)
    summary['gate_plan'] = str(gate_path)
    summary['hashes_verified'] = has_gui
    if has_gui:
        rebuilt = build_gate_plan(Path(args.gui_plan).expanduser().resolve(),
                                  Path(args.backend_receipt).expanduser().resolve())
        if comparable_document(rebuilt) != comparable_document(gate):
            raise ValueError('gate plan does not match a rebuild from the supplied GUI plan and '
                             'backend receipt; the recorded hashes/bindings are stale')
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_gui_capture_gate.py',
        description='Round-3 pre-capture gate: bind a private PB GUI plan to a successful backend '
                    'apply receipt and emit pending capture targets (fail closed).')
    subparsers = parser.add_subparsers(dest='command', required=True)
    preflight = subparsers.add_parser(
        'preflight', help='verify GUI plan/receipt consistency and write the private gate plan')
    preflight.add_argument('--gui-plan', type=Path, required=True,
                           help='private plan JSON produced by pb_gui_bridge.py')
    preflight.add_argument('--backend-receipt', type=Path, required=True,
                           help='success receipt JSON produced by pb_backend_apply.py apply')
    preflight.add_argument('--output', type=Path, required=True, help='write the private gate plan here')
    validate = subparsers.add_parser(
        'validate', help='re-check a stored gate plan; with both inputs also re-verify its hashes')
    validate.add_argument('--gate-plan', type=Path, required=True, help='gate plan JSON to verify')
    validate.add_argument('--gui-plan', type=Path,
                          help='original GUI plan to re-hash and re-bind against')
    validate.add_argument('--backend-receipt', type=Path,
                          help='original backend receipt to re-hash and re-bind against')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'preflight':
            return command_preflight(args)
        return command_validate(args)
    except ValueError as error:
        print(f'pb_gui_capture_gate.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
