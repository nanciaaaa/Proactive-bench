#!/usr/bin/env python3
"""Compile one ProactiveBench episode into a private offline backend plan.

Round 2 of the PB GUI alignment blueprint: read a single episode's PB
``benchmark.json`` registration, cluster manifest, episode manifest,
initialization payload, history and compatibility/bindings, then emit a
plan-only artifact for the existing Calendar / Mail / Clock native adapters
(``gui_calendar.py`` / ``gui_mail.py`` / ``gui_clock.py``).

The plan is offline and fail-closed:

* PB assets are the only truth; every ``*_ref`` path must resolve inside
  ``--pb-root`` and exist (legacy ``adapter_ref`` / ``historical_*`` refs are
  reported as out-of-bundle instead of resolved);
* the manifest must agree with the cluster, with the compatibility pair and
  with the bound initialization payload (world variant, target time equal to
  the decision point, timezone, world hash);
* only ``calendar`` / ``mail`` / ``clock`` are mapped to existing adapters;
  every other accessible world source is emitted as ``unsupported`` with a
  reason;
* history ``initial_overrides`` and ``events`` stay per ``entity_ref`` and the
  emitted events are sorted by ``effective_at``; device readback is planned at
  the decision point;
* private GT / need / evidence / oracle / public_rule / expected_need_set
  material is never read and never emitted; no local absolute path or private
  screenshot path can enter the output;
* an initialization status such as ``new_bundle_not_applied_to_android`` can
  only yield ``status=planned`` / ``status=unsupported``; a plan that claims
  device completion is rejected by ``validate``.

Usage::

    python3 scripts/pb_backend_plan.py plan --pb-root PB --episode-id ep_0004 \
        --output plan.json
    python3 scripts/pb_backend_plan.py validate --pb-root PB --episode-id ep_0004
    python3 scripts/pb_backend_plan.py validate --plan plan.json [--pb-root PB]
"""
import argparse
import copy
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

PLAN_SCHEMA = 'pb-backend-plan.v1'
COMMANDS = ('plan', 'validate')
SUPPORTED_SOURCES = ('calendar', 'mail', 'clock', 'contacts')
PAIR_STATUS_ALLOWED = ('compatible', 'development_only')
COMPLETION_STATUSES = frozenset({'applied', 'device_completed', 'completed', 'verified', 'gold'})
UNAPPLIED_STATUS = 'new_bundle_not_applied_to_android'
LEGACY_REF_KEYS = frozenset({'adapter_ref', 'historical_evidence_ref'})
NON_EMITTED_REF_KEYS = frozenset({'gt_ref', 'private_gt_ref', 'ground_truth_ref', 'label_ref',
                                  'answer_ref', 'need_rule_ref', 'access_ref', 'baseline_access_ref'})
PRIVATE_KEY_NAMES = frozenset(
    re.sub(r'[^a-z0-9]', '', name) for name in (
        'gt', 'gold', 'answer', 'oracle', 'private_gt', 'ground_truth',
        'need', 'need_set', 'expected_need', 'expected_need_set',
        'evidence', 'evidence_set', 'evidence_sets',
        'passive_class', 'public_rule', 'support', 'contradict'))
ABSOLUTE_VALUE = re.compile(r'^(?:/|[A-Za-z]:[\\/]|\\\\)|(?:^|[\\/])(?:home|Users|root|tmp)[\\/]')
MAIL_DATE_PATTERN = '%b %d, %Y %H:%M'

ADAPTERS = {
    'calendar': {'module': 'scripts/gui_calendar.py', 'seed': 'seed_event', 'read': 'read_event'},
    'mail': {'module': 'scripts/gui_mail.py', 'seed': 'seed_mail', 'read': 'read_mail'},
    'clock': {'module': 'scripts/gui_clock.py', 'seed': 'seed_alarm', 'read': 'read_alarm'},
    'contacts': {'module': 'scripts/gui_contacts.py', 'seed': 'seed_contact', 'read': 'read_contact'},
}
FIELD_MAP = {
    'calendar': {'title': 'title', 'start_at': 'start_at', 'end_at': 'end_at',
                 'location': 'location', 'description': 'description',
                 'reminder_minutes_before': 'calendar_reminder_minutes[0]',
                 'source': 'literal:calendar'},
    'mail': {'headers.subject': 'subject', 'headers.date': 'sent_at',
             'headers.from': 'from_name', 'headers.to': 'to',
             'headers.sender': 'sender', 'headers.senderLogo': 'literal:""',
             'body': 'body', 'status': 'status', 'attachments': 'attachments'},
    'clock': {'label': 'label', 'time': 'hour/minute as HH:MM', 'enabled': 'enabled'},
    'contacts': {'name': 'name', 'phone_v2': 'phone', 'email_v2': 'email'},
}
READBACK_FIELDS = {
    'calendar': ['import_id', 'title', 'start_ts', 'end_ts', 'location', 'description',
                 'reminder_1_minutes', 'source', 'time_zone'],
    'mail': ['headers.subject', 'headers.date', 'headers.from', 'headers.to', 'headers.sender',
             'body', 'status', 'attachments'],
    'clock': ['external_uuid', 'hour', 'minutes', 'enabled', 'label', 'daysofweek'],
    'contacts': ['name', 'phone', 'email'],
}
BACKGROUND_READBACK_FIELDS = {
    'calendar': ['event_id', 'title', 'start_at', 'end_at', 'timezone'],
    'mail': ['headers.subject', 'headers.date', 'headers.from', 'body'],
    'clock': ['label', 'hour', 'minute', 'enabled'],
    'contacts': ['display_name', 'phone', 'email'],
}
CONTACTS_MIMETYPES = frozenset({'vnd.android.cursor.item/name',
                                'vnd.android.cursor.item/phone_v2',
                                'vnd.android.cursor.item/email_v2'})
CONTACTS_UNSUPPORTED_REASON = (
    'contacts adapter verifies exactly one name, one phone_v2 and at most one email_v2 data row; '
    'records with extra Android data rows, duplicate emails, a missing name/phone, or declared '
    'history changes are neither seeded nor read back')
SOURCE_REASONS = {
    'device': 'device time configuration is initialization/verifier scope; it is not an app record adapter',
    'settings': 'settings/timezone is environment scope and stays untouched by app adapters in this round',
}
DEFAULT_UNSUPPORTED_REASON = (
    'unsupported in this round: only calendar/mail/clock/contacts have explicit PB field mappings '
    'to the existing extension adapters; this source is not seeded and not read back')


class ContactsUnsupported(ValueError):
    """A contacts record the existing seed/read/absent adapter cannot verify."""


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def load_json(path, label):
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'cannot read {label} {path}: {error}') from None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label} is not an ISO-8601 timestamp: {value!r}') from None
    if parsed.utcoffset() is None:
        raise ValueError(f'{label} must carry a UTC offset: {value!r}')
    return parsed


