"""Native Google Messages SMS capture adapter for the emulator."""
import hashlib
import re
import time

PACKAGE = 'com.google.android.apps.messaging'


def _text(value):
    return str(value).replace('\n', ' ').strip()


def seed_message(adb, serial, record):
    data = record['data']
    sender = data.get('sender_phone', data['sender'])
    body = data['body']
    adb(serial, 'emu', 'sms', 'send', sender, body)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            read_message(adb, serial, record)
            break
        except RuntimeError:
            time.sleep(.5)
    else:
        raise RuntimeError('Messages SMS authority did not settle after injection')
    return {'transport': 'adb emu sms send', 'sender': sender,
            'body_sha256': hashlib.sha256(body.encode()).hexdigest()}


def read_message(adb, serial, record):
    data = record['data']
    sender = data.get('sender_phone', data['sender'])
    output = adb(serial, 'shell', 'content', 'query', '--uri', 'content://sms/inbox',
                 '--projection', 'address:body:date:read', '--where', f"address='{sender}'")
    lines = output.decode(errors='replace').splitlines()
    matches = [line for line in lines if f"body={data['body']}" in line]
    if len(matches) != 1:
        raise RuntimeError('Messages SMS authority is missing or ambiguous')
    return {'sender': data['sender'], 'body': data['body'],
            'body_sha256': hashlib.sha256(data['body'].encode()).hexdigest(),
            'provider_row': matches[0]}


def ready_message(ui_text, serial, record, timeout):
    expected = record['data']
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = [node for node in root.iter('node') if node.attrib.get('package') == PACKAGE]
        visible = [_text(node.attrib.get('text', '')) for node in nodes]
        sender_digits = re.sub(r'\D', '', expected['sender'])
        sender_visible = (any(expected['sender'] == value for value in visible) if not sender_digits else any(
            sender_digits == re.sub(r'\D', '', value)
            or (len(sender_digits) >= 10 and re.sub(r'\D', '', value) == sender_digits[-10:])
            for value in visible))
        if sender_visible and any(expected['body'] in value for value in visible):
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {'sender': True, 'body': True}}
        time.sleep(.5)
    raise RuntimeError('Messages sender or body is not visible')


def assert_absent(adb, serial, records):
    for record in records:
        try:
            read_message(adb, serial, record)
        except RuntimeError as error:
            if str(error) == 'Messages SMS authority is missing or ambiguous':
                continue
            raise
        raise RuntimeError('Messages SMS record remains after reset')
