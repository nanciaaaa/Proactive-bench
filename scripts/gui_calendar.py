"""Fossify Calendar capture adapter for a scoped, same-day event pilot."""
import hashlib
import json
import shlex
import time
from datetime import datetime

from gui_contract import timestamp

DB = '/data/user/0/org.fossify.calendar/databases/events.db'
PACKAGE = 'org.fossify.calendar'
HISTORY_FIELDS = frozenset({'calendar_reminder_minutes', 'updated_at'})
REMINDER_COLUMNS = ('reminder_1_minutes', 'reminder_2_minutes', 'reminder_3_minutes')
REMINDER_COUNT = 3
EDITOR_MARKER = 'Edit Event'
# The editor renders every assertion in its own event field. Anchoring the
# readiness check to these resource ids is required because the generic capture
# helper counts substring occurrences across the whole screen: the PB event
# title (``项目评审 R7K2``) is also a prefix of its description, so a screen-wide
# uniqueness check can never pass even when the editor is fully visible.
EDITOR_FIELDS = {'title': 'event_title', 'location': 'event_location',
                 'description': 'event_description', 'start_at': 'event_start_time',
                 'end_at': 'event_end_time'}
# Day-view list nodes. ``day_holder`` and its ``top_value`` header prove the day
# view; the month grid stays in the same uiautomator dump, so nothing outside the
# day holder may satisfy the assertion.
DAY_HOLDER = f'{PACKAGE}:id/day_holder'
EVENT_HOLDER = f'{PACKAGE}:id/event_item_holder'
LIST_FIELDS = {'day_label': 'top_value', 'event_title': 'event_item_title',
               'event_time': 'event_item_time', 'event_description': 'event_item_description'}


def event_key(episode, ref):
    return 'gui-' + hashlib.sha256(f'{episode}:{ref}'.encode()).hexdigest()[:24]


def query(adb, serial, sql):
    output = adb(serial, 'shell', f'su 0 sqlite3 -json {DB} {shlex.quote(sql)}')
    return json.loads(output or b'[]')


def statement(adb, serial, sql):
    output = adb(serial, 'shell', f'su 0 sqlite3 {DB} {shlex.quote(sql)}')
    return output.decode(errors='replace').strip() if isinstance(output, bytes) else str(output).strip()


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


def history_minutes(value):
    """Validate the only reminder shape PB history may declare."""
    if not isinstance(value, list) or len(value) != REMINDER_COUNT or any(
            not isinstance(item, int) or isinstance(item, bool) for item in value):
        raise RuntimeError(f'Calendar history replay requires calendar_reminder_minutes to be a '
                           f'three-element integer list, got {value!r}')
    return list(value)


def history_timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f'Calendar history replay {label} must be a nonempty ISO-8601 '
                           f'timestamp, got {value!r}')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise RuntimeError(f'Calendar history replay {label} is not an ISO-8601 timestamp: '
                           f'{value!r}') from None
    if parsed.utcoffset() is None:
        raise RuntimeError(f'Calendar history replay {label} must carry a UTC offset: {value!r}')
    return parsed


