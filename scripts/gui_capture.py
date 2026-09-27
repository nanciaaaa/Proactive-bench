#!/usr/bin/env python3
"""Offline, single-device GUI capture for verified extension popup packets."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gui_contract import CAPABILITIES, ROOT
from gui_calendar import (PACKAGE as CALENDAR_PACKAGE,
                          apply_history_change as calendar_apply_history_change,
                          assert_absent, read_event, ready_calendar_day_list,
                          ready_event_editor, seed_event)
from gui_mail import (PACKAGE as MAIL_PACKAGE, apply_history_change,
                      assert_absent as mail_absent, read_mail, read_mail_state,
                      ready_mail_inbox, seed_mail)
from gui_clock import PACKAGE as CLOCK_PACKAGE, ACTIVITY as CLOCK_ACTIVITY, assert_absent as clock_absent, read_alarm, ready_alarm, seed_alarm
from gui_contacts import PACKAGE as CONTACTS_PACKAGE, assert_absent as contacts_absent, read_contact, ready_contact, seed_contact
from gui_files import PACKAGE as FILES_PACKAGE, READER_PACKAGE, assert_absent as files_absent, read_file, ready_docreader, ready_file, seed_file
from gui_messages import PACKAGE as MESSAGES_PACKAGE, assert_absent as messages_absent, read_message, ready_message, seed_message


def png_size(raw):
    if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise ValueError('invalid PNG')
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', raw[16:29])
    if not width or not height or depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        raise ValueError('unsupported PNG screenshot format')
    row_size = 1 + width * (3 if color == 2 else 4)
    if row_size * height > 100_000_000:
        raise ValueError('PNG exceeds screenshot size limit')
    offset, image_data, ended = 8, bytearray(), False
    while offset + 12 <= len(raw):
        size = struct.unpack('>I', raw[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(raw):
            raise ValueError('truncated PNG')
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', raw[end - 4:end])[0]
        if zlib.crc32(kind + data) != crc:
            raise ValueError('PNG checksum mismatch')
        if kind == b'IDAT':
            image_data.extend(data)
        if kind == b'IEND':
            ended = True
            break
        offset = end
    if not ended or offset + 12 != len(raw) or not image_data:
        raise ValueError('PNG image data missing or trailing bytes')
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(image_data, row_size * height + 1)
    if not decoder.eof or decoder.unused_data or len(pixels) != row_size * height or any(
            pixels[row * row_size] > 4 for row in range(height)):
        raise ValueError('PNG scanlines are incomplete or corrupt')
    return width, height


def command(*args, timeout=30):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors='replace')[:300]
        raise RuntimeError(f'{" ".join(args[:6])} failed ({result.returncode}): {detail}')
    return result.stdout


RESET_CAPTURE_TIMEOUT_SECONDS = 300
# Kept below the caller timeout so reset-instance.sh either finishes cleanly or
# fails on its own single deadline; the outer kill is only a last-resort guard.
RESET_TOTAL_BUDGET_SECONDS = 240


def reset_log_tail(instance, lines=6):
    path = ROOT / 'runtime' / 'instances' / instance / 'reset.log'
    try:
        rows = [row.strip() for row in path.read_text(errors='replace').splitlines() if row.strip()]
    except OSError:
        return ''
    return ' | '.join(rows[-lines:])


def reset_instance(instance):
    """Run the reset script under one deadline and surface the exact failed phase."""
    argv = (str(ROOT / 'scripts/reset-instance.sh'), instance)
    environment = dict(os.environ, RESET_TOTAL_BUDGET_SECONDS=str(RESET_TOTAL_BUDGET_SECONDS))
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, start_new_session=True)
    try:
        _, stderr = process.communicate(timeout=RESET_CAPTURE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.communicate()
        raise RuntimeError(f'reset-instance.sh exceeded {RESET_CAPTURE_TIMEOUT_SECONDS}s capture timeout '
                           f'(internal budget {RESET_TOTAL_BUDGET_SECONDS}s); {reset_log_tail(instance)}') from None
    if process.returncode:
        detail = ' '.join((stderr or b'').decode(errors='replace').strip().splitlines()[-3:])[:300]
        tail = reset_log_tail(instance)
        raise RuntimeError(f'reset-instance.sh failed ({process.returncode}): {detail}'
                           + (f'; phase log: {tail}' if tail else ''))
    return reset_log_tail(instance)


def request(base, key, path, data=None):
    headers = {'Content-Type': 'application/json', 'X-Runner-Key': key}
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers, method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=30) as response:
        value = json.load(response)
    if value.get('status') not in (None, 'ok') or value.get('success') is False:
        raise RuntimeError(f'{path}: {value.get("error", value)}')
    return value


def adb(serial, *args, timeout=30):
    if args[:2] == ('emu', 'sms') and os.environ.get('KU_EMULATOR_CONTAINER'):
        return command('docker', 'exec', os.environ['KU_EMULATOR_CONTAINER'], 'adb', *args, timeout=timeout)
    return command('adb', '-s', serial, *args, timeout=timeout)


DEVICE_ENV_FIELDS = ('resolution', 'density', 'font_scale', 'locale', 'theme', 'timezone')

PB_CLOCK_BINDING = 'pb_clock_binding'
# Compiled private PB capture plan emitted by ``pb_gui_capture_compile.py``.
PRIVATE_PLAN_SCHEMA = 'pb-gui-private-capture-plan.v1'
PRIVATE_PLAN_FIELDS = frozenset({
    'schema_version', 'generated_at', 'command', 'status', 'episode_scope', 'cluster_id',
    'episode_id', 'passive_id', 'world_variant_id', 'baseline_ref', 'device_timezone',
    'pb_clock_binding', 'initial_simulated_time', 'source_assets', 'fixture', 'packets',
    'unsupported_packets', 'counts', 'checks', 'eof_at'})
PRIVATE_PACKET_FIELDS = frozenset({
    'packet_id', 'sequence', 'tag', 'source', 'observed_at', 'delivered_at', 'text_event',
    'capture'})
HEX64 = re.compile(r'^[0-9a-f]{64}$')
# Private PB-only calendar navigation: the compiled ep_0004 plan opts out of the
# legacy Chinese-title search (``adb shell input text`` aborts Android with a
# NullPointerException) and taps the unique event item text directly instead.
CALENDAR_NAVIGATION_MODE = 'navigation_mode'
CALENDAR_DIRECT_NAVIGATION = 'direct_event'
PB_CLOCK_SOURCE = 'PB history/cluster'
PB_CLOCK_FIELDS = ('observation_start', 'decision_point', 'timezone', 'source')


def clock_timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty ISO-8601 timestamp, got {value!r}')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label} is not an ISO-8601 timestamp: {value!r}') from None
    if parsed.utcoffset() is None:
        raise ValueError(f'{label} must carry a UTC offset: {value!r}')
    return parsed


def check_pb_clock_binding(plan):
    """Validate a PB capture plan's clock binding before anything touches the device.

    Pilot plans carry no ``pb_clock_binding`` and keep their exact previous behaviour.
    A PB plan must bind the extension simulated clock to the PB history/cluster
    observation start: the plan EOF is the decision point, every packet is observed
    inside ``[observation_start, decision_point]`` in the declared timezone, and the
    requested ``initial_simulated_time`` is that observation start. Any drift aborts
    the capture before reset instead of rewinding/advancing the Controller clock into
    an HTTP 400.
    """
    binding = plan.get(PB_CLOCK_BINDING)
    if binding is None:
        return None
    if not isinstance(binding, dict):
        raise ValueError(f'{PB_CLOCK_BINDING} must be a JSON object')
    if set(binding) != set(PB_CLOCK_FIELDS):
        raise ValueError(f'{PB_CLOCK_BINDING} must carry exactly {sorted(PB_CLOCK_FIELDS)}, '
                         f'got {sorted(binding)}')
    if binding.get('source') != PB_CLOCK_SOURCE:
        raise ValueError(f'{PB_CLOCK_BINDING} source must be {PB_CLOCK_SOURCE!r}, '
                         f'got {binding.get("source")!r}')
    timezone_name = binding.get('timezone')
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError(f'{PB_CLOCK_BINDING} timezone must be a nonempty string')
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'{PB_CLOCK_BINDING} timezone {timezone_name!r} is unknown') from None
    scope = plan.get('episode_scope')
    declared_timezones = [plan.get('device_timezone'),
                          scope.get('timezone') if isinstance(scope, dict) else None]
    declared_timezones = [value for value in declared_timezones if value is not None]
    if not declared_timezones:
        raise ValueError(f'{PB_CLOCK_BINDING} requires the plan device_timezone or episode_scope '
                         f'timezone to match {timezone_name!r}')
    for declared in declared_timezones:
        if declared != timezone_name:
            raise ValueError(f'{PB_CLOCK_BINDING} timezone {timezone_name!r} does not match the '
                             f'plan timezone {declared!r}')
    observation_start = clock_timestamp(binding.get('observation_start'),
                                        f'{PB_CLOCK_BINDING} observation_start')
    decision_point = clock_timestamp(binding.get('decision_point'),
                                     f'{PB_CLOCK_BINDING} decision_point')
    for label, parsed in (('observation_start', observation_start),
                          ('decision_point', decision_point)):
        if parsed.astimezone(zone).utcoffset() != parsed.utcoffset():
            raise ValueError(f'{PB_CLOCK_BINDING} {label} {binding[label]!r} is not in timezone '
                             f'{timezone_name!r}')
    if observation_start > decision_point:
        raise ValueError(f'{PB_CLOCK_BINDING} observation_start {binding["observation_start"]!r} '
                         f'is after decision_point {binding["decision_point"]!r}')
    if plan.get('eof_at') != binding['decision_point']:
        raise ValueError(f'{PB_CLOCK_BINDING} decision_point {binding["decision_point"]!r} does '
                         f"not match the plan eof_at {plan.get('eof_at')!r}")
    if plan.get('initial_simulated_time') is not None and \
            plan['initial_simulated_time'] != binding['observation_start']:
        raise ValueError('initial_simulated_time must equal the PB clock binding observation_start')
    packets = plan.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('a PB clock binding requires the capture plan packets')
    for position, packet in enumerate(packets, start=1):
        if not isinstance(packet, dict):
            raise ValueError(f'capture plan packet #{position} must be a JSON object')
        packet_id = packet.get('packet_id') or f'#{position}'
        observed = clock_timestamp(packet.get('observed_at'), f'packet {packet_id} observed_at')
        if observed.astimezone(zone).utcoffset() != observed.utcoffset():
            raise ValueError(f'packet {packet_id} observed_at {packet["observed_at"]!r} is not in '
                             f'timezone {timezone_name!r}')
        if observed < observation_start:
            raise ValueError(f'packet {packet_id} observed_at {packet["observed_at"]!r} precedes '
                             f'the PB observation_start {binding["observation_start"]!r}')
        if observed > decision_point:
            raise ValueError(f'packet {packet_id} observed_at {packet["observed_at"]!r} is after '
                             f'the PB decision_point {binding["decision_point"]!r}')
    return binding


def set_pb_clock_binding(base, key, plan, receipt, binding):
    """Rewind the extension simulated clock to the PB observation start.

    ``POST /local/v1/clock`` is the existing Controller simulation-clock authority
    (same runner key as every management request). It rewrites only the extension
    simulated clock; the host clock and the PB data are never touched.
    """
    receipt['clock_binding'] = dict(binding)
    receipt['environment'] = {'device_timezone': plan.get('device_timezone'),
                              'timezone': binding['timezone'],
                              'simulated_time': binding['observation_start'],
                              'source': binding['source']}
    response = request(base, key, '/local/v1/clock',
                       {'simulated_time': binding['observation_start']})
    simulated_time = response.get('simulated_time')
    if simulated_time != binding['observation_start']:
        raise RuntimeError(f'Controller simulated clock is {simulated_time!r} after setting '
                           f'{binding["observation_start"]!r}')


def read_device_env(serial):
    def value(*args):
        return adb(serial, 'shell', *args).decode(errors='replace').strip()
    locale = value('getprop', 'persist.sys.locale') or value('getprop', 'ro.product.locale')
    if not locale:
        locale = value('settings', 'get', 'system', 'system_locales')
    if not locale or locale.lower() == 'null':
        raise RuntimeError('Android locale is unavailable')
    font_scale = value('settings', 'get', 'system', 'font_scale')
    if not font_scale or font_scale.lower() == 'null':
        font_scale = 'default'
    try:
        theme = value('cmd', 'uimode', 'night')
    except RuntimeError:
        theme = ''
    if not theme or theme.lower().startswith('error') or theme.lower() == 'null':
        theme = value('settings', 'get', 'secure', 'ui_night_mode')
    if not theme or theme.lower() == 'null':
        raise RuntimeError('Android night mode state is unavailable')
    environment = {'resolution': value('wm', 'size'), 'density': value('wm', 'density'),
                   'font_scale': font_scale, 'locale': locale, 'theme': theme,
                   'timezone': value('getprop', 'persist.sys.timezone')}
    if any(not item or item.lower() in ('null', 'unknown') for item in environment.values()):
        raise RuntimeError('Android device environment is incomplete')
    return environment


def set_device_time(serial, observed_at, device_timezone):
    target = datetime.fromisoformat(observed_at)
    adb(serial, 'shell', 'su', '0', 'cmd', 'alarm', 'set-timezone', device_timezone)
    adb(serial, 'shell', 'su', '0', 'cmd', 'alarm', 'set-time', str(int(target.timestamp() * 1000)))
    actual = datetime.fromisoformat(adb(serial, 'shell', 'date', '-Is').decode().strip())
    if actual.utcoffset() != target.utcoffset() or abs((actual - target).total_seconds()) > 30:
        raise RuntimeError('Android clock does not match packet time and timezone')
    return actual.isoformat()


def read_device_wall_clock(serial):
    """Sample the timezone-aware device wall clock immediately before a screencap.

    The status bar renders this instant, so reading it after ``screencap`` can
    disagree with the PNG across a minute boundary. Missing, non-ISO, or
    timezone-less readings fail the capture closed.
    """
    value = adb(serial, 'shell', 'date', '-Is').decode(errors='replace').strip()
    if not value:
        raise RuntimeError('Android wall clock is unavailable')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise RuntimeError(f'Android wall clock is not an ISO-8601 timestamp: {value!r}') from None
    if parsed.utcoffset() is None:
        raise RuntimeError(f'Android wall clock lacks a timezone: {value!r}')
    return parsed.isoformat()


def ui_text(serial):
    adb(serial, 'shell', 'uiautomator', 'dump', '/sdcard/gui-capture.xml', timeout=60)
    xml = adb(serial, 'shell', 'cat', '/sdcard/gui-capture.xml')
    root = ElementTree.fromstring(xml)
    return root, xml


def assert_unobscured(root, package, windows='', reject_popups=False):
    if any(node.attrib.get('package') not in (package, None) for node in root.iter('node')):
        raise RuntimeError('another app or System UI overlays the capture')
    for window in re.split(r'(?=\bWindow #\d+ Window\{)', windows):
        if ' NotificationShade}' in window and 'isVisible=true' in window:
            raise RuntimeError('System UI notification shade overlays the capture')
        if reject_popups and ' PopupWindow:' in window and 'isVisible=true' in window:
            raise RuntimeError('app tutorial popup overlays the capture')


def suppress_unrelated_notifications(serial):
    adb(serial, 'shell', 'cmd', 'notification', 'set_dnd', 'on')
    state = adb(serial, 'shell', 'dumpsys', 'notification').decode(errors='replace')
    if 'mZenMode=ZEN_MODE_NO_INTERRUPTIONS' not in state:
        raise RuntimeError('Android notification suppression did not take effect')


def wait_unobscured(serial, package, timeout, reject_popups=False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            assert_unobscured(ui_text(serial)[0], package,
                              adb(serial, 'shell', 'dumpsys', 'window', 'windows').decode(errors='replace'), reject_popups)
            time.sleep(.5)
            assert_unobscured(ui_text(serial)[0], package,
                              adb(serial, 'shell', 'dumpsys', 'window', 'windows').decode(errors='replace'), reject_popups)
            return
        except RuntimeError:
            time.sleep(.5)
    raise RuntimeError('app screen remains obscured after notification transition')


def ready_view(serial, app, expected, timeout, marker=None, package=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = list(root.iter('node'))
        visible = [n.attrib.get('text', '') for n in nodes if n.attrib.get('package') == (package or f'com.ku.simulator.{app}')]
        if (marker is None or marker in visible) and all(sum(value in text for text in visible) == 1 for value in expected.values()):
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(), 'visible_assertions': {k: True for k in expected}}
        time.sleep(.5)
    raise RuntimeError(f'{app} popup not visible within {timeout}s')


def await_readiness(serial, app, cap, record):
    """Wait for the packet's declared view and return its readiness receipt.

    Every branch asserts the packet's exact visible nodes on the real app
    screen: the Calendar editor fields, the Calendar day-list header plus the
    unique event row, the Mail detail fields, or the two Mail Inbox rows bound
    by exact subject/sender/snippet text.
    """
    expected, timeout, view = cap['expected_visible'], cap['timeout_seconds'], cap['view_id']
    if app == 'clock':
        return ready_alarm(ui_text, serial, expected, timeout)
    if app == 'contacts':
        return ready_contact(ui_text, serial, expected, timeout)
    if app == 'messages':
        return ready_message(ui_text, serial, record, timeout)
    if view == 'docreader_content':
        return ready_docreader(ui_text, serial, expected, timeout)
    if app == 'files':
        return ready_file(ui_text, serial, expected, timeout)
    if app == 'calendar':
        return ready_event_editor(ui_text, serial, expected, timeout) if view == 'event_editor' \
            else ready_calendar_day_list(ui_text, serial, expected, timeout)
    if app == 'mail':
        return ready_mail_inbox(ui_text, serial, expected, timeout) if view == 'mail_inbox' \
            else ready_view(serial, app, expected, timeout,
                            marker='内容详情' if view == 'article_detail' else None,
                            package=MAIL_PACKAGE)
    return ready_view(serial, app, expected, timeout)


def tap_node(serial, package, resource_id, value=None):
    root, _ = ui_text(serial)
    nodes = [n for n in root.iter('node') if n.attrib.get('package') == package
             and n.attrib.get('resource-id') == resource_id and (value is None or n.attrib.get('text') == value)]
    if len(nodes) != 1:
        raise RuntimeError(f'{package} target is not unique on screen')
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', nodes[0].attrib.get('bounds', ''))
    if not match:
        raise RuntimeError('target title has no screen bounds')
    x1, y1, x2, y2 = map(int, match.groups())
    adb(serial, 'shell', 'input', 'tap', str((x1 + x2) // 2), str((y1 + y2) // 2))


def tap_unique_text(serial, app, value, package=None):
    root, _ = ui_text(serial)
    nodes = [n for n in root.iter('node') if n.attrib.get('package') == (package or f'com.ku.simulator.{app}')
             and n.attrib.get('text') == value and n.attrib.get('visible-to-user', 'true') == 'true'
             and n.attrib.get('bounds')]
    targets = {n.attrib['bounds']: n for n in nodes}
    if len(targets) != 1:
        raise RuntimeError(f'{app} target title is not unique on screen')
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', next(iter(targets)))
    if not match:
        raise RuntimeError('target title has no screen bounds')
    x1, y1, x2, y2 = map(int, match.groups())
    adb(serial, 'shell', 'input', 'tap', str((x1 + x2) // 2), str((y1 + y2) // 2))


def tap_unique_content_desc(serial, package, value):
    root, _ = ui_text(serial)
    nodes = [n for n in root.iter('node') if n.attrib.get('package') == package
             and n.attrib.get('content-desc') == value and n.attrib.get('visible-to-user', 'true') == 'true'
             and n.attrib.get('bounds')]
    if len(nodes) != 1:
        raise RuntimeError(f'{package} target content description is not unique on screen')
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', nodes[0].attrib['bounds'])
    if not match:
        raise RuntimeError('target date has no screen bounds')
    x1, y1, x2, y2 = map(int, match.groups())
    adb(serial, 'shell', 'input', 'tap', str((x1 + x2) // 2), str((y1 + y2) // 2))


def open_calendar_day(serial, event_start, navigation):
    """Tap the unique event date cell that opens the Calendar day view.

    The month grid renders each date as a content description and never renders
    the event title, so this day-cell tap is the minimal reliable entry point
    shared by the editor and day-list captures. Both common English date orders
    are accepted, each still required to be unique. Returns True when a tap was
    issued and False when neither day cell is on screen.
    """
    for event_date in (event_start.strftime('%-d %B'), event_start.strftime('%B %-d')):
        try:
            tap_unique_content_desc(serial, CALENDAR_PACKAGE, event_date)
            navigation.append('calendar:direct-event-day')
            return True
        except RuntimeError:
            continue
    return False


def readback(base, key, app, ref, entity_type, expected):
    state = request(base, key, f'/local/v1/apps/{app}/read_state', {})['data']
    matches = [r for r in state['typed_entities'] if r['semantic_ref'] == ref and r['entity_type'] == entity_type]
    if len(matches) != 1 or any(matches[0]['data'].get(k) != v for k, v in expected.items()):
        raise RuntimeError(f'{app}:{ref} authority mismatch')
    return {'record_version': matches[0]['record_version'], 'data_sha256': hashlib.sha256(
        json.dumps(matches[0]['data'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()}


def verify_seeded(state, records, episode):
    expected = {(record['entity_type'], record['semantic_ref']): record['data'] for record in records}
    scoped = [record for record in state['typed_entities'] if record['episode_scope'] == episode]
    actual = {(record['entity_type'], record['semantic_ref']): record['data'] for record in scoped}
    if actual != expected or len(scoped) != len(expected):
        raise RuntimeError('episode authority differs from seeded World')
    return hashlib.sha256(json.dumps(sorted(scoped, key=lambda record: (record['entity_type'], record['semantic_ref'])),
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


MAIL_HISTORY_FUNCTION = 'apply_history_change'
MAIL_HISTORY_FIELD = 'status'
MAIL_HISTORY_SOURCE_STATUS = 'unread'
MAIL_HISTORY_TARGET_STATUS = 'read'
CALENDAR_HISTORY_FUNCTION = 'apply_history_change'
CALENDAR_REMINDER_FIELD = 'calendar_reminder_minutes'
CALENDAR_UPDATED_FIELD = 'updated_at'
CALENDAR_HISTORY_FIELDS = (CALENDAR_REMINDER_FIELD, CALENDAR_UPDATED_FIELD)
CALENDAR_REMINDER_COUNT = 3


def apply_history_replays(adb, serial, packet, mail_records, replayed_refs):
    """Replay one packet's declared Mail history changes before navigation.

    Only the private ``history_replays`` field a PB capture plan declares is
    consumed; plans without the field (the old pilot plans) never call the
    device replay. Every declared update must be a Mail ``unread -> read``
    transition whose ``effective_at`` has already passed the packet's
    ``observed_at``, must not have been replayed before, and must match a
    fixture record; anything else aborts the capture. The matched fixture record
    is advanced to the replayed state so the following ``read_mail`` readback
    matches the packet's instant of the World. Returns the receipt entries.
    """
    replays = packet.get('history_replays')
    if not replays:
        return []
    if not isinstance(replays, list):
        raise RuntimeError(f'packet {packet.get("packet_id")!r} history_replays must be a list')
    observed = packet.get('observed_at')
    try:
        observed_at = datetime.fromisoformat(observed)
    except (TypeError, ValueError):
        raise RuntimeError(f'packet {packet.get("packet_id")!r} observed_at must be an ISO-8601 '
                           f'timestamp with a UTC offset') from None
    results = []
    for position, replay in enumerate(replays, start=1):
        label = f'packet {packet.get("packet_id")!r} history_replays[{position}]'
        if not isinstance(replay, dict):
            raise RuntimeError(f'{label} must be an object')
        ref = replay.get('semantic_ref')
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError(f'{label} needs a nonempty semantic_ref')
        updates = replay.get('set')
        if not isinstance(updates, dict) or set(updates) != {MAIL_HISTORY_FIELD} or \
                updates[MAIL_HISTORY_FIELD] != MAIL_HISTORY_TARGET_STATUS:
            raise RuntimeError(f'{label} only allows the declared Mail {MAIL_HISTORY_SOURCE_STATUS} '
                               f'-> {MAIL_HISTORY_TARGET_STATUS} {MAIL_HISTORY_FIELD} update, got '
                               f'{updates!r}')
        effective = replay.get('effective_at')
        try:
            effective_at = datetime.fromisoformat(effective)
        except (TypeError, ValueError):
            raise RuntimeError(f'{label} effective_at must be an ISO-8601 timestamp with a UTC '
                               f'offset, got {effective!r}') from None
        if effective_at.tzinfo is None or observed_at.tzinfo is None or effective_at > observed_at:
            raise RuntimeError(f'{label} effective_at {effective!r} is not effective at the packet '
                               f'observed_at {observed!r}')
        if ref in replayed_refs:
            raise RuntimeError(f'{label} replays {ref!r} twice; the declared history was already '
                               f'applied')
        record = next((row for row in mail_records if row['semantic_ref'] == ref), None)
        if record is None:
            raise RuntimeError(f'{label} targets {ref!r}, which is not in the capture fixture')
        if record['data'].get(MAIL_HISTORY_FIELD) != MAIL_HISTORY_SOURCE_STATUS:
            raise RuntimeError(f'{label} expected {ref!r} to start at '
                               f'{MAIL_HISTORY_SOURCE_STATUS!r}, got '
                               f'{record["data"].get(MAIL_HISTORY_FIELD)!r}')
        updated = dict(record['data'])
        updated.update(updates)
        try:
            result = apply_history_change(adb, serial, {'semantic_ref': ref, 'data': updated})
        except Exception as error:
            raise RuntimeError(f'{label} failed: {error}') from error
        record['data'] = updated
        replayed_refs.add(ref)
        results.append({'packet_id': packet.get('packet_id'), 'semantic_ref': ref,
                        'effective_at': effective,
                        'set': {MAIL_HISTORY_FIELD: MAIL_HISTORY_TARGET_STATUS},
                        'function': MAIL_HISTORY_FUNCTION, 'result': result})
    return results


def apply_calendar_history_replays(adb, serial, plan, packet, calendar_records, replayed_refs):
    """Replay one packet's declared Calendar reminder history changes before navigation.

    Only the ``history_replays`` entries that match a Calendar fixture record are
    consumed, so the Mail replay path never sees them. Every declared update must
    carry exactly ``calendar_reminder_minutes`` (a three-element integer list)
    and ``updated_at`` (the declared ``effective_at`` instant), must already be
    effective at the packet's ``observed_at``, must not replay the same entity
    twice and must match a fixture record; anything else aborts the capture. The
    matched fixture record is advanced to the replayed reminder state so the
    following ``read_event`` proves the packet's instant of the World. Returns
    the receipt entries.
    """
    replays = packet.get('history_replays')
    if not replays:
        return []
    if not isinstance(replays, list):
        raise RuntimeError(f'packet {packet.get("packet_id")!r} history_replays must be a list')
    observed = packet.get('observed_at')
    try:
        observed_at = datetime.fromisoformat(observed)
    except (TypeError, ValueError):
        raise RuntimeError(f'packet {packet.get("packet_id")!r} observed_at must be an ISO-8601 '
                           f'timestamp with a UTC offset') from None
    results = []
    for position, replay in enumerate(replays, start=1):
        label = f'packet {packet.get("packet_id")!r} history_replays[{position}]'
        if not isinstance(replay, dict):
            raise RuntimeError(f'{label} must be an object')
        ref = replay.get('semantic_ref')
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError(f'{label} needs a nonempty semantic_ref')
        updates = replay.get('set')
        if not isinstance(updates, dict) or set(updates) != set(CALENDAR_HISTORY_FIELDS):
            raise RuntimeError(f'{label} only allows the declared Calendar '
                               f'{sorted(CALENDAR_HISTORY_FIELDS)} update, got {updates!r}')
        minutes = updates[CALENDAR_REMINDER_FIELD]
        if not isinstance(minutes, list) or len(minutes) != CALENDAR_REMINDER_COUNT or any(
                not isinstance(value, int) or isinstance(value, bool) for value in minutes):
            raise RuntimeError(f'{label} calendar_reminder_minutes must be a three-element integer '
                               f'list, got {minutes!r}')
        updated = updates[CALENDAR_UPDATED_FIELD]
        if not isinstance(updated, str) or not updated.strip():
            raise RuntimeError(f'{label} updated_at must be a nonempty ISO-8601 timestamp, got '
                               f'{updated!r}')
        effective = replay.get('effective_at')
        try:
            effective_at = datetime.fromisoformat(effective)
        except (TypeError, ValueError):
            raise RuntimeError(f'{label} effective_at must be an ISO-8601 timestamp with a UTC '
                               f'offset, got {effective!r}') from None
        if effective_at.tzinfo is None or observed_at.tzinfo is None or effective_at > observed_at:
            raise RuntimeError(f'{label} effective_at {effective!r} is not effective at the packet '
                               f'observed_at {observed!r}')
        if ref in replayed_refs:
            raise RuntimeError(f'{label} replays {ref!r} twice; the declared history was already '
                               f'applied')
        record = next((row for row in calendar_records if row['semantic_ref'] == ref), None)
        if record is None:
            raise RuntimeError(f'{label} targets {ref!r}, which is not a Calendar capture fixture '
                               f'record')
        change = {'semantic_ref': ref, 'entity_type': record.get('entity_type', 'events'),
                  'data': {CALENDAR_REMINDER_FIELD: list(minutes), CALENDAR_UPDATED_FIELD: updated}}
        try:
            result = calendar_apply_history_change(adb, serial, plan['episode_id'], change,
                                                   effective)
        except Exception as error:
            raise RuntimeError(f'{label} failed: {error}') from error
        record['data'] = dict(record['data'], reminder_minutes_before=minutes[0])
        replayed_refs.add(ref)
        results.append({'packet_id': packet.get('packet_id'), 'semantic_ref': ref,
                        'effective_at': effective, 'set': dict(updates),
                        'function': CALENDAR_HISTORY_FUNCTION, 'result': result})
    return results


def apply_packet_history_replays(adb, serial, plan, packet, calendar_records, mail_records,
                                 replayed_refs):
    """Apply one packet's declared history changes in effective_at order.

    ``history_replays`` mixes the supported entities: each entry is classified by
    the fixture record it targets and dispatched to that entity's own adapter
    (Calendar reminder vs Mail status), so neither validator ever accepts the
    other entity's fields. A packet that carries several declared changes replays
    them in nondecreasing ``(effective_at, semantic_ref)`` order, and an entry
    that targets no fixture record aborts the capture instead of being dropped.
    """
    declared = packet.get('history_replays')
    if not declared:
        return []
    if not isinstance(declared, list):
        raise RuntimeError(f'packet {packet.get("packet_id")!r} history_replays must be a list')
    calendar_refs = {record['semantic_ref'] for record in calendar_records}
    mail_refs = {record['semantic_ref'] for record in mail_records}
    ordered = []
    for position, replay in enumerate(declared, start=1):
        label = f'packet {packet.get("packet_id")!r} history_replays[{position}]'
        if not isinstance(replay, dict):
            raise RuntimeError(f'{label} must be an object')
        ref = replay.get('semantic_ref')
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError(f'{label} needs a nonempty semantic_ref')
        if ref in calendar_refs and ref in mail_refs:
            raise RuntimeError(f'{label} targets {ref!r}, which is both a Calendar and a Mail '
                               f'fixture record')
        if ref in calendar_refs:
            entity = 'calendar'
        elif ref in mail_refs:
            entity = 'mail'
        else:
            raise RuntimeError(f'{label} targets {ref!r}, which is not in the capture fixture')
        try:
            effective_at = datetime.fromisoformat(replay.get('effective_at'))
        except (TypeError, ValueError):
            raise RuntimeError(f'{label} effective_at must be an ISO-8601 timestamp with a UTC '
                               f'offset, got {replay.get("effective_at")!r}') from None
        if effective_at.tzinfo is None:
            raise RuntimeError(f'{label} effective_at must carry a UTC offset')
        ordered.append((effective_at, ref, entity, replay))
    ordered.sort(key=lambda item: (item[0], item[1]))
    entities = {item[2] for item in ordered}
    if entities == {'mail'}:
        return apply_history_replays(adb, serial, packet, mail_records, replayed_refs)
    if entities == {'calendar'}:
        return apply_calendar_history_replays(adb, serial, plan, packet, calendar_records,
                                              replayed_refs)
    results = []
    for _, _, entity, replay in ordered:
        filtered = dict(packet, history_replays=[replay])
        if entity == 'calendar':
            results.extend(apply_calendar_history_replays(adb, serial, plan, filtered,
                                                          calendar_records, replayed_refs))
        else:
            results.extend(apply_history_replays(adb, serial, filtered, mail_records,
                                                 replayed_refs))
    return results


def calendar_world_hash(adb, serial, plan, calendar_records):
    rows = [read_event(adb, serial, plan['episode_id'], record, plan['device_timezone'])
            for record in calendar_records]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def run(plan, instance, output):
    info_path = ROOT / 'runtime/instances' / instance / 'instance.json'
    info = json.loads(info_path.read_text())
    if info['snapshot'] != plan['baseline_ref']:
        raise ValueError('baseline mismatch')
    if not info.get('baseline_version') or not info.get('dataset_version'):
        raise ValueError('instance lacks baseline or dataset version')
    serial = info['adb_serial']
    os.environ['KU_EMULATOR_CONTAINER'] = info.get('ku_container', '')
    base = f'http://127.0.0.1:{info["controller_port"]}'
    key = info['runner_key']
    run_id = secrets.token_hex(16)
    output.mkdir(parents=True, exist_ok=False)
    images = output / 'images'
    images.mkdir()
    ui_dumps = output / 'ui'
    ui_dumps.mkdir()
    plan_bytes = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode()
    receipt = {'schema_version': '1.0', 'episode_id': plan['episode_id'], 'capture_run_id': run_id,
               'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
               'source_assets': plan['source_assets'], 'instance_id': instance, 'adb_serial': serial,
               'status': 'capture_failed', 'frames': [], 'seed_receipts': {}, 'initial_readback': {},
               'readback': {}, 'failure_reason': None}
    def save_receipt():
        temporary = output / 'receipt.json.tmp'
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(output / 'receipt.json')
    (output / 'private-plan.json').write_bytes(plan_bytes)
    save_receipt()
    lock = ROOT / 'runtime' / f'gui-{serial.replace(":", "_")}.lock'
    with lock.open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reset_started = False
        try:
            clock_binding = check_pb_clock_binding(plan)
            if ':' in serial:
                command('adb', 'connect', serial)
            adb(serial, 'get-state')
            reset_started = True
            reset_instance(instance)
            status = request(base, key, '/local/v1/device/status')
            if not status['available'] or not status['boot_completed']:
                raise RuntimeError('Android device not ready')
            if clock_binding is not None:
                set_pb_clock_binding(base, key, plan, receipt, clock_binding)
                save_receipt()
            suppress_unrelated_notifications(serial)
            receipt['device'] = {
                'baseline_version': info.get('baseline_version'), 'dataset_version': info.get('dataset_version'),
                'apk_versions': info.get('apk_versions'),
                'android_release': adb(serial, 'shell', 'getprop', 'ro.build.version.release').decode().strip(),
                **read_device_env(serial),
                'notification_policy': 'dnd_none_for_app_screen'}
            apps = sorted({p['source'] for p in plan['packets']} - {'calendar', 'mail', 'clock', 'contacts', 'files', 'messages'})
            ku_records = plan['fixture'].get('ku_records', [])
            calendar_records = [record for record in ku_records if record['app_id'] == 'calendar']
            mail_records = [record for record in ku_records if record['app_id'] == 'mail']
            clock_records = [record for record in ku_records if record['app_id'] == 'clock']
            contact_records = [record for record in ku_records if record['app_id'] == 'contacts']
            file_records = [record for record in ku_records if record['app_id'] == 'files']
            message_records = [record for record in ku_records if record['app_id'] == 'messages']
            for app in apps:
                fixture = {'episode_scope': plan['episode_id'],
                           'common_records': [r for r in plan['fixture']['common_records'] if r['app_id'] == app],
                           'world_deltas': []}
                seeded = request(base, key, f'/local/v1/apps/{app}/seed', {'fixture': fixture, 'idempotency_key': f'{run_id}-{app}'})
                receipt['seed_receipts'][app] = seeded['data']
                state = request(base, key, f'/local/v1/apps/{app}/read_state', {})['data']
                receipt['initial_readback'][app] = verify_seeded(state, fixture['common_records'], plan['episode_id'])
            for record in calendar_records:
                seed = seed_event(adb, serial, plan['episode_id'], record, plan['device_timezone'],
                                  plan['packets'][0]['observed_at'])
                receipt['seed_receipts'][record['semantic_ref']] = seed
            if calendar_records:
                receipt['initial_readback']['calendar'] = calendar_world_hash(adb, serial, plan,
                                                                              calendar_records)
            for record in mail_records:
                receipt['seed_receipts'][record['semantic_ref']] = seed_mail(adb, serial, record)
            if mail_records:
                receipt['initial_readback']['mail'] = read_mail(adb, serial, mail_records[0])
            for record in clock_records:
                receipt['seed_receipts'][record['semantic_ref']] = seed_alarm(adb, serial, plan['episode_id'], record)
            if clock_records:
                rows = [read_alarm(adb, serial, plan['episode_id'], record) for record in clock_records]
                receipt['initial_readback']['clock'] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            for record in contact_records:
                receipt['seed_receipts'][record['semantic_ref']] = seed_contact(adb, serial, record)
            if contact_records:
                rows = [read_contact(adb, serial, record) for record in contact_records]
                receipt['initial_readback']['contacts'] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            for record in file_records:
                receipt['seed_receipts'][record['semantic_ref']] = seed_file(adb, serial, record)
            if file_records:
                rows = [read_file(adb, serial, record) for record in file_records]
                receipt['initial_readback']['files'] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            for record in message_records:
                receipt['seed_receipts'][record['semantic_ref']] = seed_message(adb, serial, record)
            if message_records:
                receipt['initial_readback']['messages'] = [read_message(adb, serial, record) for record in message_records]
            save_receipt()
            dnd_active = True
            replayed_refs = set()
            mail_history_authority = None
            calendar_history_authority = None
            calendar_refs = {record['semantic_ref'] for record in calendar_records}
            for packet in plan['packets']:
                cap, app = packet['capture'], packet['source']
                observed = packet['observed_at']
                if app != 'clock' and not dnd_active:
                    suppress_unrelated_notifications(serial)
                    dnd_active = True
                request(base, key, '/local/v1/instance/advance_time', {'logical_time': observed})
                device_time = set_device_time(serial, observed, plan['device_timezone'])
                for replay in apply_packet_history_replays(adb, serial, plan, packet,
                                                           calendar_records, mail_records,
                                                           replayed_refs):
                    if replay['semantic_ref'] in calendar_refs:
                        calendar_history_authority = calendar_world_hash(adb, serial, plan,
                                                                         calendar_records)
                    else:
                        mail_history_authority = replay['result']
                    receipt.setdefault('history_replays', []).append(replay)
                    save_receipt()
                if app == 'calendar':
                    record = next(r for r in calendar_records if r['semantic_ref'] == cap['subject_ref'])
                    authority = read_event(adb, serial, plan['episode_id'], record, plan['device_timezone'])
                    navigation_mode = cap.get(CALENDAR_NAVIGATION_MODE)
                    if navigation_mode not in (None, CALENDAR_DIRECT_NAVIGATION):
                        raise RuntimeError(f'calendar capture declares unsupported '
                                           f'{CALENDAR_NAVIGATION_MODE} {navigation_mode!r}')
                    if cap['view_id'] == 'calendar_list' and \
                            navigation_mode != CALENDAR_DIRECT_NAVIGATION:
                        raise RuntimeError(f'calendar_list capture requires '
                                           f'{CALENDAR_NAVIGATION_MODE}='
                                           f'{CALENDAR_DIRECT_NAVIGATION!r} so the day list '
                                           'is asserted without opening the editor')
                    adb(serial, 'shell', 'am', 'force-stop', CALENDAR_PACKAGE)
                    adb(serial, 'shell', 'monkey', '-p', CALENDAR_PACKAGE, '1')
                    deadline = time.monotonic() + cap['timeout_seconds']
                    navigation = []
                    if navigation_mode == CALENDAR_DIRECT_NAVIGATION:
                        event_start = datetime.fromisoformat(record['data']['start_at'])
                        if cap['view_id'] == 'calendar_list':
                            # p003/p018: the day view is the target screen, so the
                            # event item must never be tapped (that would open the
                            # editor and capture the wrong surface).
                            while not open_calendar_day(serial, event_start, navigation):
                                if time.monotonic() >= deadline:
                                    raise RuntimeError('Calendar day cell was not found before the '
                                                       'capture deadline')
                                time.sleep(.5)
                        else:
                            title = cap.get('expected_visible', {}).get('title') \
                                if isinstance(cap.get('expected_visible'), dict) else None
                            if not isinstance(title, str) or not title:
                                raise RuntimeError('direct_event navigation requires the packet '
                                                   'visible title assertion')
                            if not open_calendar_day(serial, event_start, navigation):
                                # The day view (or another event list) may already be
                                # showing; the unique title tap below still has to land.
                                navigation.append('calendar:direct-event-day-not-found')
                            while True:
                                try:
                                    tap_unique_text(serial, app, title, package=CALENDAR_PACKAGE)
                                    break
                                except RuntimeError:
                                    if time.monotonic() >= deadline:
                                        raise
                                    time.sleep(.5)
                            navigation.append('calendar:direct-event-item')
                    else:
                        tap_node(serial, CALENDAR_PACKAGE, f'{CALENDAR_PACKAGE}:id/top_toolbar_search_icon')
                        adb(serial, 'shell', 'input', 'text', record['data']['title'])
                        while True:
                            try:
                                tap_node(serial, CALENDAR_PACKAGE, f'{CALENDAR_PACKAGE}:id/event_item_title', record['data']['title'])
                                break
                            except RuntimeError:
                                if time.monotonic() >= deadline:
                                    raise
                                time.sleep(.5)
                        navigation = ['calendar:search-title', 'calendar:tap-event-editor']
                elif app == 'mail':
                    record = next(r for r in mail_records if r['semantic_ref'] == cap['subject_ref'])
                    if cap['view_id'] == 'mail_inbox':
                        # Both inbox rows belong to this packet: verify every fixture
                        # mail record (including the replayed status) at this instant.
                        authority = read_mail_state(adb, serial, mail_records)
                    else:
                        authority = read_mail(adb, serial, record)
                    adb(serial, 'shell', 'am', 'force-stop', MAIL_PACKAGE)
                    adb(serial, 'shell', 'monkey', '-p', MAIL_PACKAGE, '1')
                    deadline = time.monotonic() + cap['timeout_seconds']
                    if cap['view_id'] == 'mail_inbox':
                        # The cold-started Mail app opens on its Inbox list, so the
                        # capture taps nothing; a detail page can never be substituted.
                        navigation = ['mail:inbox-cold-start']
                    else:
                        while True:
                            try:
                                tap_unique_text(serial, app, record['data']['headers']['subject'], package=MAIL_PACKAGE)
                                break
                            except RuntimeError:
                                if time.monotonic() >= deadline:
                                    raise
                                time.sleep(.5)
                        navigation = ['mail:tap-unique-subject']
                elif app == 'clock':
                    record = next(r for r in clock_records if r['semantic_ref'] == cap['subject_ref'])
                    authority = read_alarm(adb, serial, plan['episode_id'], record)
                    adb(serial, 'shell', 'am', 'force-stop', CLOCK_PACKAGE)
                    adb(serial, 'shell', 'am', 'start', '-n', f'{CLOCK_PACKAGE}/{CLOCK_ACTIVITY}')
                    navigation = ['clock:open-alarm-card']
                elif app == 'contacts':
                    record = next(r for r in contact_records if r['semantic_ref'] == cap['subject_ref'])
                    authority = read_contact(adb, serial, record)
                    adb(serial, 'shell', 'am', 'force-stop', CONTACTS_PACKAGE)
                    adb(serial, 'shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
                        '-d', f'content://com.android.contacts/contacts/{authority["contact_id"]}', '-p', CONTACTS_PACKAGE)
                    navigation = ['contacts:open-contact-id']
                elif app == 'files':
                    record = next(r for r in file_records if r['semantic_ref'] == cap['subject_ref'])
                    authority = read_file(adb, serial, record)
                    adb(serial, 'shell', 'am', 'force-stop', FILES_PACKAGE)
                    adb(serial, 'shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
                        '-d', 'content://com.android.externalstorage.documents/document/primary%3ADownload',
                        '-t', 'vnd.android.document/directory', '-p', FILES_PACKAGE)
                    tap_node(serial, FILES_PACKAGE, f'{FILES_PACKAGE}:id/option_menu_search')
                    adb(serial, 'shell', 'input', 'text', record['data']['path'].split('/')[-1])
                    adb(serial, 'shell', 'input', 'keyevent', '4')
                    navigation = ['files:open-Download', 'files:search-unique-name', 'files:hide-keyboard']
                    if cap['view_id'] == 'docreader_content':
                        ready_file(ui_text, serial, {'directory': 'Download', 'name': record['data']['path'].split('/')[-1]}, cap['timeout_seconds'])
                        tap_node(serial, FILES_PACKAGE, 'android:id/title', record['data']['path'].split('/')[-1])
                        navigation.append('files:open-unique-file')
                        deadline = time.monotonic() + cap['timeout_seconds']
                        while time.monotonic() < deadline:
                            root, _ = ui_text(serial)
                            if any(n.attrib.get('package') == READER_PACKAGE and n.attrib.get('resource-id') == f'{READER_PACKAGE}:id/page_view' for n in root.iter('node')):
                                break
                            choices = [n for n in root.iter('node') if n.attrib.get('text') == 'OpenDocument Reader']
                            once = [n for n in root.iter('node') if n.attrib.get('text') == 'Just once']
                            if len(choices) == 1:
                                tap_unique_text(serial, app, 'OpenDocument Reader', package=choices[0].attrib.get('package'))
                                navigation.append('files:choose-OpenDocument-Reader')
                            elif len(once) == 1:
                                tap_unique_text(serial, app, 'Just once', package=once[0].attrib.get('package'))
                                navigation.append('files:confirm-open-once')
                            else:
                                time.sleep(.5)
                        else:
                            raise RuntimeError('OpenDocument Reader did not open the selected file')
                elif app == 'messages':
                    record = next(r for r in message_records if r['semantic_ref'] == cap['subject_ref'])
                    authority = read_message(adb, serial, record)
                    adb(serial, 'shell', 'am', 'force-stop', MESSAGES_PACKAGE)
                    adb(serial, 'shell', 'monkey', '-p', MESSAGES_PACKAGE, '1')
                    navigation = ['messages:open-conversation-list']
                else:
                    entity_type = {'popup': 'popup', 'article_detail': 'articles'}[cap['view_id']]
                    authority = readback(base, key, app, cap['subject_ref'], entity_type, cap['expected_visible'])
                    request(base, key, f'/local/v1/apps/{app}/process', {'action': 'stop'})
                    request(base, key, f'/local/v1/apps/{app}/process', {'action': 'start'})
                    navigation = ['process:stop', 'process:start']
                    if cap['view_id'] == 'article_detail':
                        ready_view(serial, app, {'title': cap['expected_visible']['title']}, cap['timeout_seconds'])
                        tap_unique_text(serial, app, cap['expected_visible']['title'])
                        navigation.append('tap:unique-article-title')
                readiness = await_readiness(serial, app, cap, record)
                if app == 'clock':
                    adb(serial, 'shell', 'cmd', 'notification', 'set_dnd', 'off')
                    mode = adb(serial, 'shell', 'dumpsys', 'notification').decode(errors='replace')
                    if 'mZenMode=ZEN_MODE_OFF' not in mode:
                        raise RuntimeError('Clock capture cannot clear total-silence warning')
                    dnd_active = False
                    adb(serial, 'shell', 'input', 'tap', '520', '220')
                    wait_unobscured(serial, CLOCK_PACKAGE, cap['timeout_seconds'], reject_popups=True)
                    navigation.append('clock:dismiss-bedtime-tip')
                focus = adb(serial, 'shell', 'dumpsys', 'activity', 'activities').decode(errors='replace')
                focused = '\n'.join(line.strip() for line in focus.splitlines() if 'topResumedActivity=' in line)
                package = {'calendar': CALENDAR_PACKAGE, 'mail': MAIL_PACKAGE, 'clock': CLOCK_PACKAGE,
                           'contacts': CONTACTS_PACKAGE, 'files': FILES_PACKAGE, 'messages': MESSAGES_PACKAGE}.get(app, f'com.ku.simulator.{app}')
                if cap['view_id'] == 'docreader_content':
                    package = READER_PACKAGE
                if f'{package}/' not in focused:
                    raise RuntimeError(f'{app} not foreground')
                for frame in cap['frames']:
                    root, xml = ui_text(serial)
                    assert_unobscured(root, package,
                                      adb(serial, 'shell', 'dumpsys', 'window', 'windows').decode(errors='replace'),
                                      reject_popups=app == 'clock')
                    frame_environment = read_device_env(serial)
                    android_time = read_device_wall_clock(serial)
                    raw = adb(serial, 'exec-out', 'screencap', '-p')
                    assert_unobscured(ui_text(serial)[0], package,
                                      adb(serial, 'shell', 'dumpsys', 'window', 'windows').decode(errors='replace'),
                                      reject_popups=app == 'clock')
                    width, height = png_size(raw)
                    name = secrets.token_hex(16) + '.png'
                    path = images / name
                    with path.open('xb') as file:
                        file.write(raw)
                    ui_name = name.removesuffix('.png') + '.xml'
                    (ui_dumps / ui_name).write_bytes(xml)
                    receipt['frames'].append({'packet_id': packet['packet_id'], 'frame_id': frame['frame_id'],
                        'episode_id': plan['episode_id'], 'capture_run_id': run_id, 'instance_id': instance,
                        'source_assets': plan['source_assets'],
                        'sequence': packet['sequence'], 'source': app, 'observed_at': observed,
                        'captured_at': datetime.now(timezone.utc).isoformat(), 'android_time': android_time,
                        'clock_synced_at': device_time,
                        'package': package, 'activity': focused.strip(),
                        'notification_policy': 'normal_after_suppressed_time_jump' if app == 'clock' else 'dnd_none',
                        'navigation': navigation + [f'wait:{cap["view_id"]}'], 'readback': authority,
                        'ready': readiness, 'png': f'images/{name}', 'sha256': hashlib.sha256(raw).hexdigest(),
                        'ui_dump': f'ui/{ui_name}', 'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                        'width': width, 'height': height, 'device_env': frame_environment})
                    save_receipt()
                after = (read_event(adb, serial, plan['episode_id'], record, plan['device_timezone']) if app == 'calendar'
                         else read_mail_state(adb, serial, mail_records) if app == 'mail'
                         and cap['view_id'] == 'mail_inbox'
                         else read_mail(adb, serial, record) if app == 'mail'
                         else read_alarm(adb, serial, plan['episode_id'], record) if app == 'clock'
                         else read_contact(adb, serial, record) if app == 'contacts'
                         else read_file(adb, serial, record) if app == 'files'
                         else read_message(adb, serial, record) if app == 'messages'
                         else readback(base, key, app, cap['subject_ref'], entity_type, cap['expected_visible']))
                if after != authority:
                    raise RuntimeError('navigation changed authority')
            request(base, key, '/local/v1/instance/advance_time', {'logical_time': plan['eof_at']})
            receipt['freeze'] = request(base, key, '/local/v1/instance/freeze', {})
            for app in apps:
                receipt['readback'][app] = request(base, key, f'/local/v1/apps/{app}/read_state', {})['data']
                records = [record for record in plan['fixture']['common_records'] if record['app_id'] == app]
                if verify_seeded(receipt['readback'][app], records, plan['episode_id']) != receipt['initial_readback'][app]:
                    raise RuntimeError('World changed between seed and EOF')
                mutations = request(base, key, f'/local/v1/apps/{app}/mutations?since_cursor=0')['data']['mutations']
                if any(m['actor'] == 'agent_gui' and m['status'] == 'committed' for m in mutations):
                    raise RuntimeError('capture navigation polluted business state')
            if calendar_records:
                receipt['readback']['calendar'] = [read_event(adb, serial, plan['episode_id'], record,
                                                               plan['device_timezone']) for record in calendar_records]
                expected_calendar = calendar_history_authority or receipt['initial_readback']['calendar']
                if hashlib.sha256(json.dumps(receipt['readback']['calendar'], sort_keys=True).encode()).hexdigest() != expected_calendar:
                    raise RuntimeError('Calendar World changed before EOF')
            if mail_records:
                receipt['readback']['mail'] = read_mail(adb, serial, mail_records[0])
                expected_mail = mail_history_authority or receipt['initial_readback']['mail']
                if receipt['readback']['mail'] != expected_mail:
                    raise RuntimeError('Mail World changed before EOF')
            if clock_records:
                receipt['readback']['clock'] = [read_alarm(adb, serial, plan['episode_id'], record) for record in clock_records]
                if hashlib.sha256(json.dumps(receipt['readback']['clock'], sort_keys=True).encode()).hexdigest() != receipt['initial_readback']['clock']:
                    raise RuntimeError('Clock World changed before EOF')
            if contact_records:
                receipt['readback']['contacts'] = [read_contact(adb, serial, record) for record in contact_records]
                if hashlib.sha256(json.dumps(receipt['readback']['contacts'], sort_keys=True).encode()).hexdigest() != receipt['initial_readback']['contacts']:
                    raise RuntimeError('Contacts World changed before EOF')
            if file_records:
                receipt['readback']['files'] = [read_file(adb, serial, record) for record in file_records]
                if hashlib.sha256(json.dumps(receipt['readback']['files'], sort_keys=True).encode()).hexdigest() != receipt['initial_readback']['files']:
                    raise RuntimeError('Files World changed before EOF')
            if message_records:
                receipt['readback']['messages'] = [read_message(adb, serial, record) for record in message_records]
                if receipt['readback']['messages'] != receipt['initial_readback']['messages']:
                    raise RuntimeError('Messages World changed before EOF')
            receipt['status'] = 'capture_complete'
        except Exception as exc:
            receipt['failure_reason'] = str(exc)
        finally:
            try:
                if reset_started:
                    reset_instance(instance)
                    for app in sorted({p['source'] for p in plan['packets']}):
                        if app == 'calendar':
                            assert_absent(adb, serial, plan['episode_id'], calendar_records)
                            continue
                        if app == 'mail':
                            mail_absent(adb, serial, mail_records)
                            continue
                        if app == 'clock':
                            clock_absent(adb, serial, plan['episode_id'], clock_records)
                            continue
                        if app == 'contacts':
                            contacts_absent(adb, serial, contact_records)
                            continue
                        if app == 'files':
                            files_absent(adb, serial, file_records)
                            continue
                        if app == 'messages':
                            messages_absent(adb, serial, message_records)
                            continue
                        state = request(base, key, f'/local/v1/apps/{app}/read_state', {})['data']
                        if any(r['episode_scope'] == plan['episode_id'] for r in state['typed_entities']):
                            raise RuntimeError('episode residue after reset')
                    receipt['reset_verified'] = True
                else:
                    receipt['reset_verified'] = False
            except Exception as exc:
                receipt['reset_verified'] = False
                receipt['status'] = 'capture_failed'
                if receipt['failure_reason']:
                    receipt['capture_failure_reason'] = receipt['failure_reason']
                receipt['failure_reason'] = f'reset: {exc}'
            save_receipt()
    return receipt


def validate_private_capture_plan(plan):
    """Re-validate a compiled ``pb-gui-private-capture-plan.v1`` before ``run``.

    ``pb_gui_capture_compile.py`` already verified the PB GUI/backend/receipt/gate
    chain; this is the capture entry's own fail-closed re-check so a hand-edited
    plan cannot smuggle unknown fields, an unregistered app/view, a fixture that
    does not seed the packet subjects or a private/absolute path into the runner.
    Existing helpers are reused: ``check_pb_clock_binding`` for the PB clock
    window and ``pb_backend_plan`` for the GT/Need/Oracle and local-path scans.
    """
    if not isinstance(plan, dict) or plan.get('schema_version') != PRIVATE_PLAN_SCHEMA:
        raise ValueError(f'private capture plan must declare schema_version '
                         f'{PRIVATE_PLAN_SCHEMA!r}')
    unknown = sorted(set(plan) - PRIVATE_PLAN_FIELDS)
    missing = sorted(PRIVATE_PLAN_FIELDS - set(plan))
    if unknown:
        raise ValueError(f'private capture plan carries unknown fields: {unknown}')
    if missing:
        raise ValueError(f'private capture plan misses required fields: {missing}')
    if plan.get('status') != 'compiled' or plan.get('command') != 'compile':
        raise ValueError(f'private capture plan must be a compiled artifact, got status='
                         f'{plan.get("status")!r} command={plan.get("command")!r}')
    for field in ('generated_at', 'episode_id', 'baseline_ref', 'device_timezone', 'eof_at'):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValueError(f'private capture plan {field} must be a nonempty string')
    try:
        ZoneInfo(plan['device_timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'private capture plan device_timezone '
                         f'{plan["device_timezone"]!r} is unknown') from None
    scope = plan.get('episode_scope')
    scope_fields = {'cluster_id', 'episode_id', 'passive_id', 'world_variant_id', 'track',
                    'decision_point', 'timezone'}
    if not isinstance(scope, dict) or set(scope) != scope_fields:
        raise ValueError(f'private capture plan episode_scope must carry exactly '
                         f'{sorted(scope_fields)}')
    for field in ('cluster_id', 'episode_id', 'passive_id', 'world_variant_id',
                  'decision_point', 'timezone'):
        if not isinstance(scope.get(field), str) or not scope[field].strip():
            raise ValueError(f'private capture plan episode_scope {field} must be a nonempty '
                             f'string')
    if scope.get('track') is not None and (not isinstance(scope['track'], str)
                                           or not scope['track'].strip()):
        raise ValueError('private capture plan episode_scope track must be null or a nonempty '
                         'string')
    for field in ('cluster_id', 'episode_id', 'passive_id', 'world_variant_id'):
        if scope[field] != plan.get(field):
            raise ValueError(f'private capture plan {field} {plan.get(field)!r} does not match '
                             f'episode_scope {scope[field]!r}')
    if scope['decision_point'] != plan['eof_at']:
        raise ValueError('private capture plan episode_scope decision_point does not match eof_at')
    if scope['timezone'] != plan['device_timezone']:
        raise ValueError('private capture plan episode_scope timezone does not match '
                         'device_timezone')
    fixture = plan.get('fixture')
    if not isinstance(fixture, dict) or set(fixture) != {'episode_scope', 'common_records',
                                                         'world_deltas', 'ku_records'}:
        raise ValueError('private capture plan fixture must declare exactly episode_scope, '
                         'common_records, world_deltas, ku_records')
    if fixture['episode_scope'] != plan['episode_id']:
        raise ValueError('private capture plan fixture episode_scope does not match episode_id')
    if fixture['common_records'] != [] or fixture['world_deltas'] != []:
        raise ValueError('private capture plan fixture must not carry common_records or '
                         'world_deltas')
    records = fixture['ku_records']
    if not isinstance(records, list) or not records:
        raise ValueError('private capture plan fixture ku_records must be a nonempty list')
    index = {}
    for position, record in enumerate(records, start=1):
        label = f'private capture plan fixture record #{position}'
        if (not isinstance(record, dict) or set(record) != {'app_id', 'entity_type',
                                                             'semantic_ref', 'data'}
                or not isinstance(record.get('data'), dict)):
            raise ValueError(f'{label} must carry exactly app_id, entity_type, semantic_ref, '
                             f'data')
        app_id, ref = record.get('app_id'), record.get('semantic_ref')
        if not isinstance(app_id, str) or not app_id.strip() \
                or not isinstance(ref, str) or not ref.strip() \
                or not isinstance(record['entity_type'], str) or not record['entity_type'].strip():
            raise ValueError(f'{label} requires nonempty string identity fields')
        key = (app_id, ref)
        if key in index:
            raise ValueError(f'private capture plan fixture record {key[1]!r} is duplicated')
        index[key] = record
    packets = plan.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('private capture plan packets must be a nonempty list')
    seen_ids, seen_frames, prior_sequence = set(), set(), None
    for position, packet in enumerate(packets, start=1):
        label = f'private capture plan packet #{position}'
        if (not isinstance(packet, dict)
                or set(packet) - PRIVATE_PACKET_FIELDS - {'history_replays'}
                or PRIVATE_PACKET_FIELDS - set(packet)):
            raise ValueError(f'{label} must carry exactly {sorted(PRIVATE_PACKET_FIELDS)} and '
                             f'optional history_replays')
        packet_id = packet.get('packet_id')
        if not isinstance(packet_id, str) or not packet_id.strip() or packet_id in seen_ids:
            raise ValueError(f'{label} packet_id must be a unique nonempty string')
        seen_ids.add(packet_id)
        sequence = packet.get('sequence')
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError(f'{label} sequence must be a positive integer')
        if prior_sequence is not None and sequence <= prior_sequence:
            raise ValueError(f'{label} sequence must strictly increase')
        prior_sequence = sequence
        if packet.get('tag') != 'gui':
            raise ValueError(f'{label} tag must be gui')
        app = packet.get('source')
        if not isinstance(app, str) or not app.strip():
            raise ValueError(f'{label} source must be a nonempty string')
        views = (CAPABILITIES['extension_apps'].get(app)
                 or CAPABILITIES['core_apps'].get(app, {})).get('views', {})
        if not views:
            raise ValueError(f'{label} source {app!r} is not a declared GUI app')
        capture = packet.get('capture')
        if not isinstance(capture, dict) or capture.get('surface') != 'app_screen':
            raise ValueError(f'{label} capture must declare surface=app_screen')
        capture_fields = {'surface', 'app_id', 'view_id', 'subject_ref', 'visible_assertions',
                          'expected_visible', 'frames', 'timeout_seconds', 'navigation_mode'}
        unknown_capture = sorted(set(capture) - capture_fields)
        if unknown_capture:
            raise ValueError(f'{label} capture carries unknown fields: {unknown_capture}')
        if capture.get('app_id') != app:
            raise ValueError(f'{label} capture app_id does not match source')
        view, subject_ref = capture.get('view_id'), capture.get('subject_ref')
        if not isinstance(view, str) or view not in views:
            raise ValueError(f'{label} view {view!r} is not declared for app {app!r}')
        if not isinstance(subject_ref, str) or (app, subject_ref) not in index:
            raise ValueError(f'{label} subject {capture.get("subject_ref")!r} is not in the '
                             f'private capture plan fixture')
        navigation_mode = capture.get('navigation_mode')
        if (app == 'calendar') != (navigation_mode == CALENDAR_DIRECT_NAVIGATION):
            raise ValueError(f'{label} capture must declare '
                             f'navigation_mode={CALENDAR_DIRECT_NAVIGATION!r} exactly for calendar '
                             f'packets, got {navigation_mode!r}')
        if (not isinstance(capture.get('visible_assertions'), list)
                or not capture['visible_assertions']
                or not isinstance(capture.get('expected_visible'), dict)):
            raise ValueError(f'{label} capture requires visible_assertions and expected_visible')
        frames = capture.get('frames')
        if (not isinstance(frames, list) or len(frames) != 1 or not isinstance(frames[0], dict)
                or set(frames[0]) != {'frame_id'}):
            raise ValueError(f'{label} capture requires exactly one frame_id frame')
        frame_id = frames[0]['frame_id']
        if not isinstance(frame_id, str) or not frame_id.strip() or frame_id in seen_frames:
            raise ValueError(f'{label} frame_id must be a unique nonempty string')
        seen_frames.add(frame_id)
        timeout = capture.get('timeout_seconds')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) \
                or not 1 <= timeout <= 120:
            raise ValueError(f'{label} capture timeout_seconds is invalid')
        replays = packet.get('history_replays')
        if replays is not None and (not isinstance(replays, list) or not replays):
            raise ValueError(f'{label} history_replays must be a nonempty list when present')
        for replay_position, replay in enumerate(replays or (), start=1):
            replay_label = f'{label} history_replays[{replay_position}]'
            if (not isinstance(replay, dict)
                    or set(replay) != {'semantic_ref', 'effective_at', 'set'}
                    or not isinstance(replay.get('semantic_ref'), str)
                    or not replay['semantic_ref'].strip()
                    or not isinstance(replay.get('effective_at'), str)
                    or not replay['effective_at'].strip()
                    or not isinstance(replay.get('set'), dict)):
                raise ValueError(f'{replay_label} must carry exactly semantic_ref, effective_at, '
                                 f'set')
    if plan.get('unsupported_packets') != []:
        raise ValueError('private capture plan must not carry unsupported_packets')
    if not isinstance(plan.get('counts'), dict) or not isinstance(plan.get('checks'), dict):
        raise ValueError('private capture plan counts and checks must be objects')
    assets = plan.get('source_assets')
    if not isinstance(assets, dict) or not assets:
        raise ValueError('private capture plan source_assets must be a nonempty object')
    for name, entry in assets.items():
        if (not isinstance(name, str) or not name or not isinstance(entry, dict)
                or set(entry) != {'schema_version', 'sha256'}
                or not isinstance(entry['schema_version'], str) or not entry['schema_version']
                or not isinstance(entry['sha256'], str) or not HEX64.fullmatch(entry['sha256'])):
            raise ValueError(f'private capture plan source_assets.{name} must bind a '
                             f'schema_version and a 64-hex sha256')
    import pb_backend_plan as pb
    leaks = pb.private_key_paths(plan)
    if leaks:
        raise ValueError(f'private GT/Need/Oracle fields leaked into the private capture plan: '
                         f'{leaks}')
    absolutes = pb.absolute_value_paths(plan)
    if absolutes:
        raise ValueError(f'local absolute paths leaked into the private capture plan: '
                         f'{absolutes}')
    check_pb_clock_binding(plan)
    return plan


def load_capture_plan(source):
    """Load a compiled ``pb-gui-private-capture-plan.v1`` CLI plan input."""
    return validate_private_capture_plan(json.loads(Path(source).read_text()))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('author', type=Path)
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    plan = load_capture_plan(args.author)
    result = run(plan, args.instance_id, args.output)
    print(json.dumps({'status': result['status'], 'receipt': str(args.output / 'receipt.json'),
                      'failure_reason': result['failure_reason']}))
    return 0 if result['status'] == 'capture_complete' else 1


if __name__ == '__main__':
    sys.exit(main())
