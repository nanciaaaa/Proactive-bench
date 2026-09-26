#!/usr/bin/env python3
"""Compile one registered PB GUI episode into a private device capture plan.

Read-only bridge between the round-1..3 PB artifacts and the existing device
runner ``scripts/gui_capture.py::run``:

* input: ``pb_gui_bridge.py`` GUI plan, ``pb_backend_plan.py`` backend plan,
  ``pb_backend_apply.py`` success receipt and the ``pb_gui_capture_gate.py``
  gate plan, selected by the explicit ``--cluster-id`` / ``--episode-id``
  scope. Only episodes with a registered packet/view profile compile; an
  unknown episode, cluster or GUI packet aborts instead of being guessed;
* the gate must still be ``ready_for_device_capture`` with pending targets, the
  receipt must be ``status=ok`` with ``device_applied=true``, the receipt
  ``plan_sha256`` must equal the backend plan bytes and the gate bindings must
  equal the GUI plan/receipt bytes; any scope or hash drift aborts;
* the registered ``meeting-preparation-001`` / ``ep_0004`` profile (passive
  ``p002`` / world ``w001``) compiles all seven GUI packets: ``p004`` (calendar
  ``event_editor``), ``p003``/``p018`` (calendar ``calendar_list`` day view),
  ``p010``/``p013`` (mail ``mail_detail``) and ``p009``/``p015`` (mail
  ``mail_inbox``); a registered packet whose view the capture runner cannot
  open faithfully stays unsupported instead of being retargeted at another view;
* the packet -> view table is per episode and frozen; an unknown episode,
  cluster, GUI packet id, source or view aborts compilation, and no view is ever
  inferred from packet text. The ``ep_0005`` (``p003``/``w002``) profile is
  registered with the same seven calendar/mail packet views and additionally
  consumes the Calendar reminder capability: the real w002 history adds a
  ``calendar.event.cal_001`` ``calendar_reminder_minutes`` change at 08:31:00,
  which is emitted as a ``history_replays`` entry on the first packet at/after
  that instant (``p009``) and replayed by ``gui_capture.run`` through
  ``gui_calendar.apply_history_change`` (single ``import_id``, exactly one
  affected row, independent three-column/``last_updated`` readback). The
  decision-point Calendar readback must already carry the declared post-change
  reminder; a changed readback without a declared replay, or a declared change
  no device adapter supports, still fails closed;
* the emitted plan carries ``baseline_ref=init_state``,
  ``device_timezone=Asia/Shanghai``, ``eof_at`` equal to the backend decision
  point, ``pb_clock_binding`` (``observation_start``, ``decision_point``,
  ``timezone``, ``source='PB history/cluster'``) plus
  ``initial_simulated_time=observation_start`` so ``gui_capture.run`` rewinds the
  extension simulated clock to the start of the PB window after reset, the
  backend plan's observation-start ``seed.data`` records as
  ``fixture.ku_records`` (the decision-point ``readback.data`` never seeds the
  device), and per-packet
  ``capture.frames`` / ``visible_assertions`` / ``expected_visible`` /
  ``timeout_seconds`` so ``gui_capture.run`` executes it unchanged; every
  calendar packet additionally carries the private
  ``navigation_mode='direct_event'``, because the PB Chinese event title cannot
  be typed through ``adb shell input text`` (Android aborts the search with a
  NullPointerException). ``gui_capture.run`` then taps the event date cell on
  the freshly launched Calendar; ``p003``/``p018`` stop on the day view and
  assert its ``day_holder`` header and event row nodes, while ``p004``
  continues to tap the unique event item. ``p009``/``p015`` cold-start Mail
  into its Inbox list and assert the exact row text nodes without opening a
  detail page. Only a registered PB clock-bound plan compiles here, so old
  pilot plans keep their exact search navigation;
* every registered GUI packet must be observed inside
  ``[observation_start, decision_point]`` in the declared timezone; any packet
  before the observation start, after the decision point or on another UTC
  offset aborts compilation, because advancing the extension simulated clock
  backwards or across zones would make the Controller reject the capture;
* every declared supported history update is replayed on the device: Mail
  ``unread -> read`` transitions and Calendar ``calendar_reminder_minutes``
  (with ``updated_at``) changes are attached to the first compiled packet whose
  ``observed_at`` has passed the update's ``effective_at`` as a private
  optional ``history_replays`` list (ep_0005: ``p009`` replays the Calendar
  reminder, ``p010`` replays ``mail_001``, ``p013`` replays ``mail_002``,
  ``p003``/``p004`` replay nothing), and an update no compiled packet can cover
  before EOF aborts compilation instead of being dropped. A declared history
  change on any other source, an unknown Calendar field or a changed Calendar
  readback without a matching replay aborts with the concrete missing device
  replay/readback capability instead of being dropped or silently ignored;
* PB original screenshots are never read, copied, rewritten or re-hashed.

Usage::

    python3 scripts/pb_gui_capture_compile.py compile \
        --cluster-id meeting-preparation-001 --episode-id ep_0004 \
        --gui-plan plan.json --backend-plan backend-plan.json \
        --backend-receipt receipt.json --gate-plan gate.json --output capture-plan.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pb_backend_plan as pb
import pb_gui_capture_gate as capture_gate

ROOT = SCRIPT_DIR.parent
PLAN_SCHEMA = 'pb-gui-private-capture-plan.v1'
GUI_PLAN_SCHEMA = 'pb-gui-bridge.plan.v1'
BACKEND_PLAN_SCHEMA = 'pb-backend-plan.v1'
RECEIPT_SCHEMA = 'pb-backend-apply-receipt.v1'
GATE_SCHEMA = 'pb-gui-capture-gate.v1'
GATE_STATUS = 'ready_for_device_capture'
PENDING_CAPTURE = 'pending_device_capture'
BASELINE_REF = 'init_state'
DEVICE_TIMEZONE = 'Asia/Shanghai'
TIMEOUT_SECONDS = 30
FRAME_SUFFIX = '_f1'
# Registered episode scopes. The CLI requires an explicit cluster/episode
# selection and every input artifact has to match it; nothing is inferred from
# the GUI plan and an unregistered episode never compiles.
EP_0004_SCOPE = {'cluster_id': 'meeting-preparation-001', 'episode_id': 'ep_0004',
                 'passive_id': 'p002', 'world_variant_id': 'w001'}
EP_0005_SCOPE = {'cluster_id': 'meeting-preparation-001', 'episode_id': 'ep_0005',
                 'passive_id': 'p003', 'world_variant_id': 'w002'}
ENTITY_TYPES = {'calendar': 'events', 'mail': 'mails', 'clock': 'alarms'}
MAIL_HISTORY_FIELD = 'status'
MAIL_HISTORY_SOURCE_STATUS = 'unread'
MAIL_HISTORY_TARGET_STATUS = 'read'
CALENDAR_HISTORY_FIELDS = ('calendar_reminder_minutes', 'updated_at')
CALENDAR_REMINDER_COUNT = 3
PB_CLOCK_SOURCE = 'PB history/cluster'
# Private navigation hint for the PB clock-bound ep_0004 calendar captures. The
# legacy ``gui_capture.run`` route searched for the Chinese title via
# ``adb shell input text``; Android rejects that input with a
# NullPointerException on the real device, so the PB plan instead asks the
# runner to tap the unique event date cell (and, for the editor packets, the
# unique event item text afterwards) directly.
CALENDAR_DIRECT_NAVIGATION = 'direct_event'
# Views that an existing gui_capture.run branch can open faithfully for this episode.
NAVIGABLE_VIEWS = {'calendar': frozenset({'event_editor', 'calendar_list'}),
                   'mail': frozenset({'mail_detail', 'mail_inbox'})}
# Calendar day-list assertions: the day header plus the unique event row nodes.
CALENDAR_LIST_FIELDS = ('day_label', 'event_title', 'event_time', 'event_description')
# Mail Inbox rows top to bottom. ``seed_mail`` prepends every receipt record, so
# the later mail (mail_002, 08:20) renders above mail_001 (08:10); the row
# prefix only names the assertion fields, the device is bound by exact text.
MAIL_INBOX_FIELDS = ('sender', 'subject', 'snippet')
MAIL_INBOX_ROWS = ({'prefix': 'row_1', 'subject_ref': 'mail.message.mail_002'},
                   {'prefix': 'row_2', 'subject_ref': 'mail.message.mail_001'})
# Frozen ep_0004 GUI packet table: every frozen packet is executable, and an
# unknown/mistyped packet still aborts instead of being guessed at another view.
EP_0004_PACKETS = {
    'p003': {'source': 'calendar', 'view_id': 'calendar_list',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': CALENDAR_LIST_FIELDS},
    'p004': {'source': 'calendar', 'view_id': 'event_editor',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': ('title', 'location', 'description', 'start_at', 'end_at')},
    'p009': {'source': 'mail', 'view_id': 'mail_inbox',
             'subject_ref': 'mail.message.mail_002', 'rows': MAIL_INBOX_ROWS,
             'visible_assertions': tuple(f'{row["prefix"]}_{field}'
                                         for row in MAIL_INBOX_ROWS for field in MAIL_INBOX_FIELDS)},
    'p010': {'source': 'mail', 'view_id': 'mail_detail',
             'subject_ref': 'mail.message.mail_001',
             'visible_assertions': ('subject', 'from', 'body')},
    'p013': {'source': 'mail', 'view_id': 'mail_detail',
             'subject_ref': 'mail.message.mail_002',
             'visible_assertions': ('subject', 'from', 'body')},
    'p015': {'source': 'mail', 'view_id': 'mail_inbox',
             'subject_ref': 'mail.message.mail_002', 'rows': MAIL_INBOX_ROWS,
             'visible_assertions': tuple(f'{row["prefix"]}_{field}'
                                         for row in MAIL_INBOX_ROWS for field in MAIL_INBOX_FIELDS)},
    'p018': {'source': 'calendar', 'view_id': 'calendar_list',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': CALENDAR_LIST_FIELDS},
}
# Frozen ep_0005 GUI packet table. The seven p003 packets are the same visual
# views as ep_0004 (verified against the PB stream text and w002 history), so the
# packet -> view mapping is declared explicitly per episode rather than shared,
# and a divergent future stream cannot silently reuse the ep_0004 table.
EP_0005_PACKETS = {
    'p003': {'source': 'calendar', 'view_id': 'calendar_list',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': CALENDAR_LIST_FIELDS},
    'p004': {'source': 'calendar', 'view_id': 'event_editor',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': ('title', 'location', 'description', 'start_at', 'end_at')},
    'p009': {'source': 'mail', 'view_id': 'mail_inbox',
             'subject_ref': 'mail.message.mail_002', 'rows': MAIL_INBOX_ROWS,
             'visible_assertions': tuple(f'{row["prefix"]}_{field}'
                                         for row in MAIL_INBOX_ROWS for field in MAIL_INBOX_FIELDS)},
    'p010': {'source': 'mail', 'view_id': 'mail_detail',
             'subject_ref': 'mail.message.mail_001',
             'visible_assertions': ('subject', 'from', 'body')},
    'p013': {'source': 'mail', 'view_id': 'mail_detail',
             'subject_ref': 'mail.message.mail_002',
             'visible_assertions': ('subject', 'from', 'body')},
    'p015': {'source': 'mail', 'view_id': 'mail_inbox',
             'subject_ref': 'mail.message.mail_002', 'rows': MAIL_INBOX_ROWS,
             'visible_assertions': tuple(f'{row["prefix"]}_{field}'
                                         for row in MAIL_INBOX_ROWS for field in MAIL_INBOX_FIELDS)},
    'p018': {'source': 'calendar', 'view_id': 'calendar_list',
             'subject_ref': 'calendar.event.cal_001',
             'visible_assertions': CALENDAR_LIST_FIELDS},
}
# No registered packet is unsupported; the tables stay in place so a future
# packet without a faithful capture path is declared instead of guessed.
EP_0004_UNSUPPORTED = {}
EP_0005_UNSUPPORTED = {}
EPISODE_PROFILES = {
    ('meeting-preparation-001', 'ep_0004'): {
        'scope': EP_0004_SCOPE, 'packets': EP_0004_PACKETS,
        'unsupported': EP_0004_UNSUPPORTED,
    },
    ('meeting-preparation-001', 'ep_0005'): {
        'scope': EP_0005_SCOPE, 'packets': EP_0005_PACKETS,
        'unsupported': EP_0005_UNSUPPORTED,
    },
}
CAPABILITIES = json.loads((ROOT / 'contracts/gui-capabilities.json').read_text(encoding='utf8'))


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object, got {type(value).__name__}')
    return value


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string, got {value!r}')
    return value


def require_timestamp(value, label):
    parsed = pb.maybe_timestamp(value)
    if parsed is None:
        raise ValueError(f'{label} must be an ISO-8601 timestamp with a UTC offset, got {value!r}')
    return parsed


def load_document(path, schema, label):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f'{label} not found: {path}')
    document = require_object(pb.load_json(path, label), label)
    if document.get('schema_version') != schema:
        raise ValueError(f'{label} schema_version must be {schema!r}, '
                         f'got {document.get("schema_version")!r}')
    return document, path


def select_profile(cluster_id, episode_id):
    """Resolve the explicit cluster/episode selection to its frozen profile."""
    require_text(cluster_id, 'cluster_id')
    require_text(episode_id, 'episode_id')
    profile = EPISODE_PROFILES.get((cluster_id, episode_id))
    if profile is None:
        registered = ', '.join(f'{cluster}/{episode}'
                               for cluster, episode in sorted(EPISODE_PROFILES))
        raise ValueError(f'unsupported episode {cluster_id}/{episode_id}: this compiler only has '
                         f'capture profiles for {registered}; refusing to guess')
    return profile


def require_profile_scope(scope, label, profile, world_optional=False):
    scope = require_object(scope, f'{label} episode_scope')
    selected = profile['scope']
    for field, expected in selected.items():
        actual = scope.get(field)
        if actual is None and world_optional and field == 'world_variant_id':
            continue
        if actual != expected:
            raise ValueError(f'unsupported episode scope: {label} {field} is {actual!r}; the '
                             f'explicit capture profile is {selected}; refusing to guess')


def check_scope_field(label, scope, field, expected):
    actual = scope.get(field)
    if actual is not None and actual != expected:
        raise ValueError(f'scope mismatch: {label} {field} is {actual!r}, expected {expected!r}')


def check_packet_table(profile):
    for packet_id, mapping in profile['packets'].items():
        source, view = mapping['source'], mapping['view_id']
        if view not in NAVIGABLE_VIEWS.get(source, frozenset()):
            raise ValueError(f'unsupported view: packet {packet_id} maps to {source}/{view}, which '
                             f'the existing gui_capture.run navigation cannot open faithfully')
        allowed = CAPABILITIES['core_apps'].get(source, {}).get('views', {}).get(view)
        if not isinstance(allowed, list):
            raise ValueError(f'unsupported view: gui-capabilities.json does not declare '
                             f'{source}/{view}')
        unknown = [field for field in mapping['visible_assertions'] if field not in allowed]
        if unknown:
            raise ValueError(f'unsupported visible assertions for {source}/{view}: {unknown}')
        if view == 'mail_inbox':
            rows = mapping.get('rows')
            if not isinstance(rows, tuple) or not rows:
                raise ValueError(f'packet {packet_id} declares the mail_inbox view without rows')
            expected_fields = tuple(f'{row["prefix"]}_{field}' for row in rows
                                    for field in MAIL_INBOX_FIELDS)
            if tuple(mapping['visible_assertions']) != expected_fields:
                raise ValueError(f'packet {packet_id} mail_inbox assertions do not bind its rows')
        elif 'rows' in mapping:
            raise ValueError(f'packet {packet_id} declares rows for a non-list view {source}/{view}')


def gui_packets(gui_plan, profile):
    packets_table = profile['packets']
    unsupported_table = profile['unsupported']
    episode_label = f'{profile["scope"]["cluster_id"]}/{profile["scope"]["episode_id"]}'
    known_packets = set(packets_table) | set(unsupported_table)
    packets = gui_plan.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('GUI plan carries no packets')
    selected, last_sequence = {}, 0
    for position, packet in enumerate(packets, start=1):
        require_object(packet, f'GUI plan packet #{position}')
        if packet.get('tag') != 'gui':
            continue
        packet_id = require_text(packet.get('packet_id'), f'GUI plan packet #{position} packet_id')
        if packet_id not in known_packets:
            raise ValueError(f'unsupported GUI packet {packet_id!r}: the {episode_label} profile has '
                             f'no entry for it; refusing to guess a view or navigation')
        sequence = packet.get('sequence')
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError(f'GUI packet {packet_id!r} sequence must be a positive integer')
        if sequence <= last_sequence:
            raise ValueError(f'GUI packet {packet_id!r} sequence must strictly increase')
        last_sequence = sequence
        source = require_text(packet.get('source'), f'GUI packet {packet_id!r} source')
        if packet_id in unsupported_table:
            mapping = unsupported_table[packet_id]
            raise ValueError(f'unsupported GUI packet {packet_id!r} for {episode_label}: '
                             f'{mapping["reason"]}')
        expected_source = packets_table[packet_id]['source']
        if source != expected_source:
            raise ValueError(f'unsupported GUI packet {packet_id!r} source {source!r}: the '
                             f'{episode_label} mapping expects {expected_source!r}')
        require_timestamp(packet.get('observed_at'), f'GUI packet {packet_id!r} observed_at')
        require_timestamp(packet.get('delivered_at', packet.get('observed_at')),
                          f'GUI packet {packet_id!r} delivered_at')
        require_text(packet.get('text_event'), f'GUI packet {packet_id!r} text_event')
        selected[packet_id] = packet
    if set(selected) != set(packets_table):
        missing = sorted(set(packets_table) - set(selected))
        raise ValueError(f'GUI plan misses frozen {episode_label} GUI packets {missing}; scope '
                         f'drift refuses compilation')
    return selected


def resolve_observation_start(backend_plan, plan_scope):
    """Bind the capture clock to the PB history/cluster observation start.

    The backend plan carries the observation start at the top level, in its
    ``episode_scope`` and in its ``history`` block; every present copy must name
    the same instant, and the instant must not be after the decision point. Only
    then can ``gui_capture.run`` safely rewind the extension simulated clock to
    the start of the PB window and advance packet by packet from there.
    """
    candidates = []
    for label, value in (('backend plan observation_start', backend_plan.get('observation_start')),
                         ('backend plan episode_scope observation_start',
                          plan_scope.get('observation_start'))):
        if value is not None:
            candidates.append((label, require_timestamp(value, label), value))
    history = backend_plan.get('history')
    if isinstance(history, dict) and history.get('observation_start') is not None:
        label = 'backend plan history observation_start'
        candidates.append((label, require_timestamp(history['observation_start'], label),
                           history['observation_start']))
    if not candidates:
        raise ValueError('backend plan carries no observation_start; the capture clock cannot be '
                         'bound to PB history/cluster')
    reference_label, reference, reference_text = candidates[0]
    for label, parsed, text in candidates[1:]:
        if parsed != reference:
            raise ValueError(f'{label} {text!r} does not match {reference_label} '
                             f'{reference_text!r}; refusing an inconsistent capture clock')
    return reference_text, reference


def check_packet_clock_window(packets, observation_start, observation_start_dt, decision_point,
                              decision_dt):
    """Every frozen GUI packet must sit inside the PB clock window in one timezone.

    ``gui_capture.run`` drives the Controller ``advance_time`` from the observation
    start to the decision point; a packet outside that window or on another UTC
    offset can only produce an HTTP 400 or a wrong-screen capture, so it aborts
    compilation instead.
    """
    if decision_dt.utcoffset() != observation_start_dt.utcoffset():
        raise ValueError(f'backend plan decision_point {decision_point!r} timezone offset does not '
                         f'match observation_start {observation_start!r}')
    if observation_start_dt > decision_dt:
        raise ValueError(f'backend plan observation_start {observation_start!r} is after '
                         f'decision_point {decision_point!r}')
    for packet_id, packet in sorted(packets.items(), key=lambda item: item[1]['sequence']):
        observed = pb.maybe_timestamp(packet['observed_at'])
        if observed.utcoffset() != observation_start_dt.utcoffset():
            raise ValueError(f'GUI packet {packet_id!r} observed_at {packet["observed_at"]!r} '
                             f'timezone offset does not match the backend observation_start '
                             f'{observation_start!r}; refusing an inconsistent capture clock')
        if observed < observation_start_dt:
            raise ValueError(f'GUI packet {packet_id!r} observed_at {packet["observed_at"]!r} '
                             f'precedes the backend observation_start {observation_start!r}')
        if observed > decision_dt:
            raise ValueError(f'GUI packet {packet_id!r} observed_at {packet["observed_at"]!r} is '
                             f'after the backend decision_point {decision_point!r}')


def check_gate(gate, gui_path, receipt, receipt_path, profile):
    capture_gate.validate_gate_plan(gate)
    bindings = gate['bindings']
    for name, path, schema in (('gui_plan', gui_path, GUI_PLAN_SCHEMA),
                               ('backend_receipt', receipt_path, RECEIPT_SCHEMA)):
        entry = require_object(bindings.get(name), f'gate plan bindings.{name}')
        if entry.get('schema_version') != schema:
            raise ValueError(f'gate plan bindings.{name}.schema_version must be {schema!r}')
        if entry.get('sha256') != pb.digest(path):
            raise ValueError(f'gate plan bindings.{name}.sha256 does not match {path}; the gate is '
                             f'stale')
        if name == 'backend_receipt' and \
                entry.get('plan_sha256') != receipt['plan_binding']['plan_sha256']:
            raise ValueError('gate plan backend receipt binding plan_sha256 does not match the '
                             'backend receipt')
    by_id = {}
    for target in gate['targets']:
        packet_id = target['packet_id']
        if packet_id not in profile['packets']:
            raise ValueError(f'unsupported GUI packet {packet_id!r}: the gate target is not in the '
                             f'selected capture profile; refusing to guess a view or navigation')
        if packet_id in by_id:
            raise ValueError(f'gate plan target {packet_id!r} is duplicated')
        by_id[packet_id] = target
    if set(by_id) != set(profile['packets']):
        episode_label = f'{profile["scope"]["cluster_id"]}/{profile["scope"]["episode_id"]}'
        raise ValueError(f'gate plan targets cover {sorted(by_id)} and miss frozen {episode_label} '
                         f'GUI packets {sorted(set(profile["packets"]) - set(by_id))}')
    return by_id


def plan_seed_records(backend_plan):
    if backend_plan.get('status') != 'planned':
        raise ValueError(f"backend plan status must be 'planned', got {backend_plan.get('status')!r}")
    if backend_plan.get('device_applied') is not False:
        raise ValueError('backend plan claims device_applied is not false; refusing a completion '
                         'claim')
    sources = require_object(backend_plan.get('sources'), 'backend plan sources')
    records = {}
    for source, entry in sources.items():
        if source not in pb.SUPPORTED_SOURCES:
            raise ValueError(f'backend plan carries unsupported source {source!r}')
        require_object(entry, f'backend plan source {source}')
        for position, item in enumerate(entry.get('seed_records') or [], start=1):
            require_object(item, f'backend plan {source} seed record #{position}')
            ref = require_text(item.get('semantic_ref'),
                               f'backend plan {source} seed #{position} semantic_ref')
            if ref in records:
                raise ValueError(f'backend plan seed record {ref!r} is duplicated')
            seed = require_object(item.get('seed'), f'backend plan {ref} seed')
            readback = require_object(item.get('readback'), f'backend plan {ref} readback')
            require_timestamp(seed.get('at'), f'backend plan {ref} seed.at')
            require_timestamp(readback.get('at'), f'backend plan {ref} readback.at')
            records[ref] = {'source': source, 'item': item,
                            'data': require_object(seed.get('data'),
                                                   f'backend plan {ref} seed data'),
                            'readback_data': require_object(readback.get('data'),
                                                            f'backend plan {ref} readback data')}
    if not records:
        raise ValueError('backend plan declares no seed/readback records')
    return records


def mail_history_replay(ref, change, position):
    """Validate one declared Mail ``unread -> read`` update."""
    label = f'backend plan {ref} history change #{position}'
    require_object(change, label)
    require_timestamp(change.get('effective_at'), f'{label} effective_at')
    updates = require_object(change.get('set'), f'{label} set')
    if set(updates) != {MAIL_HISTORY_FIELD} or \
            updates[MAIL_HISTORY_FIELD] != MAIL_HISTORY_TARGET_STATUS:
        raise ValueError(f'{label} only allows the declared Mail {MAIL_HISTORY_SOURCE_STATUS} -> '
                         f'{MAIL_HISTORY_TARGET_STATUS} {MAIL_HISTORY_FIELD} update, got {updates!r}')
    return {'semantic_ref': ref, 'effective_at': change['effective_at'],
            'set': {MAIL_HISTORY_FIELD: MAIL_HISTORY_TARGET_STATUS}}


def calendar_history_replay(ref, change, position):
    """Validate one declared ``calendar_reminder_minutes`` update.

    The device replay can only apply the declared three-element integer list and
    its ``updated_at`` instant, so any other field, wrong type or instant drift
    aborts compilation instead of being dropped. Unknown entities never reach
    this helper; they are refused by the caller with the missing capability
    named.
    """
    label = f'backend plan {ref} history change #{position}'
    require_object(change, label)
    effective = require_timestamp(change.get('effective_at'), f'{label} effective_at')
    updates = require_object(change.get('set'), f'{label} set')
    unknown = sorted(set(updates) - set(CALENDAR_HISTORY_FIELDS))
    if unknown:
        raise ValueError(f'{label} declares Calendar history fields {unknown} that the device '
                         f'replay cannot apply; only {sorted(CALENDAR_HISTORY_FIELDS)} is supported')
    missing = sorted(set(CALENDAR_HISTORY_FIELDS) - set(updates))
    if missing:
        raise ValueError(f'{label} misses the declared Calendar history fields {missing}')
    minutes = updates['calendar_reminder_minutes']
    if not isinstance(minutes, list) or len(minutes) != CALENDAR_REMINDER_COUNT or any(
            not isinstance(value, int) or isinstance(value, bool) for value in minutes):
        raise ValueError(f'{label} calendar_reminder_minutes must be a three-element integer list, '
                         f'got {minutes!r}')
    updated = require_timestamp(updates['updated_at'], f'{label} updated_at')
    if updated != effective:
        raise ValueError(f'{label} updated_at {updates["updated_at"]!r} does not match its '
                         f'effective_at {change["effective_at"]!r}; the device replay writes '
                         f'last_updated from the effective instant')
    return {'semantic_ref': ref, 'effective_at': change['effective_at'],
            'set': {'calendar_reminder_minutes': list(minutes),
                    'updated_at': updates['updated_at']}}


def reminder_minutes_value(entry, ref):
    value = entry['data'].get('reminder_minutes_before')
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'backend plan {ref} seed reminder_minutes_before must be an integer, '
                         f'got {value!r}')
    return value


def calendar_readback_reminder(entry, ref):
    value = entry['readback_data'].get('reminder_minutes_before')
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'backend plan {ref} readback reminder_minutes_before must be an '
                         f'integer, got {value!r}')
    return value


def declared_history_replays(seed_records):
    """Collect the declared history updates a device adapter can replay.

    Calendar ``calendar_reminder_minutes`` updates (with their ``updated_at``
    instant) and Mail ``status: unread -> read`` updates are accepted. Every
    other source, field, transition or malformed change aborts compilation with
    the missing device capability named. When a Calendar record declares a
    change, the decision-point readback must already carry the final declared
    reminder, proving the state the capture claims to reproduce; a Calendar
    readback that differs from the seeded World without any declared change is
    refused too, because no replay would reproduce it. The updates are returned
    sorted by ``effective_at`` so a packet replays them in history order.
    """
    replays = []
    for ref, entry in seed_records.items():
        changes = entry['item'].get('history_changes') or []
        if not isinstance(changes, list):
            raise ValueError(f'backend plan {ref} history_changes must be a list')
        source = entry['source']
        if not changes:
            if source == 'calendar' and reminder_minutes_value(entry, ref) != \
                    calendar_readback_reminder(entry, ref):
                raise ValueError(
                    f'backend plan {ref} readback reminder_minutes_before '
                    f'{entry["readback_data"]["reminder_minutes_before"]!r} differs from the '
                    f'seeded {entry["data"]["reminder_minutes_before"]!r} without a declared '
                    f'history change; no device replay could reproduce the PB world, so the '
                    f'episode is refused')
            continue
        if source == 'mail':
            seed_status = entry['data'].get(MAIL_HISTORY_FIELD)
            readback_status = entry['readback_data'].get(MAIL_HISTORY_FIELD)
            if seed_status != MAIL_HISTORY_SOURCE_STATUS or \
                    readback_status != MAIL_HISTORY_TARGET_STATUS:
                raise ValueError(f'backend plan {ref} rejects the unknown history transition '
                                 f'{seed_status!r} -> {readback_status!r}; only Mail '
                                 f'{MAIL_HISTORY_SOURCE_STATUS} -> {MAIL_HISTORY_TARGET_STATUS} can '
                                 f'be replayed')
            for position, change in enumerate(changes, start=1):
                replays.append(mail_history_replay(ref, change, position))
        elif source == 'calendar':
            declared = [calendar_history_replay(ref, change, position)
                        for position, change in enumerate(changes, start=1)]
            final = max(declared, key=lambda replay: pb.maybe_timestamp(replay['effective_at']))
            readback_reminder = calendar_readback_reminder(entry, ref)
            if readback_reminder != final['set']['calendar_reminder_minutes'][0]:
                raise ValueError(
                    f'backend plan {ref} declares calendar_reminder_minutes '
                    f'{final["set"]["calendar_reminder_minutes"]!r} but its decision-point readback '
                    f'reminder_minutes_before is {readback_reminder!r}; a changed Calendar readback '
                    f'is required before the history replay is compiled')
            replays.extend(declared)
        else:
            details = []
            for position, change in enumerate(changes, start=1):
                if not isinstance(change, dict):
                    details.append(f'#{position} malformed')
                    continue
                fields = sorted((change.get('set') or {}).keys()) \
                    if isinstance(change.get('set'), dict) else []
                details.append(f'#{position} {fields} at {change.get("effective_at")}')
            raise ValueError(
                f'backend plan {ref} declares {source} history changes ({" ; ".join(details)}) '
                f'that the existing capture path cannot replay or read back: gui_capture.run '
                f'replays only Calendar reminder changes via scripts/gui_calendar.py and the Mail '
                f'{MAIL_HISTORY_SOURCE_STATUS} -> {MAIL_HISTORY_TARGET_STATUS} status transition '
                f'via scripts/gui_mail.py; the device state after the change would diverge from '
                f'the PB world, so this episode is refused until a {source} replay/readback '
                f'adapter exists')
    replays.sort(key=lambda replay: (pb.maybe_timestamp(replay['effective_at']),
                                     replay['semantic_ref']))
    return replays


def assign_history_replays(packets, replays, profile):
    """Attach each declared update to the first compiled packet at/after it takes effect.

    A packet only replays an update once its ``observed_at`` has passed the
    declared ``effective_at``, so the device state in front of that packet
    matches the PB history at that instant. An update that no compiled packet
    can cover before EOF aborts compilation instead of being silently dropped.
    """
    order = sorted(profile['packets'], key=lambda packet_id: packets[packet_id]['sequence'])
    assigned = {packet_id: [] for packet_id in order}
    for replay in replays:
        effective = pb.maybe_timestamp(replay['effective_at'])
        host = next((packet_id for packet_id in order
                     if pb.maybe_timestamp(packets[packet_id]['observed_at']) >= effective), None)
        if host is None:
            raise ValueError(f'declared history change {replay["semantic_ref"]!r} at '
                             f'{replay["effective_at"]} is not covered by any compiled GUI packet; '
                             f'refusing an incomplete capture plan')
        assigned[host].append(dict(replay))
    return assigned


def receipt_records(receipt, seed_records):
    if receipt.get('status') != 'ok':
        raise ValueError(f"backend receipt status must be 'ok', got {receipt.get('status')!r}")
    if receipt.get('device_applied') is not True:
        raise ValueError('backend receipt device_applied must be true; refusing to compile a capture '
                         'plan before the backend seed/readback succeeded')
    records = receipt.get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('backend receipt carries no seed/readback records; a completed apply is '
                         'required')
    index = {}
    for position, record in enumerate(records, start=1):
        require_object(record, f'backend receipt record #{position}')
        ref = require_text(record.get('semantic_ref'),
                           f'backend receipt record #{position} semantic_ref')
        source = require_text(record.get('source'), f'backend receipt record {ref!r} source')
        if ref in index:
            raise ValueError(f'backend receipt record {ref!r} is duplicated')
        if ref not in seed_records:
            raise ValueError(f'backend receipt record {ref!r} is not in the backend plan; the '
                             f'receipt does not match the plan')
        if seed_records[ref]['source'] != source:
            raise ValueError(f'backend receipt record {ref!r} source {source!r} does not match the '
                             f'backend plan source {seed_records[ref]["source"]!r}')
        contract = pb.ADAPTERS[source]
        adapter = require_object(record.get('adapter'), f'backend receipt record {ref!r} adapter')
        for field in ('module', 'seed', 'read'):
            if adapter.get(field) != contract[field]:
                raise ValueError(f'backend receipt record {ref!r} adapter {field} '
                                 f'{adapter.get(field)!r} does not match {contract[field]!r}')
        if record.get('status') != 'ok':
            raise ValueError(f'backend receipt record {ref!r} status must be ok')
        index[ref] = record
    missing = sorted(set(seed_records) - set(index))
    if missing:
        raise ValueError(f'backend receipt misses planned seed records {missing}; the apply did not '
                         f'complete')
    return index


def build_fixture(receipt_index, seed_records, profile):
    """Seed the device with the PB observation_start state, never the readback."""
    ku_records = [{'app_id': seed_records[ref]['source'],
                   'entity_type': ENTITY_TYPES[seed_records[ref]['source']],
                   'semantic_ref': ref, 'data': seed_records[ref]['data']}
                  for ref in receipt_index]
    return {'episode_scope': profile['scope']['episode_id'], 'common_records': [],
            'world_deltas': [], 'ku_records': ku_records}


def visible_value(source, view, field, data, label):
    if source == 'calendar':
        if field not in ('title', 'location', 'description', 'start_at', 'end_at'):
            raise ValueError(f'unsupported calendar {view} visible assertion {field!r}: no frozen '
                             f'mapping exists for this episode')
        if field in ('start_at', 'end_at'):
            # The Fossify editor renders 12-hour time with a zero-padded hour
            # ("09:00 AM"), so the visible assertion has to carry the same text the
            # device shows for both readiness and machine QA node matching.
            return require_timestamp(data.get(field), f'{label} {field}').strftime('%I:%M %p')
        return require_text(data.get(field), f'{label} {field}')
    headers = require_object(data.get('headers'), f'{label} headers')
    mapping = {'subject': headers.get('subject'), 'from': headers.get('from'),
               'body': data.get('body')}
    if field not in mapping:
        raise ValueError(f'unsupported {source} {view} visible assertion {field!r}: no frozen '
                         f'mapping exists for this episode')
    return require_text(mapping[field], f'{label} {field}')


def calendar_list_visible(data, label):
    """Expected Calendar day-view nodes: header plus the unique event row.

    The device renders the day header as ``September 14 (Mon)`` and the row time
    as zero-padded 12-hour text joined by `` - `` (probed on the real APK), so the
    expected values carry exactly that text for both readiness and node QA.
    """
    start = require_timestamp(data.get('start_at'), f'{label} start_at')
    end = require_timestamp(data.get('end_at'), f'{label} end_at')
    return {'day_label': start.strftime('%B %-d (%a)'),
            'event_title': require_text(data.get('title'), f'{label} title'),
            'event_time': f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}",
            'event_description': require_text(data.get('description'), f'{label} description')}


def mail_inbox_visible(rows, records, label):
    """Expected Mail Inbox row nodes for every declared row.

    A row is bound by its exact subject text; the sender and the full snippet
    then have to live in the same row container on the device. Inbox rows carry
    no resource ids, so only these exact text nodes are asserted; the unread
    bold/read gray weight and the bottom count badge are never compiled.
    """
    expected = {}
    for row in rows:
        ref = row['subject_ref']
        data = records.get(('mail', ref))
        if data is None:
            raise ValueError(f'{label} row {row["prefix"]!r} subject {ref!r} is not in the '
                             f'backend receipt fixture; refusing an incomplete capture plan')
        headers = require_object(data.get('headers'), f'{label} {ref} headers')
        expected[f'{row["prefix"]}_sender'] = require_text(headers.get('from'),
                                                           f'{label} {ref} from')
        expected[f'{row["prefix"]}_subject'] = require_text(headers.get('subject'),
                                                            f'{label} {ref} subject')
        expected[f'{row["prefix"]}_snippet'] = require_text(data.get('body'),
                                                            f'{label} {ref} body')
    return expected


def compile_packet(packet, mapping, fixture, history_replays=()):
    packet_id = packet['packet_id']
    records = {(record['app_id'], record['semantic_ref']): record['data']
               for record in fixture['ku_records']}
    data = records.get((mapping['source'], mapping['subject_ref']))
    if data is None:
        raise ValueError(f'GUI packet {packet_id!r} subject {mapping["subject_ref"]!r} is not in the '
                         f'backend receipt fixture; refusing an incomplete capture plan')
    label = f'packet {packet_id} {mapping["subject_ref"]}'
    if mapping['view_id'] == 'calendar_list':
        expected = calendar_list_visible(data, label)
    elif mapping['view_id'] == 'mail_inbox':
        expected = mail_inbox_visible(mapping['rows'], records, label)
    else:
        expected = {field: visible_value(mapping['source'], mapping['view_id'], field, data, label)
                    for field in mapping['visible_assertions']}
    if set(expected) != set(mapping['visible_assertions']):
        raise ValueError(f'GUI packet {packet_id!r} compiled assertions {sorted(expected)} do not '
                         f'match the frozen mapping {sorted(mapping["visible_assertions"])}')
    compiled = {key: packet[key] for key in ('packet_id', 'sequence', 'tag', 'source', 'observed_at',
                                             'delivered_at', 'text_event')}
    if history_replays:
        compiled['history_replays'] = [dict(replay) for replay in history_replays]
    compiled['capture'] = {
        'surface': 'app_screen', 'app_id': mapping['source'], 'view_id': mapping['view_id'],
        'subject_ref': mapping['subject_ref'], 'visible_assertions': list(mapping['visible_assertions']),
        'expected_visible': expected, 'frames': [{'frame_id': f'{packet_id}{FRAME_SUFFIX}'}],
        'timeout_seconds': TIMEOUT_SECONDS,
    }
    if mapping['source'] == 'calendar':
        # Private, PB-only navigation: tap the event date cell (and, for the editor
        # packets, the unique event item) directly instead of typing the Chinese
        # title into the Calendar search box. The explicit profile/scope checks
        # guarantee this field only ever lands in a registered PB plan, and the
        # pilot capture compiler rejects unknown capture keys, so old plans never
        # carry it.
        compiled['capture']['navigation_mode'] = CALENDAR_DIRECT_NAVIGATION
    return compiled


def compile_plan(gui_plan_path, backend_plan_path, receipt_path, gate_plan_path,
                 cluster_id, episode_id):
    profile = select_profile(cluster_id, episode_id)
    check_packet_table(profile)
    gui_plan, gui_path = load_document(gui_plan_path, GUI_PLAN_SCHEMA, 'GUI plan')
    backend_plan, backend_path = load_document(backend_plan_path, BACKEND_PLAN_SCHEMA, 'backend plan')
    receipt, receipt_file = load_document(receipt_path, RECEIPT_SCHEMA, 'backend receipt')
    gate, gate_path = load_document(gate_plan_path, GATE_SCHEMA, 'gate plan')

    require_profile_scope(gui_plan, 'GUI plan', profile, world_optional=True)
    plan_scope = require_object(backend_plan.get('episode_scope'), 'backend plan episode_scope')
    require_profile_scope(plan_scope, 'backend plan', profile)
    binding = require_object(receipt.get('plan_binding'), 'backend receipt plan_binding')
    receipt_scope = require_object(binding.get('episode_scope'),
                                   'backend receipt plan_binding episode_scope')
    require_profile_scope(receipt_scope, 'backend receipt', profile)
    require_profile_scope(gate.get('episode_scope'), 'gate plan', profile)

    backend_sha = pb.digest(backend_path)
    if binding.get('schema_version') != BACKEND_PLAN_SCHEMA:
        raise ValueError(f'backend receipt plan_binding schema_version must be '
                         f'{BACKEND_PLAN_SCHEMA!r}, got {binding.get("schema_version")!r}')
    if binding.get('plan_sha256') != backend_sha:
        raise ValueError('backend plan bytes do not match the backend receipt plan_sha256; the '
                         'receipt is stale')
    targets = check_gate(gate, gui_path, receipt, receipt_file, profile)

    decision_point = require_text(backend_plan.get('decision_point'),
                                  'backend plan decision_point')
    require_timestamp(decision_point, 'backend plan decision_point')
    check_scope_field('GUI plan', gui_plan, 'decision_point', decision_point)
    check_scope_field('backend receipt', receipt_scope, 'decision_point', decision_point)
    check_scope_field('gate plan', gate.get('episode_scope'), 'decision_point', decision_point)
    if plan_scope.get('timezone') != DEVICE_TIMEZONE:
        raise ValueError(f'unsupported backend plan timezone {plan_scope.get("timezone")!r}: this '
                         f'compiler only supports {DEVICE_TIMEZONE!r}')
    check_scope_field('backend receipt', receipt_scope, 'timezone', DEVICE_TIMEZONE)
    check_scope_field('gate plan', gate.get('episode_scope'), 'timezone', DEVICE_TIMEZONE)
    for field in ('world_status', 'world_hash'):
        if plan_scope.get(field) is not None:
            check_scope_field('backend receipt', receipt_scope, field, plan_scope[field])
            check_scope_field('gate plan', gate.get('episode_scope'), field, plan_scope[field])

    packets = gui_packets(gui_plan, profile)
    observation_start, observation_start_dt = resolve_observation_start(backend_plan, plan_scope)
    check_packet_clock_window(packets, observation_start, observation_start_dt, decision_point,
                              require_timestamp(decision_point, 'backend plan decision_point'))
    seed_records = plan_seed_records(backend_plan)
    receipt_index = receipt_records(receipt, seed_records)
    fixture = build_fixture(receipt_index, seed_records, profile)
    history_replays = assign_history_replays(packets, declared_history_replays(seed_records),
                                             profile)
    for packet_id, target in targets.items():
        packet = packets[packet_id]
        for field in ('sequence', 'source', 'observed_at'):
            if target.get(field) != packet.get(field):
                raise ValueError(f'gate target {packet_id!r} {field} {target.get(field)!r} differs '
                                 f'from the GUI plan packet {packet.get(field)!r}')

    ordered = sorted(profile['packets'], key=lambda packet_id: packets[packet_id]['sequence'])
    compiled = [compile_packet(packets[packet_id], profile['packets'][packet_id], fixture,
                               history_replays[packet_id])
                for packet_id in ordered]
    replay_count = sum(len(items) for items in history_replays.values())
    eof = require_timestamp(decision_point, 'backend plan decision_point')
    for packet in compiled:
        delivered = require_timestamp(packet['delivered_at'], f'packet {packet["packet_id"]} '
                                                              f'delivered_at')
        if delivered > eof:
            raise ValueError(f'GUI packet {packet["packet_id"]!r} is delivered after the decision '
                             f'point; refusing an incomplete capture plan')
    unsupported = [{'packet_id': packet_id, 'sequence': packets[packet_id]['sequence'],
                    'source': packets[packet_id]['source'],
                    'surface': profile['unsupported'][packet_id]['surface'],
                    'reason': profile['unsupported'][packet_id]['reason']}
                   for packet_id in sorted(profile['unsupported'],
                                           key=lambda item: packets[item]['sequence'])]
    source_assets = {
        'gui_plan': {'schema_version': GUI_PLAN_SCHEMA, 'sha256': pb.digest(gui_path)},
        'backend_plan': {'schema_version': BACKEND_PLAN_SCHEMA, 'sha256': backend_sha},
        'backend_receipt': {'schema_version': RECEIPT_SCHEMA, 'sha256': pb.digest(receipt_file)},
        'gate_plan': {'schema_version': GATE_SCHEMA, 'sha256': pb.digest(gate_path)},
    }
    plan = {
        'schema_version': PLAN_SCHEMA,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'command': 'compile',
        'status': 'compiled',
        'episode_scope': {'cluster_id': profile['scope']['cluster_id'],
                          'episode_id': profile['scope']['episode_id'],
                          'passive_id': profile['scope']['passive_id'],
                          'world_variant_id': profile['scope']['world_variant_id'],
                          'track': plan_scope.get('track'), 'decision_point': decision_point,
                          'timezone': DEVICE_TIMEZONE},
        'cluster_id': profile['scope']['cluster_id'], 'episode_id': profile['scope']['episode_id'],
        'passive_id': profile['scope']['passive_id'],
        'world_variant_id': profile['scope']['world_variant_id'],
        'baseline_ref': BASELINE_REF, 'device_timezone': DEVICE_TIMEZONE,
        'pb_clock_binding': {'observation_start': observation_start,
                             'decision_point': decision_point, 'timezone': DEVICE_TIMEZONE,
                             'source': PB_CLOCK_SOURCE},
        'initial_simulated_time': observation_start,
        'source_assets': source_assets, 'fixture': fixture, 'packets': compiled,
        'unsupported_packets': unsupported,
        'counts': {'packets': len(compiled), 'unsupported_packets': len(unsupported),
                   'fixture_records': len(fixture['ku_records']), 'frames': len(compiled),
                   'history_replays': replay_count},
        'checks': {
            'gate_status': GATE_STATUS, 'gate_capture_state': PENDING_CAPTURE,
            'receipt_status': receipt['status'], 'receipt_device_applied': True,
            'hashes_verified': 4, 'scope_matches': True,
            'gui_plan_sha256': source_assets['gui_plan']['sha256'],
            'backend_plan_sha256': backend_sha,
            'backend_receipt_sha256': source_assets['backend_receipt']['sha256'],
            'gate_plan_sha256': source_assets['gate_plan']['sha256'],
            'packets_executable': len(compiled), 'packets_unsupported': len(unsupported),
            'history_replays': replay_count,
            'images_read': 0, 'images_copied': 0, 'device_writes': 0,
        },
        'eof_at': decision_point,
    }
    leaks = pb.private_key_paths(plan)
    if leaks:
        raise ValueError(f'private GT/Need/Oracle fields leaked into the capture plan: {leaks}')
    absolutes = pb.absolute_value_paths(plan)
    if absolutes:
        raise ValueError(f'local absolute paths leaked into the capture plan: {absolutes}')
    return plan


def command_compile(args):
    plan = compile_plan(args.gui_plan, args.backend_plan, args.backend_receipt, args.gate_plan,
                        args.cluster_id, args.episode_id)
    output = Path(args.output).expanduser().resolve()
    if output.is_dir():
        raise ValueError(f'--output is a directory: {output}')
    target = pb.write_json(output, plan)
    print(json.dumps({'status': 'ok', 'command': 'compile', 'plan_status': plan['status'],
                      'cluster_id': plan['cluster_id'], 'episode_id': plan['episode_id'],
                      'passive_id': plan['passive_id'], 'world_variant_id': plan['world_variant_id'],
                      'packets': plan['counts']['packets'],
                      'unsupported_packets': plan['counts']['unsupported_packets'],
                      'fixture_records': plan['counts']['fixture_records'],
                      'history_replays': plan['counts']['history_replays'],
                      'eof_at': plan['eof_at'], 'output': str(target)}, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pb_gui_capture_compile.py',
        description='Compile one explicitly selected PB GUI episode into a private capture plan for '
                    'scripts/gui_capture.py::run (fail closed).')
    subparsers = parser.add_subparsers(dest='command', required=True)
    compile_parser = subparsers.add_parser(
        'compile', help='verify the PB plan/receipt/gate chain and write the private capture plan')
    compile_parser.add_argument('--cluster-id', required=True,
                                help='PB cluster id, e.g. meeting-preparation-001')
    compile_parser.add_argument('--episode-id', required=True,
                                help='PB episode id, e.g. ep_0004; must have a registered profile')
    compile_parser.add_argument('--gui-plan', type=Path, required=True,
                                help='round-1 private GUI plan produced by pb_gui_bridge.py')
    compile_parser.add_argument('--backend-plan', type=Path, required=True,
                                help='round-2 backend plan produced by pb_backend_plan.py')
    compile_parser.add_argument('--backend-receipt', type=Path, required=True,
                                help='round-2 success receipt produced by pb_backend_apply.py apply')
    compile_parser.add_argument('--gate-plan', type=Path, required=True,
                                help='round-3 gate plan produced by pb_gui_capture_gate.py preflight')
    compile_parser.add_argument('--output', type=Path, required=True,
                                help='write the private capture plan here')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return command_compile(args)
    except ValueError as error:
        print(f'pb_gui_capture_compile.py: error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
