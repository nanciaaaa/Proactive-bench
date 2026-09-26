#!/usr/bin/env python3
"""Apply one private PB backend plan to a device through the existing GUI adapters.

Round 2 -> 3 execution wiring of the PB GUI alignment blueprint. The plan-only
artifact produced by ``pb_backend_plan.py`` is consumed here; this is the only
script that turns the plan's Calendar/Mail/Clock projections into device writes,
and it does so strictly through the existing native adapter functions:

* ``gui_calendar.py``: ``seed_event`` / ``read_event`` / ``apply_history_change`` /
  ``assert_absent``
* ``gui_mail.py``: ``seed_mail`` / ``read_mail`` / ``apply_history_change`` /
  ``assert_absent``
* ``gui_clock.py``: ``seed_alarm`` / ``read_alarm`` / ``assert_absent``
* ``gui_contacts.py``: ``seed_contact`` / ``read_contact`` / ``assert_absent``

Contacts has no executable history function; a contacts record that declares
history changes is refused instead of being silently replayed or ignored.

Contract:

* ``preflight`` never opens adb and never writes a device. It validates that the
  plan is ``status=planned`` with ``device_applied=false``, that every source has
  a supported adapter contract, that every seed/readback projection is
  executable, and prints exactly the items ``apply`` would run.
* ``apply`` refuses to run without ``--serial`` and a usable adb binary (and an
  online device). It runs the reused ``assert_absent`` residue guard for every
  planned record first, then seeds every record in Calendar -> Mail -> Clock ->
  Contacts order, replays each record's declared history after its seed and before its
  readback -- Mail ``unread -> read`` through ``gui_mail.apply_history_change``
  and the Calendar ``calendar_reminder_minutes`` + ``updated_at`` update through
  ``gui_calendar.apply_history_change``, in nondecreasing ``effective_at``
  order -- and reads each record back with the matching ``read_*`` function. A
  Calendar record may only declare that reminder update, its decision-point
  readback must already carry the final replayed reminder, and a readback that
  changed without a declared replay is refused, so the device state cannot
  silently drift from the plan. Any failure stops immediately with a non-zero
  exit; no receipt is written.
* A plan whose ``world_status`` ends in ``not_applied_to_android`` is refused
  unless ``--allow-device-apply`` explicitly acknowledges the unapplied PB
  bundle. The receipt always keeps the original world status; the flag never
  rewrites it. A plan that already claims ``device_applied=true`` is always
  refused: a completion claim is not re-applied by this script.
* The success receipt binds the plan schema, plan digest, ``episode_scope``,
  ``world_hash`` and ``world_status``, every record's ``semantic_ref``, the
  adapter module/function names, the seed and readback results, the per-record
  history replays (``function`` / ``semantic_ref`` / ``effective_at`` / ``set``
  / ``result``) and the device serial. ``device_applied=true`` only appears
  after every seed and readback succeeded, and PB GT/Need/Oracle/private fields
  or local absolute paths never enter the preflight summary or the receipt.

Usage::

    python3 scripts/pb_backend_apply.py preflight --plan plan.json [--pb-root PB]
    python3 scripts/pb_backend_apply.py apply --plan plan.json --serial emulator-5554 \
        --device-timezone Asia/Shanghai --receipt receipt.json [--allow-device-apply]
"""
import argparse
import copy
import importlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pb_backend_plan as pb

RECEIPT_SCHEMA = 'pb-backend-apply-receipt.v1'
PREFLIGHT_SCHEMA = 'pb-backend-preflight.v1'
ADB_TIMEOUT_SECONDS = 60
ABSENT_FUNCTION = 'assert_absent'
UNAPPLIED_SUFFIX = 'not_applied_to_android'
MAIL_HISTORY_FUNCTION = 'apply_history_change'
MAIL_HISTORY_FIELD = 'status'
MAIL_HISTORY_SOURCE_STATUS = 'unread'
MAIL_HISTORY_TARGET_STATUS = 'read'
CALENDAR_HISTORY_FUNCTION = 'apply_history_change'
CALENDAR_REMINDER_FIELD = 'calendar_reminder_minutes'
CALENDAR_UPDATED_FIELD = 'updated_at'
CALENDAR_HISTORY_FIELDS = (CALENDAR_REMINDER_FIELD, CALENDAR_UPDATED_FIELD)
CALENDAR_REMINDER_COUNT = 3
HISTORY_FUNCTIONS = {'calendar': CALENDAR_HISTORY_FUNCTION, 'mail': MAIL_HISTORY_FUNCTION}