def maybe_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def walk_keys(value, trail=()):
    if isinstance(value, dict):
        for key, item in value.items():
            here = trail + (str(key),)
            yield '/'.join(here), key
            yield from walk_keys(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_keys(item, trail + (str(index),))


def walk_refs(value, trail=()):
    if isinstance(value, dict):
        for key, item in value.items():
            here = trail + (str(key),)
            if isinstance(key, str) and key.endswith('_ref') and isinstance(item, str):
                yield '/'.join(here), key, item
            else:
                yield from walk_refs(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_refs(item, trail + (str(index),))


def private_key_paths(value, trail=()):
    found = []
    for path, key in walk_keys(value, trail):
        if normalize(key) in PRIVATE_KEY_NAMES:
            found.append(path)
    return found


def absolute_value_paths(value, trail=()):
    found = []
    for path, key in walk_keys(value, trail):
        item = value
        for step in path.split('/'):
            item = item[int(step)] if isinstance(item, list) else item[step]
        if isinstance(item, str) and ABSOLUTE_VALUE.search(item):
            found.append(path)
    return found


def resolve_inside(root, candidates, ref, label):
    inside = []
    for base_name, candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            continue
        inside.append((base_name, resolved))
        if resolved.exists():
            return resolved
    if inside:
        raise ValueError(f'{label} does not exist: {ref!r} '
                         f'(tried {", ".join(name for name, _ in inside)})')
    raise ValueError(f'{label} escapes --pb-root: {ref!r}')


def resolve_ref(root, base_dirs, ref, label):
    if ref.startswith('shared://'):
        target = root / 'shared' / ref[len('shared://'):]
        return resolve_inside(root, [('pb-root/shared', target)], ref, label)
    if '://' in ref:
        raise ValueError(f'{label} uses an unsupported ref scheme: {ref!r}')
    candidates = [(dirs_name, base / ref) for dirs_name, base in base_dirs]
    return resolve_inside(root, candidates, ref, label)


def sweep_document(root, base_dirs, document, label, checks):
    resolved = []
    for path, key, ref in walk_refs(document):
        if key in LEGACY_REF_KEYS or key.startswith('historical'):
            checks['out_of_bundle_refs'].append(
                {'document': label,
                 'kind': 'legacy_adapter' if key == 'adapter_ref' else 'historical_provenance',
                 'reason': 'legacy/historical reference outside the active PB slice; label only, not resolved'})
            continue
        if '://' in ref or '/' in ref:
            target = resolve_ref(root, base_dirs, ref, f'{label} {path}')
            checks['references_checked'] += 1
            checks['references_within_root_and_existing'] += 1
            resolved.append({'document': label, 'path': path, 'key': key, 'ref': ref, 'resolved': target})
        elif key in ('entity_ref', 'record_ref'):
            checks['domain_refs'].append({'document': label, 'path': path, 'ref': ref})
        else:
            checks['short_refs_not_paths'].append({'document': label, 'path': path, 'key': key, 'ref': ref})
    return resolved


def find_episode(root, benchmark, episode_id, cluster_filter):
    clusters = benchmark.get('clusters') if isinstance(benchmark, dict) else None
    if not isinstance(clusters, dict) or not clusters:
        raise ValueError('benchmark.json requires a nonempty clusters object')
    hits = []
    for cluster_id, entry in clusters.items():
        if cluster_filter and cluster_id != cluster_filter:
            continue
        rel = entry.get('path') if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not rel.strip():
            raise ValueError(f'clusters[{cluster_id!r}] requires a path string')
        cluster_dir = resolve_inside(root, [('pb-root', root / rel)], rel, f'clusters[{cluster_id!r}].path')
        if not cluster_dir.is_dir():
            raise ValueError(f'clusters[{cluster_id!r}].path is not a directory: {rel!r}')
        manifest_path = cluster_dir / 'episodes' / episode_id / 'manifest.json'
        if manifest_path.is_file():
            hits.append((cluster_id, cluster_dir, manifest_path))
    if not hits:
        raise ValueError(f'episode {episode_id!r} not found under --pb-root {root}')
    if len(hits) > 1:
        raise ValueError(f'episode {episode_id!r} is ambiguous across clusters '
                         f'{sorted(item[0] for item in hits)}; pass --cluster')
    cluster_id, cluster_dir, manifest_path = hits[0]
    cluster = load_json(cluster_dir / 'cluster.json', 'cluster')
    if cluster.get('cluster_id') != cluster_id:
        raise ValueError(f'cluster.json cluster_id {cluster.get("cluster_id")!r} '
                         f'does not match benchmark key {cluster_id!r}')
    if episode_id not in cluster.get('episode_ids', []):
        raise ValueError(f'cluster {cluster_id!r} does not register episode {episode_id!r}')
    manifest = load_json(manifest_path, 'manifest')
    if manifest.get('episode_id') != episode_id:
        raise ValueError(f'manifest episode_id {manifest.get("episode_id")!r} '
                         f'does not match directory {episode_id!r}')
    return cluster_id, cluster_dir, manifest_path, manifest, cluster


def entry_by_id(entries, key, value, label):
    if not isinstance(entries, list):
        raise ValueError(f'{label} must be a list')
    matched = [item for item in entries if isinstance(item, dict) and item.get(key) == value]
    if len(matched) != 1:
        raise ValueError(f'{label} needs exactly one entry with {key}={value!r}, found {len(matched)}')
    return matched[0]


def merge_patch(records, patch, label):
    if not isinstance(patch, dict) or not isinstance(patch.get('patches', []), list):
        raise ValueError(f'{label} requires a patches list')
    for op in patch['patches']:
        if not isinstance(op, dict):
            raise ValueError(f'{label} patch entries must be objects')
        ref, kind = op.get('entity_ref'), op.get('op')
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(f'{label} patch entry needs an entity_ref')
        if kind == 'add':
            if ref in records:
                raise ValueError(f'{label} adds an existing record {ref!r}')
            records[ref] = copy.deepcopy(op.get('record'))
        elif kind == 'update':
            if ref not in records:
                raise ValueError(f'{label} updates an unknown record {ref!r}')
            changes = op.get('set')
            if not isinstance(changes, dict) or not changes:
                raise ValueError(f'{label} update {ref!r} needs a nonempty set object')
            records[ref].setdefault('data', {}).update(copy.deepcopy(changes))
        elif kind == 'delete':
            if ref not in records:
                raise ValueError(f'{label} deletes an unknown record {ref!r}')
            del records[ref]
        else:
            raise ValueError(f'{label} patch entry {ref!r} has unsupported op {kind!r}')
    return records


def apply_changes(records, changes, label):
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise ValueError(f'{label}[{index}] must be an object')
        ref, values = change.get('entity_ref'), change.get('set')
        if ref not in records:
            raise ValueError(f'{label}[{index}] targets an unknown record {ref!r}')
        if not isinstance(values, dict) or not values:
            raise ValueError(f'{label}[{index}] needs a nonempty set object')
        records[ref].setdefault('data', {}).update(copy.deepcopy(values))
    return records


def apply_events(records, events, cutoff, label):
    applied = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f'{label} entries must be objects')
        effective = timestamp(event.get('effective_at'), f'{label} effective_at')
        if effective <= cutoff:
            apply_changes(records, event.get('updates', []), f'{label} updates')
            applied.append(event)
    return applied


def compose_world(baseline, cluster_patch, variant_patch):
    records = (baseline or {}).get('records')
    if not isinstance(records, dict) or not records:
        raise ValueError('shared baseline world_base.json requires a nonempty records object')
    composed = {ref: copy.deepcopy(record) for ref, record in records.items()}
    merge_patch(composed, cluster_patch, 'cluster_base.patch')
    merge_patch(composed, variant_patch, 'variant.patch')
    return composed


def payload_section(init_payload, source):
    """Return one payload source, accepting both flat payloads and ``payload.sources``."""
    entries = init_payload.get(source)
    if entries is None:
        nested = init_payload.get('sources')
        if isinstance(nested, dict):
            entries = nested.get(source)
    return entries


def payload_index(init_payload, source):
    entries = payload_section(init_payload, source)
    if source == 'calendar':
        items = entries if isinstance(entries, list) else []
        keyed = {item.get('event_id'): item for item in items if isinstance(item, dict)}
    elif source == 'mail':
        items = entries.get('mails') if isinstance(entries, dict) else None
        items = items if isinstance(items, list) else []
        keyed = {item.get('headers', {}).get('subject'): item for item in items if isinstance(item, dict)}
    elif source == 'contacts':
        items = entries if isinstance(entries, list) else []
        keyed = {item.get('record_id'): item for item in items if isinstance(item, dict)}
    else:
        items = entries if isinstance(entries, list) else []
        keyed = {item.get('alarm_id'): item for item in items if isinstance(item, dict)}
    if None in keyed or len(keyed) != len(items):
        raise ValueError(f'initialization payload {source} entries need unique identity fields')
    return keyed


def world_record_key(source, data):
    if source == 'calendar':
        return data.get('event_id')
    if source == 'mail':
        return data.get('subject')
    if source == 'contacts':
        return data.get('record_id')
    return data.get('alarm_id')


def values_equal(left, right):
    left_time, right_time = maybe_timestamp(left), maybe_timestamp(right)
    if left_time is not None and right_time is not None:
        return left_time == right_time
    return left == right


def verify_payload_record(source, ref, world_end, world_start, item, checks):
    data = world_end.get('data')
    if not isinstance(data, dict):
        raise ValueError(f'{ref} has no data object in the composed world')
    if source == 'calendar':
        fields = ('event_id', 'business_ref', 'title', 'start_at', 'end_at', 'timezone', 'location',
                  'description', 'calendar_reminder_minutes', 'status', 'updated_at')
    elif source == 'mail':
        fields = ('body', 'attachments', 'status')
    elif source == 'contacts':
        fields = ('record_id', 'title', 'details')
    else:
        fields = ('alarm_id', 'label', 'hour', 'minute', 'enabled', 'repeat_days', 'next_fire_at')
    for field in fields:
        if field not in data or field not in item:
            raise ValueError(f'{ref} initialization payload misses field {field!r}')
        if not values_equal(data[field], item[field]):
            raise ValueError(f'{ref} initialization payload {field!r} disagrees with history state: '
                             f'{item[field]!r} != {data[field]!r}')
    if source == 'mail':
        headers = item.get('headers')
        if not isinstance(headers, dict):
            raise ValueError(f'{ref} initialization payload mail entry needs headers')
        mapped = {'from': 'from_name', 'to': 'to', 'sender': 'sender', 'subject': 'subject'}
        for header, field in mapped.items():
            if not values_equal(headers.get(header), data.get(field)):
                raise ValueError(f'{ref} initialization payload headers.{header} disagrees '
                                 f'with world {field!r}')
        sent = timestamp(data.get('sent_at'), f'{ref} sent_at')
        display = headers.get('date')
        as_instant = maybe_timestamp(display)
        if as_instant is not None:
            if as_instant != sent:
                raise ValueError(f'{ref} initialization payload headers.date {display!r} '
                                 f'does not match sent_at')
        else:
            try:
                parsed = datetime.strptime(display, MAIL_DATE_PATTERN)
            except (TypeError, ValueError):
                raise ValueError(f'{ref} initialization payload headers.date is neither '
                                 f'timezone-aware ISO nor PB display format: {display!r}') from None
            if parsed != sent.astimezone(sent.tzinfo).replace(tzinfo=None):
                raise ValueError(f'{ref} initialization payload headers.date {display!r} '
                                 f'does not match sent_at')
        start_status, end_status = world_start.get('data', {}).get('status'), data.get('status')
        if item.get('status') != end_status or not isinstance(start_status, str):
            raise ValueError(f'{ref} initialization payload status disagrees with history status')
    checks['payload_state_records_verified'] += 1


def calendar_import_id(episode_id, semantic_ref):
    return 'gui-' + hashlib.sha256(f'{episode_id}:{semantic_ref}'.encode()).hexdigest()[:24]


def clock_external_uuid(episode_id, semantic_ref):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'gui-clock:{episode_id}:{semantic_ref}'))