def apply_history_change(adb, serial, episode, record, effective_at):
    """Replay one declared PB Calendar reminder change on the scoped event row.

    ``record`` must carry a nonempty ``semantic_ref`` and a ``data`` object that
    holds exactly the two declared PB fields: ``calendar_reminder_minutes`` (a
    three-element integer list) and ``updated_at`` (the change instant, equal to
    the packet's ``effective_at``). Unknown fields, another entity type, wrong
    types, a duplicated or ambiguous ``import_id`` and a non-single affected row
    are all refused, so no PB update can be silently dropped or widened. The
    UPDATE only touches the episode's single ``import_id`` row, and a separate
    SELECT then reads the three reminder columns plus ``last_updated`` back and
    compares them with the declared change.
    """
    if not isinstance(record, dict):
        raise RuntimeError('Calendar history replay record must be an object')
    ref = record.get('semantic_ref')
    if not isinstance(ref, str) or not ref.strip():
        raise RuntimeError('Calendar history replay needs a nonempty semantic_ref')
    entity = record.get('entity_type')
    if entity is not None and entity != 'events':
        raise RuntimeError(f'Calendar history replay rejects entity type {entity!r}')
    data = record.get('data')
    if not isinstance(data, dict):
        raise RuntimeError('Calendar history replay record data must be an object')
    unknown = sorted(set(data) - HISTORY_FIELDS)
    if unknown:
        raise RuntimeError(f'Calendar history replay rejects unknown fields {unknown}')
    missing = sorted(HISTORY_FIELDS - set(data))
    if missing:
        raise RuntimeError(f'Calendar history replay misses declared fields {missing}')
    minutes = history_minutes(data['calendar_reminder_minutes'])
    updated_at = history_timestamp(data['updated_at'], 'updated_at')
    effective = history_timestamp(effective_at, 'effective_at')
    if updated_at != effective:
        raise RuntimeError(f'Calendar history replay updated_at {data["updated_at"]!r} does not '
                           f'match the declared effective_at {effective_at!r}')
    key = event_key(episode, ref)
    if len(query(adb, serial, f'SELECT id FROM events WHERE import_id={quoted(key)};')) != 1:
        raise RuntimeError('Calendar history replay identity is missing or ambiguous')
    last_updated = int(effective.timestamp() * 1000)
    changed = statement(adb, serial,
                        f'UPDATE events SET reminder_1_minutes={minutes[0]},'
                        f'reminder_2_minutes={minutes[1]},reminder_3_minutes={minutes[2]},'
                        f'last_updated={last_updated} WHERE import_id={quoted(key)};'
                        f'SELECT changes();')
    try:
        affected = int(changed.splitlines()[-1])
    except (IndexError, ValueError):
        raise RuntimeError(f'Calendar history replay could not prove a single affected row: '
                           f'{changed!r}') from None
    if affected != 1:
        raise RuntimeError(f'Calendar history replay must affect exactly one row, affected '
                           f'{affected}')
    rows = query(adb, serial, f'SELECT reminder_1_minutes,reminder_2_minutes,'
                              f'reminder_3_minutes,last_updated FROM events '
                              f'WHERE import_id={quoted(key)};')
    if len(rows) != 1:
        raise RuntimeError('Calendar history readback identity is missing or ambiguous')
    expected = dict(zip(REMINDER_COLUMNS, minutes), last_updated=last_updated)
    if any(rows[0].get(column) != value for column, value in expected.items()):
        raise RuntimeError(f'Calendar history readback {rows[0]!r} differs from the declared '
                           f'change {expected!r}')
    return {'semantic_ref': ref, 'import_id': key, **expected}


def read_event(adb, serial, episode, record, device_timezone):
    key = event_key(episode, record['semantic_ref'])
    rows = query(adb, serial, f'SELECT id,title,start_ts,end_ts,location,description,reminder_1_minutes,'
               f'source,time_zone,import_id FROM events WHERE import_id={quoted(key)};')
    if len(rows) != 1:
        raise RuntimeError('Calendar event identity is missing or ambiguous')
    row, data = rows[0], record['data']
    expected = {'title': data['title'], 'start_ts': int(timestamp(data['start_at']).timestamp()),
                'end_ts': int(timestamp(data['end_at']).timestamp()), 'location': data['location'],
                'description': data['description'], 'reminder_1_minutes': data['reminder_minutes_before'],
                'source': data['source'], 'time_zone': device_timezone, 'import_id': key}
    if any(row[field] != value for field, value in expected.items()):
        raise RuntimeError('Calendar event differs from seeded authority')
    return row


def seed_event(adb, serial, episode, record, device_timezone, observed_at):
    data = record['data']
    key = event_key(episode, record['semantic_ref'])
    if query(adb, serial, f'SELECT id FROM events WHERE import_id={quoted(key)};'):
        raise RuntimeError('Calendar event exists before seed')
    start, end = (int(timestamp(data[field]).timestamp()) for field in ('start_at', 'end_at'))
    columns = ('start_ts', 'end_ts', 'title', 'location', 'description', 'reminder_1_minutes',
               'reminder_2_minutes', 'reminder_3_minutes', 'reminder_1_type', 'reminder_2_type',
               'reminder_3_type', 'repeat_interval', 'repeat_rule', 'repeat_limit',
               'repetition_exceptions', 'attendees', 'import_id', 'time_zone', 'flags', 'event_type',
               'parent_id', 'last_updated', 'source', 'availability', 'access_level', 'color', 'type', 'status')
    values = (start, end, data['title'], data['location'], data['description'], data['reminder_minutes_before'],
              -1, -1, 0, 0, 0, 0, 0, 0, '[]', '[]', key, device_timezone, 0, 1, 0,
              int(timestamp(observed_at).timestamp() * 1000), data['source'], 0, 0, 0, 0, 1)
    sql_values = ','.join(quoted(value) if isinstance(value, str) else str(value) for value in values)
    query(adb, serial, f'INSERT INTO events({",".join(columns)}) VALUES ({sql_values});')
    return read_event(adb, serial, episode, record, device_timezone)