def adapter_contract(source):
    entry = pb.ADAPTERS[source]
    return {'module': entry['module'],
            'module_name': Path(entry['module']).stem,
            'seed': entry['seed'],
            'read': entry['read'],
            'absent': ABSENT_FUNCTION}


def ordered_sources(plan):
    sources = plan.get('sources')
    if not isinstance(sources, dict) or not sources:
        raise ValueError('plan has no supported sources to execute')
    unexpected = sorted(set(sources) - set(pb.SUPPORTED_SOURCES))
    if unexpected:
        raise ValueError(f'plan maps sources without an existing adapter: {unexpected}')
    ordering = [source for source in pb.SUPPORTED_SOURCES if source in sources]
    for source in ordering:
        entry = sources[source]
        if not isinstance(entry, dict) or entry.get('status') != 'supported':
            raise ValueError(f'plan source {source!r} is not marked supported')
        adapter = entry.get('adapter') or {}
        contract = adapter_contract(source)
        for key in ('module', 'seed', 'read'):
            if adapter.get(key) != contract[key]:
                raise ValueError(f'plan source {source!r} adapter {key!r} does not match the known '
                                 f'{contract[key]!r} contract')
    return ordering


def record_projection(record, ref):
    for label in ('seed', 'readback'):
        section = record.get(label)
        if not isinstance(section, dict) or not isinstance(section.get('data'), dict):
            raise ValueError(f'{ref} needs a {label}.data object')
        at = section.get('at')
        if not isinstance(at, str) or not at.strip() or pb.maybe_timestamp(at) is None:
            raise ValueError(f'{ref} {label}.at must be an ISO-8601 timestamp with a UTC offset')
    readback = record['readback']
    fields = readback.get('fields')
    if not isinstance(fields, list) or not fields:
        raise ValueError(f'{ref} needs a nonempty readback.fields list')
    return (record['seed']['at'], record['readback']['at'],
            copy.deepcopy(record['seed']['data']), copy.deepcopy(readback['data']), list(fields))


def execution_items(plan):
    items = []
    for source in ordered_sources(plan):
        records = plan['sources'][source].get('seed_records')
        if not isinstance(records, list):
            raise ValueError(f'plan source {source!r} seed_records must be a list')
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f'{source} seed record must be an object')
            ref = record.get('semantic_ref')
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError(f'{source} seed record needs a nonempty semantic_ref')
            seed_at, readback_at, seed_data, readback_data, fields = record_projection(record, ref)
            history_changes = record.get('history_changes', [])
            if not isinstance(history_changes, list):
                raise ValueError(f'{ref} history_changes must be a list')
            item = {'order': len(items) + 1, 'source': source, 'semantic_ref': ref,
                    'contract': adapter_contract(source),
                    'seed_at': seed_at, 'readback_at': readback_at,
                    'seed_data': seed_data, 'readback_data': readback_data,
                    'readback_fields': fields,
                    'history_changes': copy.deepcopy(history_changes)}
            item['history_replays'] = declared_history_replays(item)
            items.append(item)
    if not items:
        raise ValueError('plan has no executable seed/readback records')
    return items


def history_effective_at(change, ref, index):
    label = f'{ref} history_changes[{index}]'
    if not isinstance(change, dict):
        raise ValueError(f'{label} must be an object')
    effective_at = change.get('effective_at')
    if not isinstance(effective_at, str) or pb.maybe_timestamp(effective_at) is None:
        raise ValueError(f'{label} effective_at must be an ISO-8601 timestamp with a UTC offset')
    return effective_at


def ordered_replays(replays):
    """Sort one record's validated replays by effective_at (declared order wins ties)."""
    return sorted(replays, key=lambda replay: pb.maybe_timestamp(replay['effective_at']))


def check_effective_window(item, replays):
    """Refuse a declared replay that cannot sit between this record's seed and readback."""
    seed_at = pb.maybe_timestamp(item['seed_at'])
    readback_at = pb.maybe_timestamp(item['readback_at'])
    for replay in replays:
        effective = pb.maybe_timestamp(replay['effective_at'])
        if effective < seed_at or effective > readback_at:
            raise ValueError(f'{item["semantic_ref"]} history replay effective_at '
                             f'{replay["effective_at"]!r} is outside the seed/readback window '
                             f'{item["seed_at"]!r} .. {item["readback_at"]!r}')
    return replays