def reminder_minutes(data, ref, field='calendar_reminder_minutes'):
    values = data.get(field)
    if not isinstance(values, list) or not values:
        raise ValueError(f'{ref} requires a nonempty {field} list')
    value = values[0]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'{ref} {field}[0] must be an integer, got {value!r}')
    return value


def build_calendar_seed(episode_id, ref, record, start_data, end_data, timezone, changes):
    for field in ('title', 'start_at', 'end_at', 'location', 'description'):
        if not isinstance(start_data.get(field), str):
            raise ValueError(f'{ref} requires string field {field!r}')
    start, end = (timestamp(start_data[field], f'{ref} {field}') for field in ('start_at', 'end_at'))
    if end < start:
        raise ValueError(f'{ref} end_at precedes start_at')
    source = record.get('source')
    if not isinstance(source, str):
        raise ValueError(f'{ref} requires a source string')
    seed_reminder = reminder_minutes(start_data, ref)
    read_reminder = reminder_minutes(end_data, ref)
    seed_data = {'title': start_data['title'], 'start_at': start_data['start_at'],
                 'end_at': start_data['end_at'], 'location': start_data['location'],
                 'description': start_data['description'], 'reminder_minutes_before': seed_reminder,
                 'source': source}
    readback_data = dict(seed_data, reminder_minutes_before=read_reminder)
    import_id = calendar_import_id(episode_id, ref)
    return {
        'record_ref': ref,
        'semantic_ref': ref,
        'status': 'supported',
        'identity': {'record_ref': ref, 'event_id': end_data.get('event_id'),
                     'business_ref': end_data.get('business_ref'), 'native_import_id': import_id,
                     'timezone': timezone},
        'seed': {'at': changes['observation_start'], 'data': seed_data},
        'readback': {'at': changes['decision_point'], 'data': readback_data,
                     'fields': list(READBACK_FIELDS['calendar']),
                     'native_expected': {'import_id': import_id, 'title': readback_data['title'],
                                         'start_ts': int(timestamp(readback_data['start_at'],
                                                                  f'{ref} start_at').timestamp()),
                                         'end_ts': int(timestamp(readback_data['end_at'],
                                                                 f'{ref} end_at').timestamp()),
                                         'location': readback_data['location'],
                                         'description': readback_data['description'],
                                         'reminder_1_minutes': read_reminder, 'source': source,
                                         'time_zone': timezone}},
        'history_changes': changes['record_changes'].get(ref, []),
    }


