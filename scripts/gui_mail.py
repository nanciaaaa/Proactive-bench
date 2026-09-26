"""Gmailclone app-screen capture adapter; it does not publish system notifications."""
import hashlib
import json
import re
import tempfile
import time
from pathlib import Path

PACKAGE = 'com.gmailclone'
STATE = '/sdcard/Android/data/com.gmailclone/files/state.json'
# Only the declared, verifiable history transitions may be replayed on the device.
STATUS_TRANSITIONS = frozenset({('unread', 'read')})
# Inbox list rows have no resource ids on the real APK, so a row is bound by the
# exact text of its subject, sender and full snippet TextViews inside one
# container. ``sender|subject|snippet`` are the only machine-asserted fields;
# the unread bold / read gray weight and the bottom count badge are not asserted.
ROW_FIELD = re.compile(r'^row_(\d+)_(sender|subject|snippet)$')


def read_state(adb, serial):
    state = json.loads(adb(serial, 'shell', 'cat', STATE))
    if not isinstance(state.get('mails'), list):
        raise RuntimeError('Mail state has no message list')
    return state


def write_state(adb, serial, state):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'state.json'
        path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf8')
        adb(serial, 'push', str(path), STATE)


def find_mail(state, record):
    subject = record['data']['headers']['subject']
    return [mail for mail in state['mails'] if mail.get('headers', {}).get('subject') == subject]


def read_mail(adb, serial, record):
    state = read_state(adb, serial)
    matches = find_mail(state, record)
    if len(matches) != 1 or matches[0] != record['data']:
        raise RuntimeError('Mail message identity or authority changed')
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_mail_state(adb, serial, records):
    """Verify every fixture record in the live state and hash the whole state.

    Inbox packets assert two rows at once, so each fixture mail record must
    match the app-owned state at that packet's instant (including the replayed
    read/unread status); anything else aborts the capture.
    """
    state = read_state(adb, serial)
    for record in records:
        matches = find_mail(state, record)
        if len(matches) != 1 or matches[0] != record['data']:
            raise RuntimeError('Mail message identity or authority changed')
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text_nodes_in(node):
    return [child for child in node.iter('node')
            if child.attrib.get('class') == 'android.widget.TextView']


def bind_inbox_row(parents, nodes, subject, sender, snippet):
    """Bind one Inbox row to its exact subject/sender/snippet TextViews.

    The subject is unique on screen; walking up from it finds the lowest
    ancestor that holds exactly one sender and one snippet node, which is the
    row container. A detail page can bind at most one row and never both rows of
    the inbox assertion.
    """
    subjects = [node for node in nodes if node.attrib.get('text') == subject]
    if len(subjects) != 1:
        return None
    node = subjects[0]
    while node is not None:
        senders = [child for child in text_nodes_in(node) if child.attrib.get('text') == sender]
        snippets = [child for child in text_nodes_in(node) if child.attrib.get('text') == snippet]
        if len(senders) == 1 and len(snippets) == 1:
            return {'sender': senders[0], 'subject': subjects[0], 'snippet': snippets[0]}
        node = parents.get(node)
    return None


def ready_mail_inbox(ui_text, serial, expected, timeout):
    """Wait for the real Gmailclone Inbox list and bind every asserted row.

    Each declared ``row_N_sender|subject|snippet`` triple must appear as exact
    TextView texts inside one row container. No navigation tap is issued, so a
    mail detail page can satisfy at most one row and fails the two-row
    assertion; the unread bold/gray styling and the bottom badge are never
    asserted because the real device does not expose them reliably.
    """
    rows = {}
    for field in expected:
        match = ROW_FIELD.fullmatch(field)
        if match is None:
            raise RuntimeError(f'unsupported Mail inbox visible assertion {field!r}')
        rows.setdefault(f'row_{match.group(1)}', set()).add(match.group(2))
    if any(parts != {'sender', 'subject', 'snippet'} for parts in rows.values()):
        raise RuntimeError('Mail inbox assertions must declare sender, subject and snippet per row')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root, xml = ui_text(serial)
        parents = {child: parent for parent in root.iter('node') for child in parent}
        nodes = [node for node in root.iter('node') if node.attrib.get('package') == PACKAGE
                 and node.attrib.get('class') == 'android.widget.TextView']
        matched = {}
        for prefix in sorted(rows):
            row = bind_inbox_row(parents, nodes, expected[f'{prefix}_subject'],
                                 expected[f'{prefix}_sender'], expected[f'{prefix}_snippet'])
            if row is not None:
                matched[prefix] = row
        if len(matched) == len(rows):
            return {'ui_dump_sha256': hashlib.sha256(xml).hexdigest(),
                    'visible_assertions': {field: True for field in expected}}
        time.sleep(.5)
    raise RuntimeError(f'mail inbox not visible within {timeout}s')


def seed_mail(adb, serial, record):
    adb(serial, 'shell', 'am', 'force-stop', PACKAGE)
    state = read_state(adb, serial)
    if find_mail(state, record):
        raise RuntimeError('Mail subject already exists in baseline')
    state['mails'].insert(0, record['data'])
    write_state(adb, serial, state)
    return read_mail(adb, serial, record)


def apply_history_change(adb, serial, record):
    """Replay one declared PB history change through the app's own state path.

    Only the ``unread -> read`` Mail status transition is accepted, and both the
    observed start status and the declared end status are verified before the
    state file is written back. Returns the readback hash of the changed record.
    """
    data = record['data']
    target = data.get('status')
    adb(serial, 'shell', 'am', 'force-stop', PACKAGE)
    state = read_state(adb, serial)
    matches = find_mail(state, record)
    if len(matches) != 1:
        raise RuntimeError('Mail history replay identity is missing or ambiguous')
    current = matches[0].get('status')
    if (current, target) not in STATUS_TRANSITIONS:
        raise RuntimeError(f'Mail history replay rejects the {current!r} -> {target!r} status transition')
    matches[0]['status'] = target
    write_state(adb, serial, state)
    return read_mail(adb, serial, record)


def assert_absent(adb, serial, records):
    state = read_state(adb, serial)
    if any(mail.get('headers', {}).get('subject') == record['data']['headers']['subject']
           for record in records for mail in state['mails']):
        raise RuntimeError('Mail message remains after reset')