def mail_history_replays(item, changes):
    """Validate Mail's only replayable transition: ``status: unread -> read``."""
    ref = item['semantic_ref']
    seed_status = item['seed_data'].get(MAIL_HISTORY_FIELD)
    readback_status = item['readback_data'].get(MAIL_HISTORY_FIELD)
    if seed_status != MAIL_HISTORY_SOURCE_STATUS or readback_status != MAIL_HISTORY_TARGET_STATUS:
        raise ValueError(f'{ref} rejects the unknown history transition '
                         f'{seed_status!r} -> {readback_status!r}')
    replays = []
    for index, change in enumerate(changes):
        effective_at = history_effective_at(change, ref, index)
        updates = change.get('set')
        if not isinstance(updates, dict):
            raise ValueError(f'{ref} history_changes[{index}] needs a set object')
        if set(updates) != {MAIL_HISTORY_FIELD}:
            raise ValueError(f'{ref} history_changes[{index}] only allows the {MAIL_HISTORY_FIELD!r} '
                             f'field, got {sorted(updates)}')
        if updates[MAIL_HISTORY_FIELD] != MAIL_HISTORY_TARGET_STATUS:
            raise ValueError(f'{ref} history_changes[{index}] rejects the unknown history transition '
                             f'{seed_status!r} -> {updates[MAIL_HISTORY_FIELD]!r}')
        replays.append({'semantic_ref': ref, 'effective_at': effective_at,
                        'set': {MAIL_HISTORY_FIELD: MAIL_HISTORY_TARGET_STATUS},
                        'function': MAIL_HISTORY_FUNCTION})
    return ordered_replays(replays)


def calendar_history_replays(item, changes):
    """Validate the replayable Calendar reminder update and its readback alignment.

    ``gui_calendar.apply_history_change`` can only write the declared
    three-element integer ``calendar_reminder_minutes`` list plus the matching
    ``updated_at`` instant, so any other field, wrong type or instant drift is
    refused before the device is touched. Declaring a reminder update without a
    decision-point readback that carries the final value -- or changing the
    readback reminder without declaring an update -- is refused too, because the
    device could not reproduce the state the plan claims to read back.
    """
    ref = item['semantic_ref']
    replays = []
    for index, change in enumerate(changes):
        label = f'{ref} history_changes[{index}]'
        effective_at = history_effective_at(change, ref, index)
        updates = change.get('set')
        if not isinstance(updates, dict):
            raise ValueError(f'{label} needs a set object')
        unknown = sorted(set(updates) - set(CALENDAR_HISTORY_FIELDS))
        if unknown:
            raise ValueError(f'{label} rejects unknown Calendar history fields {unknown}; only '
                             f'{sorted(CALENDAR_HISTORY_FIELDS)} can be replayed')
        missing = sorted(set(CALENDAR_HISTORY_FIELDS) - set(updates))
        if missing:
            raise ValueError(f'{label} misses the declared Calendar history fields {missing}')
        minutes = updates[CALENDAR_REMINDER_FIELD]
        if not isinstance(minutes, list) or len(minutes) != CALENDAR_REMINDER_COUNT or any(
                not isinstance(value, int) or isinstance(value, bool) for value in minutes):
            raise ValueError(f'{label} calendar_reminder_minutes must be a three-element integer '
                             f'list, got {minutes!r}')
        updated_at = updates[CALENDAR_UPDATED_FIELD]
        if not isinstance(updated_at, str) or pb.maybe_timestamp(updated_at) is None:
            raise ValueError(f'{label} updated_at must be an ISO-8601 timestamp with a UTC offset')
        if pb.maybe_timestamp(updated_at) != pb.maybe_timestamp(effective_at):
            raise ValueError(f'{label} updated_at {updated_at!r} does not match its effective_at '
                             f'{effective_at!r}')
        replays.append({'semantic_ref': ref, 'effective_at': effective_at,
                        'set': {CALENDAR_REMINDER_FIELD: list(minutes),
                                CALENDAR_UPDATED_FIELD: updated_at},
                        'function': CALENDAR_HISTORY_FUNCTION})
    return ordered_replays(replays)