def build_mail_seed(episode_id, ref, start_data, end_data, payload_item, changes):
    if not isinstance(payload_item, dict) or not isinstance(payload_item.get('headers'), dict):
        raise ValueError(f'{ref} initialization payload mail entry needs headers')
    for field in ('body', 'attachments'):
        if field not in payload_item:
            raise ValueError(f'{ref} initialization payload mail entry misses {field!r}')
    seed_data = copy.deepcopy(payload_item)
    readback_data = copy.deepcopy(payload_item)
    for field in ('subject',):
        if not isinstance(end_data.get(field), str):
            raise ValueError(f'{ref} requires string field {field!r}')
    seed_data['status'] = start_data.get('status')
    readback_data['status'] = end_data.get('status')
    if not isinstance(seed_data['status'], str) or not isinstance(readback_data['status'], str):
        raise ValueError(f'{ref} requires a string status in history')
    return {
        'record_ref': ref,
        'semantic_ref': ref,
        'status': 'supported',
        'identity': {'record_ref': ref, 'message_id': end_data.get('message_id'),
                     'subject': end_data.get('subject'), 'match_field': 'headers.subject'},
        'seed': {'at': changes['observation_start'], 'data': seed_data},
        'readback': {'at': changes['decision_point'], 'data': readback_data,
                     'fields': list(READBACK_FIELDS['mail']),
                     'native_expected': {'headers.subject': end_data.get('subject'),
                                         'status': readback_data['status'],
                                         'match_field': 'headers.subject'}},
        'history_changes': changes['record_changes'].get(ref, []),
    }


def build_clock_seed(episode_id, ref, start_data, end_data, changes):
    for field, expected in (('label', str), ('hour', int), ('minute', int)):
        if not isinstance(end_data.get(field), expected) or isinstance(end_data.get(field), bool):
            raise ValueError(f'{ref} requires {expected.__name__} field {field!r}')
    if not isinstance(end_data.get('enabled'), bool):
        raise ValueError(f'{ref} requires bool field enabled')
    time_text = f'{end_data["hour"]:02d}:{end_data["minute"]:02d}'
    data = {'label': end_data['label'], 'time': time_text, 'enabled': end_data['enabled']}
    external_uuid = clock_external_uuid(episode_id, ref)
    return {
        'record_ref': ref,
        'semantic_ref': ref,
        'status': 'supported',
        'identity': {'record_ref': ref, 'alarm_id': end_data.get('alarm_id'),
                     'native_external_uuid': external_uuid},
        'seed': {'at': changes['observation_start'], 'data': dict(data)},
        'readback': {'at': changes['decision_point'], 'data': dict(data),
                     'fields': list(READBACK_FIELDS['clock']),
                     'native_expected': {'external_uuid': external_uuid, 'hour': end_data['hour'],
                                         'minutes': end_data['minute'],
                                         'enabled': int(end_data['enabled']), 'label': end_data['label'],
                                         'daysofweek': 0}},
        'history_changes': changes['record_changes'].get(ref, []),
    }


def contacts_data(ref, data):
    """Reduce one PB contacts record to the adapter's verifiable name/phone/email contract.

    ``gui_contacts.read_contact`` only accepts exactly one ``name`` row, exactly
    one ``phone_v2`` row and an optional single ``email_v2`` row; it compares the
    full ``mimetype -> data1`` map. Any other Android data row, duplicate email,
    missing name or missing phone is not verifiable, so it raises
    ``ContactsUnsupported`` and the record is never seeded.
    """
    details = data.get('details') if isinstance(data, dict) else None
    fields = details.get('fields') if isinstance(details, dict) else None
    if not isinstance(fields, list) or not fields:
        raise ContactsUnsupported(f'{ref} needs a nonempty details.fields list')
    grouped = {}
    for index, field in enumerate(fields):
        if not isinstance(field, dict) or not isinstance(field.get('mimetype'), str):
            raise ContactsUnsupported(f'{ref} details.fields[{index}] needs a mimetype')
        mimetype = field['mimetype']
        if mimetype not in CONTACTS_MIMETYPES:
            raise ContactsUnsupported(f'{ref} has an unsupported Android data row {mimetype!r}')
        value = field.get('data1')
        if not isinstance(value, str) or not value:
            raise ContactsUnsupported(f'{ref} {mimetype} needs a nonempty data1 string')
        grouped.setdefault(mimetype, []).append(value)
    names = grouped.get('vnd.android.cursor.item/name', [])
    phones = grouped.get('vnd.android.cursor.item/phone_v2', [])
    emails = grouped.get('vnd.android.cursor.item/email_v2', [])
    if len(names) != 1:
        raise ContactsUnsupported(f'{ref} needs exactly one name row, found {len(names)}')
    if len(phones) != 1:
        raise ContactsUnsupported(f'{ref} needs exactly one phone_v2 row, found {len(phones)}')
    if len(emails) > 1:
        raise ContactsUnsupported(f'{ref} has {len(emails)} email_v2 rows; the contacts adapter '
                                  f'verifies at most one')
    seed_data = {'name': names[0], 'phone': phones[0]}
    if emails:
        seed_data['email'] = emails[0]
    return seed_data


def build_contacts_seed(ref, start_data, end_data, changes):
    seed_data = contacts_data(ref, start_data)
    readback_data = contacts_data(ref, end_data)
    if seed_data != readback_data:
        raise ContactsUnsupported(f'{ref} contacts history changes are not replayable by the '
                                  f'contacts adapter')
    fields = ['name', 'phone'] + (['email'] if 'email' in seed_data else [])
    return {
        'record_ref': ref,
        'semantic_ref': ref,
        'status': 'supported',
        'identity': {'record_ref': ref, 'record_id': start_data.get('record_id'),
                     'title': start_data.get('title'), 'match_field': 'phone'},
        'seed': {'at': changes['observation_start'], 'data': dict(seed_data)},
        'readback': {'at': changes['decision_point'], 'data': dict(readback_data),
                     'fields': fields, 'native_expected': dict(seed_data)},
        'history_changes': changes['record_changes'].get(ref, []),
    }


