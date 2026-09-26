"""Android Contacts detail capture for distinct contacts with the same name."""
import hashlib
import json
import re
import shlex
import time

PACKAGE = 'com.google.android.contacts'
DB = '/data/data/com.android.providers.contacts/databases/contacts2.db'


def query(adb, serial, sql):
    return json.loads(adb(serial, 'shell', f'su 0 sqlite3 -json {DB} {shlex.quote(sql)}') or b'[]')


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


def read_contact(adb, serial, record):
    data = record['data']
    rows = query(adb, serial, 'SELECT r._id,r.contact_id,r.display_name,r.aggregation_mode '
                 'FROM raw_contacts r JOIN data d ON d.raw_contact_id=r._id '
                 'JOIN mimetypes m ON m._id=d.mimetype_id '
                 f"WHERE m.mimetype='vnd.android.cursor.item/phone_v2' AND d.data1={quoted(data['phone'])};")
    if len(rows) != 1 or rows[0]['display_name'] != data['name'] or rows[0]['aggregation_mode'] != 3:
        raise RuntimeError('Contacts identity is missing, merged, or ambiguous')
    row = rows[0]
    values = query(adb, serial, 'SELECT m.mimetype,d.data1 FROM data d '
                   'JOIN mimetypes m ON m._id=d.mimetype_id '
                   f"WHERE d.raw_contact_id={row['_id']};")
    expected = {'vnd.android.cursor.item/name': data['name'],
                'vnd.android.cursor.item/phone_v2': data['phone']}
    if 'email' in data:
        expected['vnd.android.cursor.item/email_v2'] = data['email']
    if {value['mimetype']: value['data1'] for value in values} != expected or len(values) != len(expected):
        raise RuntimeError('Contacts data differs from seeded authority')
    return {**row, 'data': data}


def seed_contact(adb, serial, record):
    data = record['data']
    if query(adb, serial, "SELECT d._id FROM data d JOIN mimetypes m ON m._id=d.mimetype_id "
             f"WHERE m.mimetype='vnd.android.cursor.item/phone_v2' AND d.data1={quoted(data['phone'])};"):
        raise RuntimeError('Contacts phone already exists in baseline')
    before = query(adb, serial, 'SELECT COALESCE(MAX(_id),0) AS id FROM raw_contacts;')[0]['id']
    adb(serial, 'shell', 'content insert --uri content://com.android.contacts/raw_contacts '
        '--bind account_name:s:null --bind account_type:s:null --bind aggregation_mode:i:3')
    new = query(adb, serial, f'SELECT _id FROM raw_contacts WHERE _id>{before};')
    if len(new) != 1:
        raise RuntimeError('Contacts raw ID could not be isolated')
    raw_id = new[0]['_id']
    fields = [('name', data['name']), ('phone_v2', data['phone'])]
    if 'email' in data:
        fields.append(('email_v2', data['email']))
    for kind, value in fields:
        adb(serial, 'shell', 'content insert --uri content://com.android.contacts/data '
            f'--bind raw_contact_id:i:{raw_id} --bind mimetype:s:vnd.android.cursor.item/{kind} '
            f'--bind data1:s:{value}')
    row = read_contact(adb, serial, record)
    if row['_id'] != raw_id or row['contact_id'] is None:
        raise RuntimeError('Contacts inserted row was merged or replaced')
    return row


def assert_absent(adb, serial, records):
    for record in records:
        phone = record['data']['phone']
        if query(adb, serial, f'SELECT _id FROM data WHERE data1={quoted(phone)};'):
            raise RuntimeError('Contacts record remains after reset')


def ready_contact(ui_text, serial, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = [n for n in root.iter('node') if n.attrib.get('package') == PACKAGE]
        titles = [n for n in nodes if n.attrib.get('resource-id') == f'{PACKAGE}:id/large_title'
                  and n.attrib.get('text') == expected['name']]
        phones = [n for n in nodes if re.sub(r'\D', '', n.attrib.get('text', '')) == expected['phone']]
        emails = [n for n in nodes if n.attrib.get('text') == expected.get('email')]
        if len(titles) == len(phones) == 1 and ('email' not in expected or len(emails) == 1):
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {field: True for field in expected}}
        time.sleep(.5)
    raise RuntimeError('Contacts detail name, phone, or email not visible')