def check_calendar_readback(item, replays):
    """Bind the declared Calendar replays to the decision-point readback reminder.

    A declared replay must end on the reminder the readback claims, and a
    readback that differs from the seeded reminder without a declared replay is
    refused too: the device could not reproduce the state the plan would read.
    """
    ref = item['semantic_ref']
    seed_reminder = item['seed_data'].get('reminder_minutes_before')
    readback_reminder = item['readback_data'].get('reminder_minutes_before')
    if replays:
        final = replays[-1]['set'][CALENDAR_REMINDER_FIELD][0]
        if readback_reminder != final:
            raise ValueError(
                f'{ref} history change declares calendar_reminder_minutes '
                f'{replays[-1]["set"][CALENDAR_REMINDER_FIELD]!r} '
                f'but its decision-point readback reminder_minutes_before is {readback_reminder!r}; a '
                f'changed Calendar readback is required')
    elif readback_reminder != seed_reminder:
        raise ValueError(f'{ref} readback reminder_minutes_before {readback_reminder!r} differs from '
                         f'the seeded {seed_reminder!r} without a declared history change; no device '
                         f'replay could reproduce it')
    return replays


def declared_history_replays(item):
    """Validate one record's declared history changes against its replayable contract.

    Mail keeps its ``status: unread -> read`` transition and Calendar accepts
    only ``calendar_reminder_minutes`` + ``updated_at``. Contacts has no history
    function, so any contacts change is refused. An unknown source, field, type
    or instant fails closed before any history is replayed on the device.
    """
    changes = item['history_changes']
    source = item['source']
    if source == 'mail':
        replays = mail_history_replays(item, changes) if changes else []
    elif source == 'calendar':
        replays = calendar_history_replays(item, changes) if changes else []
        check_calendar_readback(item, replays)
    elif source == 'contacts':
        if changes:
            raise ValueError(f'{item["semantic_ref"]} declares contacts history changes, but the '
                             f'contacts adapter has no history function; refusing to ignore them')
        return []
    elif not changes:
        return []
    else:
        raise ValueError(f'{item["semantic_ref"]} declares history changes for unknown source '
                         f'{source!r}')
    return check_effective_window(item, replays)


def validate_executable_plan(plan):
    if plan.get('status') != 'planned':
        raise ValueError(f"plan status must be 'planned' to execute, got {plan.get('status')!r}")
    if plan.get('device_applied') is not False:
        raise ValueError('plan claims device_applied is not false; refusing a device completion claim')
    scope = plan.get('episode_scope')
    if not isinstance(scope, dict):
        raise ValueError('plan episode_scope must be an object')
    if scope.get('device_applied') is not False:
        raise ValueError('plan episode_scope claims device_applied is not false; refusing a completion claim')
    world = plan.get('world')
    if not isinstance(world, dict):
        raise ValueError('plan world must be an object')
    world_status = scope.get('world_status')
    if not isinstance(world_status, str) or not world_status.strip():
        raise ValueError('plan episode_scope.world_status must be a nonempty string')
    if world_status != world.get('world_status'):
        raise ValueError('plan world_status is inconsistent between episode_scope and world')
    world_hash = world.get('world_hash')
    if not isinstance(world_hash, str) or not world_hash.strip():
        raise ValueError('plan world_hash must be a nonempty string')
    if scope.get('world_hash') != world_hash:
        raise ValueError('plan world_hash is inconsistent between episode_scope and world')
    return execution_items(plan)


def requires_device_apply_ack(plan):
    status = (plan.get('episode_scope') or {}).get('world_status')
    return isinstance(status, str) and status.endswith(UNAPPLIED_SUFFIX)


def check_world_gate(plan, allow_device_apply):
    if requires_device_apply_ack(plan) and not allow_device_apply:
        status = plan['episode_scope']['world_status']
        raise ValueError(f'plan world_status is {status!r}; pass --allow-device-apply to acknowledge the '
                         f'unapplied PB bundle (the receipt keeps this status unchanged)')


def load_plan(plan_path, pb_root=None):
    path = Path(plan_path).expanduser().resolve()
    plan = pb.load_json(path, 'plan')
    if not isinstance(plan, dict):
        raise ValueError('plan must be a JSON object')
    root = None if pb_root is None else Path(pb_root).expanduser()
    pb.validate_plan(plan, root)
    return plan, path