def compile_plan(root, episode_id, cluster_filter=None):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'--pb-root is not a directory: {root}')
    benchmark_path = root / 'benchmark.json'
    if not benchmark_path.is_file():
        raise ValueError(f'benchmark.json not found under --pb-root: {benchmark_path}')
    benchmark = load_json(benchmark_path, 'benchmark')
    layout = benchmark.get('layout_version')
    if not isinstance(layout, str) or not layout.startswith('proactivebench.layout'):
        raise ValueError(f'benchmark layout_version is not a proactivebench layout: {layout!r}')
    cluster_id, cluster_dir, manifest_path, manifest, cluster = find_episode(
        root, benchmark, episode_id, cluster_filter)
    cluster_path, manifest_dir = cluster_dir / 'cluster.json', manifest_path.parent
    checks = {'references_checked': 0, 'references_within_root_and_existing': 0,
              'out_of_bundle_refs': [], 'domain_refs': [], 'short_refs_not_paths': [],
              'payload_state_records_verified': 0, 'history_overrides': 0, 'history_events': 0,
              'events_sorted_ascending': True, 'events_after_decision_point': 0,
              'private_fields_emitted': 0}

    cluster_bases = [('cluster', cluster_dir), ('pb-root', root)]
    manifest_bases = [('cluster', cluster_dir), ('episode', manifest_dir), ('pb-root', root)]
    init_bases = [('cluster', cluster_dir), ('pb-root', root)]
    sweep_document(root, [('pb-root', root)], benchmark, 'benchmark.json', checks)
    sweep_document(root, cluster_bases, cluster, 'cluster.json', checks)
    sweep_document(root, manifest_bases, manifest, 'manifest.json', checks)

    world_entry = entry_by_id(cluster.get('world_variants'), 'world_variant_id',
                              manifest.get('world_variant_id'), 'cluster world_variants')
    passive_entry = entry_by_id(cluster.get('passive_variants'), 'passive_id',
                                manifest.get('passive_id'), 'cluster passive_variants')
    for key in ('patch_ref', 'access_ref', 'binding_ref', 'history_ref', 'initialization_ref'):
        if manifest.get(key) != world_entry.get(key):
            raise ValueError(f'manifest {key} {manifest.get(key)!r} does not match world variant '
                             f'{world_entry.get("world_variant_id")!r} {world_entry.get(key)!r}')
    if manifest.get('passive_ref') != passive_entry.get('stream_ref'):
        raise ValueError('manifest passive_ref does not match the registered passive stream_ref')
    if manifest.get('passive_revision') != passive_entry.get('passive_revision'):
        raise ValueError('manifest passive_revision does not match the registered passive revision')
    if manifest.get('track') != passive_entry.get('track'):
        raise ValueError('manifest track does not match the registered passive track')
    if manifest.get('decision_point') != cluster.get('decision_point'):
        raise ValueError('manifest decision_point does not match cluster decision_point')
    world_context = cluster.get('world_context') or {}
    timezone = manifest.get('timezone')
    if not isinstance(timezone, str) or timezone != world_context.get('timezone'):
        raise ValueError('manifest timezone does not match cluster world_context.timezone')
    decision_point = timestamp(manifest.get('decision_point'), 'cluster decision_point')

    compatibility_ref = cluster.get('compatibility_ref')
    compatibility = load_json(resolve_ref(root, cluster_bases, compatibility_ref, 'compatibility_ref'),
                              'compatibility matrix')
    pairs = [item for item in compatibility['pairs']
             if item.get('passive_id') == manifest.get('passive_id')
             and item.get('world_variant_id') == manifest.get('world_variant_id')]
    if len(pairs) != 1:
        raise ValueError('compatibility matrix needs exactly one pair for the manifest '
                         f'{manifest.get("passive_id")!r}/{manifest.get("world_variant_id")!r}')
    pair = pairs[0]
    if pair.get('status') not in PAIR_STATUS_ALLOWED:
        raise ValueError(f'compatibility pair status {pair.get("status")!r} is not one of '
                         f'{list(PAIR_STATUS_ALLOWED)}; refusing to plan')
    if pair.get('episode_id') not in (None, episode_id):
        raise ValueError(f'compatibility pair belongs to episode {pair.get("episode_id")!r}, '
                         f'not {episode_id!r}')
    if pair.get('history_ref') not in (None, manifest.get('history_ref')):
        raise ValueError('compatibility pair history_ref does not match the manifest history_ref')
    declared_revision = compatibility.get('passive_revisions', {}).get(manifest.get('passive_id'))
    if declared_revision is not None and declared_revision != manifest.get('passive_revision'):
        raise ValueError('compatibility passive_revisions does not match the manifest passive_revision')
    if compatibility.get('cartesian_product_not_assumed') is not True:
        raise ValueError('compatibility matrix must declare cartesian_product_not_assumed')

    init_ref = world_entry.get('initialization_ref')
    initialization = load_json(resolve_ref(root, cluster_bases, init_ref, 'initialization_ref'),
                               'initialization payload')
    sweep_document(root, init_bases, initialization, 'initialization', checks)
    if initialization.get('world_variant_id') != manifest.get('world_variant_id'):
        raise ValueError('initialization world_variant_id does not match the manifest world_variant_id')
    target = timestamp(initialization.get('target_time'), 'initialization target_time')
    if target != decision_point:
        raise ValueError(f'initialization target_time {initialization.get("target_time")!r} '
                         f'does not equal decision_point {manifest.get("decision_point")!r}')
    if initialization.get('timezone') != timezone:
        raise ValueError('initialization timezone does not match the manifest timezone')
    world_status = initialization.get('status')
    if not isinstance(world_status, str) or not world_status:
        raise ValueError('initialization payload requires a status string')
    init_payload = initialization.get('payload')
    if not isinstance(init_payload, dict):
        raise ValueError('initialization payload requires a payload object')
    device = init_payload.get('device')
    if device is not None and (not isinstance(device, dict) or device.get('timezone') != timezone):
        raise ValueError('initialization payload device timezone does not match the manifest timezone')
    device_timezone = (device or {}).get('timezone', timezone)

    contract_ref = cluster.get('initialization_contract_ref')
    contract = load_json(resolve_ref(root, cluster_bases, contract_ref, 'initialization_contract_ref'),
                         'adapter contract')
    sweep_document(root, init_bases, contract, 'adapter_contract', checks)
    declared_observation = contract.get('observation_start')
    observation_start = timestamp(declared_observation.get('logical_time'),
                                  'adapter contract observation_start.logical_time') \
        if declared_observation is not None else None
    logical_time_source = contract.get('decision_point', {}).get('logical_time_source')
    if logical_time_source is not None and logical_time_source != 'cluster.json decision_point':
        raise ValueError('adapter contract must bind the decision point to cluster.json decision_point')

    history_ref = world_entry.get('history_ref')
    history = load_json(resolve_ref(root, cluster_bases, history_ref, 'history_ref'), 'history')
    sweep_document(root, cluster_bases, history, 'history', checks)
    if history.get('world_variant_id') != manifest.get('world_variant_id'):
        raise ValueError('history world_variant_id does not match the manifest world_variant_id')
    history_observation = timestamp(history.get('observation_start'), 'history observation_start')
    if observation_start is None:
        observation_start = history_observation
    elif history_observation != observation_start:
        raise ValueError('history observation_start does not match the adapter contract observation_start')
    if observation_start > decision_point:
        raise ValueError('observation_start is after the decision point')
    overrides = history.get('initial_overrides', [])
    events = history.get('events', [])
    if not isinstance(overrides, list) or not isinstance(events, list):
        raise ValueError('history initial_overrides and events must be lists')

    baseline_ref = cluster.get('global_base_ref')
    baseline = load_json(resolve_ref(root, cluster_bases, baseline_ref, 'global_base_ref'),
                         'shared baseline')
    cluster_patch_ref = cluster.get('cluster_base_ref')
    cluster_patch = load_json(resolve_ref(root, cluster_bases, cluster_patch_ref, 'cluster_base_ref'),
                              'cluster base patch')
    variant_patch_ref = world_entry.get('patch_ref')
    variant_patch = load_json(resolve_ref(root, cluster_bases, variant_patch_ref, 'patch_ref'),
                              'variant patch')
    records = compose_world(baseline, cluster_patch, variant_patch)
    checks['world_records'] = len(records)

    for index, override in enumerate(overrides):
        if not isinstance(override, dict) or 'entity_ref' not in override:
            raise ValueError(f'history initial_overrides[{index}] needs an entity_ref')
    ordered_events = sorted(events, key=lambda event: timestamp(event.get('effective_at'), 'history event effective_at'))
    checks['events_sorted_ascending'] = [event.get('effective_at') for event in events] == \
                                        [event.get('effective_at') for event in ordered_events]
    for index, event in enumerate(ordered_events):
        effective = timestamp(event.get('effective_at'), f'history events[{index}].effective_at')
        if effective < observation_start:
            raise ValueError(f'history events[{index}] effective_at precedes observation_start')
        if effective > decision_point:
            checks['events_after_decision_point'] += 1
        if not isinstance(event.get('updates'), list) or not event['updates']:
            raise ValueError(f'history events[{index}] needs a nonempty updates list')

    for ref in [override['entity_ref'] for override in overrides] + \
            [update.get('entity_ref') for event in events for update in event.get('updates', [])]:
        if ref not in records:
            raise ValueError(f'history targets a record outside the composed world: {ref!r}')

    start_state = apply_changes({ref: copy.deepcopy(record) for ref, record in records.items()},
                                overrides, 'history initial_overrides')
    point_state = {ref: copy.deepcopy(record) for ref, record in start_state.items()}
    applied_events = apply_events(point_state, ordered_events, decision_point, 'history events')
    record_changes = {}
    for event in applied_events:
        for update in event['updates']:
            ref = update['entity_ref']
            record_changes.setdefault(ref, []).append(
                {'effective_at': event['effective_at'], 'set': copy.deepcopy(update['set'])})

    bindings = load_json(resolve_ref(root, cluster_bases, world_entry.get('binding_ref'), 'binding_ref'),
                         'bindings')
    if bindings.get('world_hash') != initialization.get('world_hash'):
        raise ValueError('bindings world_hash does not match the initialization world_hash')
    if bindings.get('world_variant_id') != manifest.get('world_variant_id'):
        raise ValueError('bindings world_variant_id does not match the manifest world_variant_id')
    queries = bindings.get('queries')
    if not isinstance(queries, dict) or not queries:
        raise ValueError('bindings require a nonempty queries object')
    binding_refs = []
    for action_id, query in queries.items():
        refs = query.get('record_refs') if isinstance(query, dict) else None
        if not isinstance(refs, list):
            raise ValueError(f'bindings query {action_id!r} needs a record_refs list')
        for ref in refs:
            if ref not in records:
                raise ValueError(f'bindings query {action_id!r} references an unknown record {ref!r}')
            binding_refs.append(ref)
    binding_refs = sorted(set(binding_refs))
    checks['bindings_record_refs'] = len(binding_refs)

    scene_refs = [op['entity_ref'] for patch in (cluster_patch, variant_patch)
                  for op in patch.get('patches', [])
                  if op.get('op') in ('add', 'update') and records.get(op['entity_ref'], {}).get('source')
                  in SUPPORTED_SOURCES]
    scene_refs = sorted(set(scene_refs), key=lambda ref: (SUPPORTED_SOURCES.index(records[ref]['source']), ref))

    changes_context = {'observation_start': observation_start.isoformat(),
                       'decision_point': decision_point.isoformat(),
                       'record_changes': record_changes}
    sources = {}
    contacts_unsupported = {}
    payload_indexes = {source: payload_index(init_payload, source) for source in SUPPORTED_SOURCES}
    for ref in scene_refs:
        record = records[ref]
        source = record['source']
        key = world_record_key(source, record.get('data') or {})
        item = payload_indexes[source].get(key)
        if item is None:
            raise ValueError(f'{ref} is missing from the initialization payload {source} entries')
        verify_payload_record(source, ref, point_state[ref], start_state[ref], item, checks)
        context = dict(changes_context)
        if source == 'calendar':
            entry = build_calendar_seed(episode_id, ref, record, start_state[ref]['data'],
                                        point_state[ref]['data'], timezone, context)
        elif source == 'mail':
            entry = build_mail_seed(episode_id, ref, start_state[ref]['data'],
                                    point_state[ref]['data'], item, context)
        elif source == 'clock':
            entry = build_clock_seed(episode_id, ref, start_state[ref]['data'],
                                     point_state[ref]['data'], context)
        else:
            if context['record_changes'].get(ref):
                contacts_unsupported[ref] = (
                    f'{ref} declares contacts history changes the contacts adapter cannot replay')
                continue
            try:
                entry = build_contacts_seed(ref, start_state[ref]['data'],
                                            point_state[ref]['data'], context)
            except ContactsUnsupported as error:
                contacts_unsupported[ref] = str(error)
                continue
        source_plan = sources.setdefault(source, {
            'source': source, 'status': 'supported', 'adapter': ADAPTERS[source],
            'field_map': FIELD_MAP[source], 'expected_readback_fields': list(READBACK_FIELDS[source]),
            'seed_records': [], 'background_records': {'policy': 'preserve_existing_snapshot_records',
                                                       'readback_fields': list(BACKGROUND_READBACK_FIELDS[source]),
                                                       'record_refs': [], 'count': 0}})
        source_plan['seed_records'].append(entry)
    background = {ref: records[ref]['source'] for ref in binding_refs
                  if records[ref].get('source') in SUPPORTED_SOURCES and ref not in set(scene_refs)}
    for ref, source in sorted(background.items()):
        source_plan = sources.get(source)
        if source_plan is None:
            continue
        source_plan['background_records']['record_refs'].append(ref)
    for source_plan in sources.values():
        background_refs = source_plan['background_records']['record_refs']
        source_plan['background_records']['record_refs'] = sorted(background_refs)
        source_plan['background_records']['count'] = len(background_refs)

    unsupported = {}
    for ref in binding_refs:
        source = records[ref].get('source')
        if source in SUPPORTED_SOURCES:
            continue
        unsupported.setdefault(source, {'source': source, 'kind': 'world_records',
                                        'reason': SOURCE_REASONS.get(source, DEFAULT_UNSUPPORTED_REASON),
                                        'record_refs': [], 'count': 0})
        unsupported[source]['record_refs'].append(ref)
    for ref in sorted(contacts_unsupported):
        entry = unsupported.setdefault('contacts', {'source': 'contacts', 'kind': 'world_records',
                                                    'reason': CONTACTS_UNSUPPORTED_REASON,
                                                    'record_refs': [], 'count': 0})
        entry['record_refs'].append(ref)
    payload_sources = init_payload.get('sources') if isinstance(init_payload.get('sources'), dict) \
        else init_payload
    for source in sorted(payload_sources):
        if source not in SUPPORTED_SOURCES:
            unsupported.setdefault(source, {'source': source, 'kind': 'initialization_payload',
                                            'reason': SOURCE_REASONS.get(source, DEFAULT_UNSUPPORTED_REASON),
                                            'record_refs': [], 'count': 0})
    unsupported_list = []
    for source in sorted(unsupported):
        entry = unsupported[source]
        entry['record_refs'] = sorted(entry['record_refs'])
        entry['count'] = len(entry['record_refs'])
        unsupported_list.append(entry)

    seed_refs = [item['record_ref'] for entries in sources.values() for item in entries['seed_records']]
    seed_ref_set = set(seed_refs)
    history_plan = {
        'observation_start': observation_start.isoformat(),
        'initial_overrides': [
            {'entity_ref': override['entity_ref'], 'set': copy.deepcopy(override['set']),
             'record_ref': override['entity_ref'], 'supported': override['entity_ref'] in seed_ref_set}
            for override in overrides],
        'events': [
            {'effective_at': event['effective_at'],
             'updates': [{'entity_ref': update['entity_ref'], 'set': copy.deepcopy(update['set']),
                          'record_ref': update['entity_ref'], 'supported': update['entity_ref'] in seed_ref_set}
                         for update in event.get('updates', [])]}
            for event in ordered_events],
    }
    checks['history_overrides'] = len(overrides)
    checks['history_events'] = len(ordered_events)

    references = {
        'benchmark': {'path': 'benchmark.json', 'sha256': digest(benchmark_path)},
        'cluster': {'path': cluster_path.relative_to(root).as_posix(),
                    'sha256': digest(cluster_path)},
        'manifest': {'path': manifest_path.relative_to(root).as_posix(), 'sha256': digest(manifest_path)},
        'compatibility': {'path': resolve_ref(root, cluster_bases, compatibility_ref,
                                              'compatibility_ref').relative_to(root).as_posix(),
                          'sha256': digest(resolve_ref(root, cluster_bases, compatibility_ref,
                                                       'compatibility_ref'))},
        'cluster_base_patch': {'path': resolve_ref(root, cluster_bases, cluster_patch_ref,
                                                   'cluster_base_ref').relative_to(root).as_posix(),
                               'sha256': digest(resolve_ref(root, cluster_bases, cluster_patch_ref,
                                                            'cluster_base_ref'))},
        'variant_patch': {'path': resolve_ref(root, cluster_bases, variant_patch_ref,
                                              'patch_ref').relative_to(root).as_posix(),
                          'sha256': digest(resolve_ref(root, cluster_bases, variant_patch_ref,
                                                       'patch_ref'))},
        'initialization': {'path': resolve_ref(root, cluster_bases, init_ref,
                                               'initialization_ref').relative_to(root).as_posix(),
                           'sha256': digest(resolve_ref(root, cluster_bases, init_ref,
                                                        'initialization_ref'))},
        'history': {'path': resolve_ref(root, cluster_bases, history_ref,
                                        'history_ref').relative_to(root).as_posix(),
                    'sha256': digest(resolve_ref(root, cluster_bases, history_ref, 'history_ref'))},
        'bindings': {'path': resolve_ref(root, cluster_bases, world_entry.get('binding_ref'),
                                         'binding_ref').relative_to(root).as_posix(),
                     'sha256': digest(resolve_ref(root, cluster_bases, world_entry.get('binding_ref'),
                                                  'binding_ref'))},
        'adapter_contract': {'path': resolve_ref(root, cluster_bases, contract_ref,
                                                 'initialization_contract_ref').relative_to(root).as_posix(),
                             'sha256': digest(resolve_ref(root, cluster_bases, contract_ref,
                                                          'initialization_contract_ref'))},
        'baseline_world': {'path': resolve_ref(root, cluster_bases, baseline_ref,
                                               'global_base_ref').relative_to(root).as_posix(),
                           'sha256': digest(resolve_ref(root, cluster_bases, baseline_ref,
                                                        'global_base_ref'))},
    }
    for name, key, ref in (('observation_start_calendar_seed', 'calendar_seed_ref',
                            contract.get('observation_start', {}).get('calendar_seed_ref')),
                           ('observation_start_mail_seed', 'mail_seed_ref',
                            contract.get('observation_start', {}).get('mail_seed_ref'))):
        if isinstance(ref, str) and ref:
            references[name] = {'path': resolve_ref(root, init_bases, ref, key).relative_to(root).as_posix(),
                                'sha256': digest(resolve_ref(root, init_bases, ref, key))}
    if isinstance(cluster.get('source_registry_ref'), str):
        references['source_registry'] = {
            'path': resolve_ref(root, cluster_bases, cluster['source_registry_ref'],
                                'source_registry_ref').relative_to(root).as_posix(),
            'sha256': digest(resolve_ref(root, cluster_bases, cluster['source_registry_ref'],
                                         'source_registry_ref'))}

    plan = {
        'schema_version': PLAN_SCHEMA,
        'status': 'planned' if seed_refs else 'unsupported',
        'device_applied': False,
        'completion_claim': 'offline_plan_only',
        'layout_version': layout,
        'episode_scope': {
            'cluster_id': cluster_id,
            'episode_id': episode_id,
            'passive_id': manifest.get('passive_id'),
            'world_variant_id': manifest.get('world_variant_id'),
            'track': manifest.get('track'),
            'input_mode': manifest.get('input_mode'),
            'passive_revision': manifest.get('passive_revision'),
            'passive_release_status': passive_entry.get('release_status'),
            'compatibility_status': pair.get('status'),
            'review_pending': True,
            'observation_start': observation_start.isoformat(),
            'decision_point': decision_point.isoformat(),
            'timezone': timezone,
            'world_status': world_status,
            'world_hash': initialization.get('world_hash'),
            'device_applied': False,
        },
        'observation_start': observation_start.isoformat(),
        'decision_point': decision_point.isoformat(),
        'timezone': timezone,
        'world_variant_id': manifest.get('world_variant_id'),
        'world': {
            'world_status': world_status,
            'world_hash': initialization.get('world_hash'),
            'target_time': initialization.get('target_time'),
            'timezone': initialization.get('timezone'),
            'device_applied': False,
            'note': 'PB initialization payloads are not applied to Android; this is offline planning only',
        },
        'environment': {
            'timezone': timezone,
            'device_timezone': device_timezone,
            'clock_policy': contract.get('observation_start', {}).get('clock_policy'),
            'history_policy': contract.get('observation_start', {}).get('history_policy'),
            'device_requirements': initialization.get('requirements') if isinstance(
                initialization.get('requirements'), list) else [],
        },
        'sources': {source: sources[source] for source in SUPPORTED_SOURCES if source in sources},
        'unsupported': unsupported_list,
        'history': history_plan,
        'references': references,
        'checks': {
            'references_checked': checks['references_checked'],
            'references_within_root_and_existing': checks['references_within_root_and_existing'],
            'out_of_bundle_refs': checks['out_of_bundle_refs'],
            'short_refs_not_paths': len(checks['short_refs_not_paths']),
            'world_records': checks['world_records'],
            'bindings_record_refs': checks['bindings_record_refs'],
            'supported_seed_records': len(seed_refs),
            'payload_state_records_verified': checks['payload_state_records_verified'],
            'unsupported_sources': len(unsupported_list),
            'unsupported_record_refs': sum(entry['count'] for entry in unsupported_list),
            'history_overrides': checks['history_overrides'],
            'history_events': checks['history_events'],
            'events_sorted_ascending': checks['events_sorted_ascending'],
            'events_after_decision_point': checks['events_after_decision_point'],
            'private_fields_emitted': 0,
            'absolute_paths_emitted': 0,
        },
    }
    leaks = private_key_paths(plan)
    if leaks:
        raise ValueError(f'private GT material leaked into the plan: {leaks}')
    absolutes = absolute_value_paths(plan)
    if absolutes:
        raise ValueError(f'local absolute paths leaked into the plan: {absolutes}')
    return plan