def assert_absent(adb, serial, episode, records):
    for record in records:
        key = event_key(episode, record['semantic_ref'])
        if query(adb, serial, f'SELECT id FROM events WHERE import_id={quoted(key)};'):
            raise RuntimeError('Calendar event remains after reset')


def ready_event_editor(ui_text, serial, expected, timeout):
    """Wait for the real Fossify event editor and assert each visible field.

    ``expected`` must carry exactly the frozen calendar editor assertions. Each
    field is matched by exact text on the resource id that renders it, so the
    title/description overlap stays unambiguous and the device's own ``hh:mm a``
    time text (for example ``09:00 AM``) is the authoritative value.
    """
    unsupported = sorted(set(expected) - set(EDITOR_FIELDS))
    if unsupported:
        raise RuntimeError(f'unsupported Calendar visible assertions {unsupported}')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = [node for node in root.iter('node') if node.attrib.get('package') == PACKAGE]
        marker = any(node.attrib.get('text') == EDITOR_MARKER for node in nodes)
        matches = {field: [node for node in nodes
                           if node.attrib.get('resource-id') == f'{PACKAGE}:id/{EDITOR_FIELDS[field]}'
                           and node.attrib.get('text') == value]
                   for field, value in expected.items()}
        if marker and all(len(field_nodes) == 1 for field_nodes in matches.values()):
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {field: True for field in expected}}
        time.sleep(.5)
    raise RuntimeError(f'calendar popup not visible within {timeout}s')


def child_text(node, resource):
    """Exact text of a node's single child with ``resource``, else ``None``."""
    matches = [child for child in node.iter('node')
               if child.attrib.get('resource-id') == f'{PACKAGE}:id/{resource}']
    if len(matches) != 1:
        return None
    return matches[0].attrib.get('text')


def ready_calendar_day_list(ui_text, serial, expected, timeout):
    """Wait for the real Calendar day list and assert its exact text nodes.

    The day view is proven by the unique ``day_holder`` container whose
    ``top_value`` header carries the expected day. The event row is then bound to
    one ``event_item_holder`` whose ``event_item_title`` / ``event_item_time`` /
    ``event_item_description`` children all carry the exact expected text, so a
    month grid, a search result page or another event row cannot satisfy the
    assertion. The capture never taps the event item itself.
    """
    unsupported = sorted(set(expected) - set(LIST_FIELDS))
    if unsupported:
        raise RuntimeError(f'unsupported Calendar list visible assertions {unsupported}')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = [node for node in root.iter('node') if node.attrib.get('package') == PACKAGE]
        holders = [node for node in nodes if node.attrib.get('resource-id') == DAY_HOLDER]
        day_nodes = [node for node in nodes
                     if node.attrib.get('resource-id') == f'{PACKAGE}:id/top_value'
                     and node.attrib.get('text') == expected['day_label']]
        if len(holders) == 1 and len(day_nodes) == 1 and \
                any(node is day_nodes[0] for node in holders[0].iter('node')):
            rows = [node for node in holders[0].iter('node')
                    if node.attrib.get('resource-id') == EVENT_HOLDER]
            matches = [row for row in rows
                       if child_text(row, 'event_item_title') == expected['event_title']
                       and child_text(row, 'event_item_time') == expected['event_time']
                       and child_text(row, 'event_item_description') == expected['event_description']]
            if len(matches) == 1:
                return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                        'visible_assertions': {field: True for field in expected}}
        time.sleep(.5)
    raise RuntimeError(f'calendar day list not visible within {timeout}s')