def resolve_adapter_functions(source):
    contract = adapter_contract(source)
    try:
        module = importlib.import_module(contract['module_name'])
    except ImportError as error:
        raise ValueError(f'cannot import the {source} adapter module {contract["module"]!r}: {error}') from None
    functions = {'contract': contract, 'module': module}
    for key in ('seed', 'read', 'absent'):
        name = contract[key]
        function = getattr(module, name, None)
        if not callable(function):
            raise ValueError(f'{contract["module"]} does not expose callable {name!r}')
        functions[key] = function
    history_name = HISTORY_FUNCTIONS.get(source)
    if history_name:
        function = getattr(module, history_name, None)
        if not callable(function):
            raise ValueError(f'{contract["module"]} does not expose callable {history_name!r}')
        functions['history'] = function
    return functions


def build_adb_runner(serial, adb_binary='adb'):
    binary = (adb_binary or 'adb').strip()
    if not binary:
        raise ValueError('--adb must be a nonempty adb binary name or path')
    path = shutil.which(binary)
    if not path:
        raise ValueError(f'adb binary {binary!r} is not available on PATH; refusing to apply without adb')

    def run(device_serial, *args, timeout=ADB_TIMEOUT_SECONDS):
        if device_serial != serial:
            raise RuntimeError(f'adb wrapper is bound to serial {serial!r}, got {device_serial!r}')
        command = [path, '-s', serial, *[str(arg) for arg in args]]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f'adb invocation failed: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).decode(errors='replace')[:300]
            raise RuntimeError(f'adb {" ".join(str(arg) for arg in args[:3])} failed '
                               f'({result.returncode}): {detail}')
        return result.stdout

    return run


def ensure_device(adb, serial):
    state = adb(serial, 'get-state')
    text = state.decode(errors='replace').strip() if isinstance(state, bytes) else str(state).strip()
    if text != 'device':
        raise RuntimeError(f'adb device {serial!r} is not online (get-state={text!r}); refusing to apply')


def json_safe(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode(errors='replace')
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def prepare_receipt_path(receipt_path):
    target = Path(receipt_path).expanduser().resolve()
    if target.is_dir():
        raise ValueError(f'--receipt is a directory: {target}')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f'cannot prepare --receipt {target}: {error}') from None
    return target


def clear_stale_receipt(target):
    """Drop a previous success receipt before touching the device.

    A failed apply writes no receipt, so an old success file must not survive a
    device-reachable attempt and be mistaken for this run's result.
    """
    try:
        if target.exists():
            target.unlink()
    except OSError as error:
        raise ValueError(f'cannot clear stale --receipt {target}: {error}') from None


def assert_no_private_leak(payload, label):
    """Refuse to emit any payload that carries PB GT material or a local absolute path."""
    leaks = pb.private_key_paths(payload)
    if leaks:
        raise RuntimeError(f'private GT material would leak into the {label}: {leaks}')
    absolutes = pb.absolute_value_paths(payload)
    if absolutes:
        raise RuntimeError(f'local absolute paths would leak into the {label}: {absolutes}')
    return payload


def build_receipt(plan, plan_path, serial, device_timezone, adb_binary, allow_device_apply, records):
    scope = copy.deepcopy(plan['episode_scope'])
    receipt = {
        'schema_version': RECEIPT_SCHEMA,
        'status': 'ok',
        'command': 'apply',
        'device_applied': True,
        'allow_device_apply': bool(allow_device_apply),
        'applied_at': datetime.now(timezone.utc).isoformat(),
        'plan_binding': {
            'schema_version': plan.get('schema_version'),
            'plan_sha256': pb.digest(plan_path),
            'status': plan.get('status'),
            'device_applied': plan.get('device_applied'),
            'episode_scope': scope,
            'world_status': scope.get('world_status'),
            'world_hash': scope.get('world_hash'),
        },
        'device': {'serial': serial, 'timezone': device_timezone, 'adb': (adb_binary or 'adb').strip()},
        'ordering': [source for source in pb.SUPPORTED_SOURCES if source in plan.get('sources', {})],
        'records': records,
        'checks': {'seed_records': len(records), 'seeds': len(records), 'readbacks': len(records),
                   'absence_guards': len({record['source'] for record in records}), 'failures': 0},
    }
    return assert_no_private_leak(receipt, 'receipt')