def validate_plan(plan, root=None):
    if not isinstance(plan, dict):
        raise ValueError('plan must be a JSON object')
    if plan.get('schema_version') != PLAN_SCHEMA:
        raise ValueError(f'plan schema_version must be {PLAN_SCHEMA!r}')
    status = plan.get('status')
    if status not in ('planned', 'unsupported'):
        raise ValueError(f'plan status {status!r} is neither planned nor unsupported')
    if status in COMPLETION_STATUSES or plan.get('device_applied') is not False:
        raise ValueError('plan may not claim device completion; offline plans stay planned/unsupported')
    scope = plan.get('episode_scope')
    if not isinstance(scope, dict) or not scope.get('episode_id') or not scope.get('world_variant_id'):
        raise ValueError('plan episode_scope requires episode_id and world_variant_id')
    world = plan.get('world') or {}
    if scope.get('world_status') != world.get('world_status'):
        raise ValueError('plan world status is inconsistent between episode_scope and world')
    if scope.get('world_status') == UNAPPLIED_STATUS and plan.get('device_applied') is not False:
        raise ValueError(f'PB world status {UNAPPLIED_STATUS} forbids a device completion claim')
    for source, entry in (plan.get('sources') or {}).items():
        if source not in SUPPORTED_SOURCES:
            raise ValueError(f'plan maps an unsupported source {source!r}')
        if entry.get('status') != 'supported':
            raise ValueError(f'plan source {source!r} must be marked supported or left out')
        for record in entry.get('seed_records', []):
            if not record.get('record_ref') or record.get('semantic_ref') != record.get('record_ref'):
                raise ValueError(f'{source} seed record needs a matching record_ref/semantic_ref')
            data, readback = record.get('seed', {}).get('data'), record.get('readback', {})
            if not isinstance(data, dict) or not isinstance(readback.get('data'), dict):
                raise ValueError(f'{record.get("record_ref")} needs seed/readback data objects')
            if not readback.get('fields'):
                raise ValueError(f'{record.get("record_ref")} needs readback fields')
            if source == 'calendar':
                if not isinstance(data.get('reminder_minutes_before'), int) or isinstance(
                        data['reminder_minutes_before'], bool):
                    raise ValueError(f'{record.get("record_ref")} reminder_minutes_before must be an integer')
                timestamp(data.get('start_at'), f'{record.get("record_ref")} start_at')
                timestamp(data.get('end_at'), f'{record.get("record_ref")} end_at')
            elif source == 'mail':
                if not isinstance(data.get('headers'), dict) or 'body' not in data or 'status' not in data:
                    raise ValueError(f'{record.get("record_ref")} mail data needs headers/body/status')
            elif source == 'clock':
                if 'label' not in data or 'enabled' not in data or not re.fullmatch(r'\d{2}:\d{2}', data.get('time', '')):
                    raise ValueError(f'{record.get("record_ref")} clock data needs label/time/enabled')
            else:
                unknown = sorted(set(data) - {'name', 'phone', 'email'})
                if unknown or not isinstance(data.get('name'), str) or not data.get('name') \
                        or not isinstance(data.get('phone'), str) or not data.get('phone'):
                    raise ValueError(f'{record.get("record_ref")} contacts data needs a name and phone '
                                     f'(unknown fields {unknown})')
                if 'email' in data and (not isinstance(data['email'], str) or not data['email']):
                    raise ValueError(f'{record.get("record_ref")} contacts email must be a nonempty string')
    for entry in plan.get('unsupported', []):
        if not entry.get('source') or not entry.get('reason'):
            raise ValueError('unsupported entries need a source and a reason')
    history = plan.get('history') or {}
    events = history.get('events')
    if not isinstance(events, list):
        raise ValueError('plan history.events must be a list')
    stamps = [timestamp(event.get('effective_at'), 'plan history event') for event in events]
    if stamps != sorted(stamps):
        raise ValueError('plan history.events must be sorted by effective_at')
    leaks = private_key_paths(plan)
    if leaks:
        raise ValueError(f'private GT material present in the plan: {leaks}')
    absolutes = absolute_value_paths(plan)
    if absolutes:
        raise ValueError(f'local absolute paths present in the plan: {absolutes}')
    checked = 0
    if root is not None:
        root = Path(root).expanduser().resolve()
        references = plan.get('references')
        if not isinstance(references, dict) or not references:
            raise ValueError('plan references are required to validate against --pb-root')
        for name, entry in references.items():
            if name in NON_EMITTED_REF_KEYS:
                raise ValueError(f'plan must not emit reference {name!r}')
            path, expected = entry.get('path'), entry.get('sha256')
            if not isinstance(path, str) or not isinstance(expected, str):
                raise ValueError(f'plan reference {name!r} needs path and sha256')
            target = resolve_inside(root, [('pb-root', root / path)], path, f'plan reference {name!r}')
            actual = digest(target)
            if actual != expected:
                raise ValueError(f'plan reference {name!r} hash changed: {expected} != {actual}')
            checked += 1
    return {'status': 'ok', 'schema_version': PLAN_SCHEMA, 'plan_status': status,
            'episode_id': scope.get('episode_id'), 'world_variant_id': scope.get('world_variant_id'),
            'world_status': scope.get('world_status'),
            'sources': sorted(plan.get('sources', {})), 'unsupported_sources': len(plan.get('unsupported', [])),
            'references_verified': checked}


