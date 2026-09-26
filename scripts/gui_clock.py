"""Scoped Google DeskClock alarm-card capture adapter."""
import hashlib
import json
import shlex
import time
import uuid

DB = '/data/user_de/0/com.google.android.deskclock/databases/alarms.db'
PACKAGE = 'com.google.android.deskclock'
ACTIVITY = 'com.android.deskclock.DeskClock'


def alarm_key(episode, ref):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'gui-clock:{episode}:{ref}'))


def query(adb, serial, sql):
    return json.loads(adb(serial, 'shell', f'su 0 sqlite3 -json {DB} {shlex.quote(sql)}') or b'[]')


def read_alarm(adb, serial, episode, record):
    key = alarm_key(episode, record['semantic_ref'])
    rows = query(adb, serial, f"SELECT _id,external_uuid,hour,minutes,enabled,daysofweek,label "
                              f"FROM alarm_templates WHERE external_uuid='{key}';")
    if len(rows) != 1:
        raise RuntimeError('Clock alarm identity is missing or ambiguous')
    row = rows[0]
    hour, minute = map(int, record['data']['time'].split(':'))
    expected = {'external_uuid': key, 'hour': hour, 'minutes': minute,
                'enabled': int(record['data']['enabled']), 'daysofweek': 0, 'label': record['data']['label']}
    if any(row[field] != value for field, value in expected.items()):
        raise RuntimeError('Clock alarm differs from seeded authority')
    return row


def seed_alarm(adb, serial, episode, record):
    key = alarm_key(episode, record['semantic_ref'])
    adb(serial, 'shell', 'am', 'force-stop', PACKAGE)
    if query(adb, serial, f"SELECT _id FROM alarm_templates WHERE external_uuid='{key}' "
                          f"OR label='{record['data']['label']}';"):
        raise RuntimeError('Clock alarm exists before seed')
    hour, minute = map(int, record['data']['time'].split(':'))
    label = record['data']['label']
    query(adb, serial, "INSERT INTO alarm_templates(external_uuid,hour,minutes,enabled,daysofweek,"
                       "vibrate,label,ringtone,delete_after_use) VALUES "
                       f"('{key}',{hour},{minute},{int(record['data']['enabled'])},0,1,'{label}',"
                       "'content://settings/system/alarm_alert',0);")
    return read_alarm(adb, serial, episode, record)


def assert_absent(adb, serial, episode, records):
    for record in records:
        if query(adb, serial, f"SELECT _id FROM alarm_templates WHERE external_uuid="
                              f"'{alarm_key(episode, record['semantic_ref'])}';"):
            raise RuntimeError('Clock alarm remains after reset')


def ready_alarm(ui_text, serial, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        cards = [node for node in root.iter('node') if node.attrib.get('package') == PACKAGE
                 and node.attrib.get('resource-id') == f'{PACKAGE}:id/alarm_card_layout'
                 and any(child.attrib.get('resource-id') == f'{PACKAGE}:id/label'
                         and child.attrib.get('text') == expected['label'] for child in node.iter('node'))]
        if len(cards) == 1:
            children = list(cards[0].iter('node'))
            clock = [node.attrib.get('text', '') for node in children
                     if node.attrib.get('resource-id') == f'{PACKAGE}:id/digital_clock']
            switches = [node.attrib.get('checked') for node in children
                        if node.attrib.get('resource-id') == f'{PACKAGE}:id/onoff']
            if (len(clock) == len(switches) == 1
                    and ''.join(clock[0].split()) == expected['time']
                    and switches[0] == expected['enabled']):
                return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                        'visible_assertions': {field: True for field in expected}}
        time.sleep(.5)
    raise RuntimeError('Clock alarm card not visible or switch/time mismatch')