def execute_plan(plan, plan_path, items, adb, serial, device_timezone, adb_binary, allow_device_apply):
    scope = plan['episode_scope']
    episode = scope['episode_id']
    ordering = [source for source in pb.SUPPORTED_SOURCES if source in plan.get('sources', {})]
    functions = {}
    for source in ordering:
        if any(item['source'] == source for item in items):
            functions[source] = resolve_adapter_functions(source)

    for source in ordering:
        if source not in functions:
            continue
        records = [{'semantic_ref': item['semantic_ref'], 'record_ref': item['semantic_ref'],
                    'data': item['seed_data']} for item in items if item['source'] == source]
        try:
            if source in ('mail', 'contacts'):
                functions[source]['absent'](adb, serial, records)
            else:
                functions[source]['absent'](adb, serial, episode, records)
        except Exception as error:
            raise RuntimeError(f'apply stopped at {source} residue check: {error}') from error

    results = []
    for item in items:
        source, ref = item['source'], item['semantic_ref']
        functions_source = functions[source]
        seed_record = {'semantic_ref': ref, 'record_ref': ref, 'data': item['seed_data']}
        readback_record = {'semantic_ref': ref, 'record_ref': ref, 'data': item['readback_data']}
        try:
            if source == 'calendar':
                seed_result = functions_source['seed'](adb, serial, episode, seed_record,
                                                       device_timezone, item['seed_at'])
            elif source in ('mail', 'contacts'):
                seed_result = functions_source['seed'](adb, serial, seed_record)
            else:
                seed_result = functions_source['seed'](adb, serial, episode, seed_record)
        except Exception as error:
            raise RuntimeError(f'apply stopped at {source} seed {ref!r}: {error}') from error
        replays = []
        try:
            for replay in item['history_replays']:
                if source == 'calendar':
                    change = {'semantic_ref': ref, 'entity_type': 'events', 'data': replay['set']}
                    result = functions_source['history'](adb, serial, episode, change,
                                                         replay['effective_at'])
                else:
                    result = functions_source['history'](adb, serial, readback_record)
                replays.append({'function': replay['function'], 'semantic_ref': ref,
                                'effective_at': replay['effective_at'], 'set': replay['set'],
                                'result': json_safe(result)})
        except Exception as error:
            raise RuntimeError(f'apply stopped at {source} history replay {ref!r}: {error}') from error
        try:
            if source == 'calendar':
                readback_result = functions_source['read'](adb, serial, episode, readback_record,
                                                           device_timezone)
            elif source in ('mail', 'contacts'):
                readback_result = functions_source['read'](adb, serial, readback_record)
            else:
                readback_result = functions_source['read'](adb, serial, episode, readback_record)
        except Exception as error:
            raise RuntimeError(f'apply stopped at {source} readback {ref!r}: {error}') from error
        results.append({
            'order': item['order'],
            'source': source,
            'semantic_ref': ref,
            'adapter': {'module': item['contract']['module'], 'seed': item['contract']['seed'],
                        'read': item['contract']['read']},
            'seed': {'at': item['seed_at'], 'function': item['contract']['seed'],
                     'result': json_safe(seed_result)},
            'readback': {'at': item['readback_at'], 'function': item['contract']['read'],
                         'result': json_safe(readback_result)},
            'history_replays': replays,
            'status': 'ok',
        })
    return build_receipt(plan, plan_path, serial, device_timezone, adb_binary, allow_device_apply, results)


def preflight_summary(plan, allow_device_apply=False):
    items = validate_executable_plan(plan)
    scope = plan['episode_scope']
    blockers = []
    if requires_device_apply_ack(plan) and not allow_device_apply:
        blockers.append(f'world_status={scope.get("world_status")} requires --allow-device-apply; '
                        f'the receipt keeps the status unchanged')
    unsupported = [{'source': entry.get('source'), 'kind': entry.get('kind'),
                    'reason': entry.get('reason'), 'count': entry.get('count', 0)}
                   for entry in plan.get('unsupported', [])]
    by_source = {source: sum(1 for item in items if item['source'] == source)
                 for source in ordered_sources(plan)}
    summary = {
        'schema_version': PREFLIGHT_SCHEMA,
        'status': 'ok',
        'command': 'preflight',
        'device_write': False,
        'adb_required': False,
        'device_apply_ready': not blockers,
        'blockers': blockers,
        'plan': {'schema_version': plan.get('schema_version'), 'status': plan.get('status'),
                 'device_applied': plan.get('device_applied'),
                 'episode_scope': copy.deepcopy(scope),
                 'world_status': scope.get('world_status'), 'world_hash': plan['world'].get('world_hash')},
        'ordering': ordered_sources(plan),
        'counts': {'seed_records': len(items), 'readbacks': len(items), 'by_source': by_source},
        'items': [{'order': item['order'], 'source': item['source'], 'semantic_ref': item['semantic_ref'],
                   'adapter_module': item['contract']['module'],
                   'seed_function': item['contract']['seed'],
                   'readback_function': item['contract']['read'],
                   'seed_at': item['seed_at'], 'readback_at': item['readback_at'],
                   'seed_data': item['seed_data'], 'readback_data': item['readback_data'],
                   'readback_fields': item['readback_fields'],
                   'history_replays': [{'function': replay['function'],
                                        'semantic_ref': replay['semantic_ref'],
                                        'effective_at': replay['effective_at'],
                                        'set': replay['set']}
                                       for replay in item['history_replays']]} for item in items],
        'unsupported_not_executed': unsupported,
    }
    return assert_no_private_leak(summary, 'preflight summary')


