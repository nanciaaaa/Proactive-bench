"""Android Files Download-list capture with independent byte-hash readback."""
import hashlib
import tempfile
import time
from pathlib import Path
from datetime import datetime

PACKAGE = 'com.google.android.documentsui'
READER_PACKAGE = 'at.tomtasche.reader'


def file_path(record):
    return '/sdcard/' + record['data']['path']


def read_file(adb, serial, record):
    path = file_path(record)
    output = adb(serial, 'shell', 'sha256sum', path).decode().strip()
    digest = output.split()[0]
    expected = hashlib.sha256(record['data']['content'].encode()).hexdigest()
    if digest != expected:
        raise RuntimeError('Files content hash differs from seeded World')
    modified_at = int(adb(serial, 'shell', 'stat', '-c', '%Y', path).decode().strip())
    if modified_at != int(datetime.fromisoformat(record['data']['modified_at']).timestamp()):
        raise RuntimeError('Files modification time differs from seeded World')
    return {'path': path, 'sha256': digest, 'modified_at': modified_at}


def seed_file(adb, serial, record):
    path = file_path(record)
    if adb(serial, 'shell', f'test -e {path} && echo exists || echo absent').strip() != b'absent':
        raise RuntimeError('Files target path exists in baseline')
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / Path(path).name
        local.write_bytes(record['data']['content'].encode())
        adb(serial, 'push', str(local), path)
    adb(serial, 'shell', 'touch', '-d', record['data']['modified_at'], path)
    return read_file(adb, serial, record)


def assert_absent(adb, serial, records):
    for record in records:
        if adb(serial, 'shell', f'test -e {file_path(record)} && echo exists || echo absent').strip() != b'absent':
            raise RuntimeError('Files path remains after reset')


def ready_file(ui_text, serial, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        nodes = [n for n in root.iter('node') if n.attrib.get('package') == PACKAGE]
        directory = [n for n in nodes if n.attrib.get('resource-id') == f'{PACKAGE}:id/header_title'
                     and n.attrib.get('text') == 'Files in ' + expected['directory']]
        files = [n for n in nodes if n.attrib.get('resource-id') == 'android:id/title'
                 and n.attrib.get('text') == expected['name']]
        if len(directory) == len(files) == 1:
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {'directory': True, 'name': True}}
        time.sleep(.5)
    raise RuntimeError('Files path or unique filename not visible')


def ready_docreader(ui_text, serial, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        content = [n for n in root.iter('node') if n.attrib.get('package') == READER_PACKAGE
                   and n.attrib.get('text') == expected['content']
                   and n.attrib.get('class') == 'android.view.View']
        if len(content) == 1:
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {'content': True}}
        time.sleep(.5)
    raise RuntimeError('Docreader content view did not become ready')