def plan_summary(plan, root=None):
    scope = plan['episode_scope']
    summary = {'status': 'ok', 'plan_status': plan['status'], 'cluster_id': scope.get('cluster_id'),
               'episode_id': scope.get('episode_id'), 'passive_id': scope.get('passive_id'),
               'world_variant_id': scope.get('world_variant_id'), 'world_status': scope.get('world_status'),
               'device_applied': plan['device_applied'],
               'sources': sorted(plan.get('sources', {})),
               'seed_records': plan['checks']['supported_seed_records'],
               'unsupported_sources': plan['checks']['unsupported_sources'],
               'unsupported_records': plan['checks']['unsupported_record_refs'],
               'references_checked': plan['checks']['references_checked'],
               'events_sorted_ascending': plan['checks']['events_sorted_ascending']}
    if root is not None:
        summary['references_verified'] = validate_plan(plan, root)['references_verified']
    return summary


def write_json(output, payload):
    target = Path(output).expanduser().resolve()
    if target.is_dir():
        raise ValueError(f'--output is a directory: {target}')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    except OSError as error:
        raise ValueError(f'cannot write --output {target}: {error}') from None
    return target


def command_plan(args):
    plan = compile_plan(args.pb_root, args.episode_id, args.cluster)
    summary = plan_summary(plan, args.pb_root)
    if args.output:
        target = write_json(args.output, plan)
        summary['output'] = str(target)
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def command_validate(args):
    if args.plan:
        plan = load_json(Path(args.plan).expanduser().resolve(), 'plan')
        summary = validate_plan(plan, args.pb_root if args.pb_root else None)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if not args.pb_root or not args.episode_id:
        raise ValueError('validate needs --plan or both --pb-root and --episode-id')
    plan = compile_plan(args.pb_root, args.episode_id, args.cluster)
    summary = plan_summary(plan, args.pb_root)
    if args.output:
        target = write_json(args.output, plan)
        summary['output'] = str(target)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_backend_plan.py',
        description='Compile one PB episode into a private offline backend plan for the '
                    'Calendar/Mail/Clock adapters (default command is plan).')
    subparsers = parser.add_subparsers(dest='command')
    for name, help_text in (('plan', 'write the offline backend plan for one episode'),
                            ('validate', 'validate a plan file or recompile one episode')):
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument('--pb-root', type=Path,
                               help='ProactiveBench root containing benchmark.json')
        subparser.add_argument('--episode-id', help='episode id, e.g. ep_0004')
        subparser.add_argument('--cluster', help='restrict the search to one registered cluster id')
        subparser.add_argument('--plan', type=Path, help='existing plan JSON to validate')
        subparser.add_argument('--output', type=Path, help='write the plan to this JSON file')
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('-h', '--help'):
        pass
    elif not argv or argv[0] not in COMMANDS:
        argv = ['plan', *argv]
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'plan':
            if not args.pb_root or not args.episode_id:
                raise ValueError('plan needs --pb-root and --episode-id')
            return command_plan(args)
        return command_validate(args)
    except ValueError as error:
        print(f'pb_backend_plan.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