def command_preflight(args):
    plan, _ = load_plan(args.plan, args.pb_root)
    print(json.dumps(preflight_summary(plan, args.allow_device_apply), ensure_ascii=False))
    return 0


def command_apply(args, adb_runner=None):
    plan, plan_path = load_plan(args.plan, args.pb_root)
    items = validate_executable_plan(plan)
    check_world_gate(plan, args.allow_device_apply)
    serial = (args.serial or '').strip()
    if not serial:
        raise ValueError('apply requires a nonempty --serial')
    device_timezone = (args.device_timezone or '').strip()
    if not device_timezone:
        raise ValueError('apply requires --device-timezone')
    plan_timezone = plan['episode_scope'].get('timezone')
    if device_timezone != plan_timezone:
        raise ValueError(f'--device-timezone {device_timezone!r} does not match plan timezone '
                         f'{plan_timezone!r}')
    receipt_path = prepare_receipt_path(args.receipt)
    adb_binary = (args.adb or 'adb').strip()
    if adb_runner is None:
        adb = build_adb_runner(serial, adb_binary)
        ensure_device(adb, serial)
    elif not callable(adb_runner):
        raise ValueError('adb_runner must be callable')
    else:
        adb = adb_runner
    clear_stale_receipt(receipt_path)
    receipt = execute_plan(plan, plan_path, items, adb, serial, device_timezone, adb_binary,
                           args.allow_device_apply)
    pb.write_json(receipt_path, receipt)
    print(json.dumps({'status': 'ok', 'command': 'apply', 'device_applied': True, 'serial': serial,
                      'ordering': receipt['ordering'], 'records': receipt['checks']['seed_records'],
                      'receipt': str(receipt_path)}, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_backend_apply.py',
        description='Preflight or apply one PB offline backend plan through the existing '
                    'Calendar/Mail/Clock adapters (fail closed).')
    subparsers = parser.add_subparsers(dest='command', required=True)
    preflight = subparsers.add_parser(
        'preflight', help='validate a planned backend plan and list the seed/readback items; '
                          'never touches adb or a device')
    preflight.add_argument('--plan', type=Path, required=True, help='plan JSON produced by pb_backend_plan.py')
    preflight.add_argument('--pb-root', type=Path, help='verify plan reference hashes against this PB root')
    preflight.add_argument('--allow-device-apply', action='store_true',
                           help='report an unapplied world status as acknowledged instead of a blocker')
    apply_parser = subparsers.add_parser(
        'apply', help='seed and read back one planned backend plan, in Calendar/Mail/Clock order')
    apply_parser.add_argument('--plan', type=Path, required=True,
                              help='plan JSON produced by pb_backend_plan.py')
    apply_parser.add_argument('--pb-root', type=Path, help='verify plan reference hashes against this PB root')
    apply_parser.add_argument('--serial', required=True, help='adb device serial (required)')
    apply_parser.add_argument('--device-timezone', required=True,
                              help='device timezone, must match the plan timezone')
    apply_parser.add_argument('--receipt', type=Path, required=True,
                              help='write the success receipt JSON to this path')
    apply_parser.add_argument('--allow-device-apply', action='store_true',
                              help='acknowledge a world_status ending in not_applied_to_android; '
                                   'the receipt keeps the status unchanged')
    apply_parser.add_argument('--adb', default='adb', help='adb binary name or path (default: adb)')
    return parser


def main(argv=None, adb_runner=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'preflight':
            return command_preflight(args)
        return command_apply(args, adb_runner=adb_runner)
    except (ValueError, RuntimeError) as error:
        print(f'pb_backend_apply.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
