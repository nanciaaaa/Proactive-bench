#!/usr/bin/env python3
"""Compile a private author episode into a device capture plan."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = json.loads((ROOT / 'contracts/gui-capabilities.json').read_text())
PUBLIC_PACKET = {'packet_id', 'sequence', 'type', 'source', 'observed_at', 'delivered_at', 'text_projection'}
PRIVATE_WORDS = ('world_id', 'gold', 'label', 'need_set', 'gt', 'answer')
VIEW_ENTITY = {'popup': 'popup', 'article_detail': 'articles', 'event_editor': 'events',
               'mail_detail': 'mails', 'alarm_card': 'alarms', 'contact_detail': 'contacts',
               'download_file': 'files', 'docreader_content': 'files',
               'conversation_list': 'messages'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('timestamp requires timezone')
    return parsed


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calendar_visible(data):
    required = {'title', 'start_at', 'end_at', 'location', 'description', 'reminder_minutes_before', 'source'}
    if set(data) != required or not isinstance(data['title'], str) or not data['title'].isascii() or not data['title'].isalnum():
        raise ValueError('Calendar pilot requires ASCII alphanumeric title and complete event data')
    start, end = timestamp(data['start_at']), timestamp(data['end_at'])
    reminder = data['reminder_minutes_before']
    if end <= start or start.date() != end.date() or not isinstance(reminder, int) or isinstance(reminder, bool) or reminder != 30:
        raise ValueError('Calendar pilot supports same-day events with 30-minute reminder')
    if any(not isinstance(data[field], str) or not data[field] for field in ('location', 'description', 'source')):
        raise ValueError('Calendar text/source fields required')
    return {**data, 'start_at': start.strftime('%I:%M %p'), 'end_at': end.strftime('%I:%M %p'),
            'reminder_minutes_before': '30 minutes before'}


def mail_visible(data):
    headers = data.get('headers')
    if (set(data) != {'headers', 'body', 'status'} or not isinstance(headers, dict)
            or set(headers) != {'subject', 'date', 'from', 'to', 'sender', 'senderLogo'}
            or data['status'] != 'read' or any(not isinstance(value, str) for value in headers.values())
            or any(not headers[field] for field in ('subject', 'date', 'from', 'to', 'sender'))
            or not isinstance(data['body'], str) or not data['body']):
        raise ValueError('Mail pilot requires a complete, already-read message')
    return {**headers, 'body': data['body']}


def clock_visible(data):
    if (set(data) != {'label', 'time', 'enabled'} or not isinstance(data['label'], str)
            or not data['label'].isascii() or not data['label'].isalnum()
            or not isinstance(data['enabled'], bool) or not isinstance(data['time'], str)):
        raise ValueError('Clock pilot requires a unique ASCII label, time, and enabled bool')
    try:
        hour, minute = map(int, data['time'].split(':'))
    except ValueError:
        raise ValueError('Clock time must be HH:MM') from None
    if len(data['time']) != 5 or not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError('Clock time must be HH:MM')
    return {'label': data['label'], 'time': f'{(hour - 1) % 12 + 1}:{minute:02d}{"AM" if hour < 12 else "PM"}',
            'enabled': str(data['enabled']).lower()}


def contact_visible(data):
    if (set(data) not in ({'name', 'phone'}, {'name', 'phone', 'email'}) or not isinstance(data['name'], str)
            or not data['name'].isascii() or not data['name'].isalnum()
            or not isinstance(data['phone'], str) or not data['phone'].isdigit()
            or not 7 <= len(data['phone']) <= 15):
        raise ValueError('Contacts pilot requires ASCII alphanumeric name and 7–15 digit phone')
    if 'email' in data and (not isinstance(data['email'], str)
                            or not re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', data['email'])):
        raise ValueError('Contacts email is invalid')
    return data


def file_visible(data):
    if (set(data) != {'path', 'content', 'modified_at'} or not isinstance(data['path'], str)
            or not isinstance(data['content'], str) or not data['content']
            or not data['path'].startswith('Download/')
            or not data['path'][9:].isascii() or not data['path'][9:].replace('.', '').isalnum()
            or len(data['path'][9:]) > 80):
        raise ValueError('Files pilot requires a simple Download filename and nonempty content')
    timestamp(data['modified_at'])
    return {'directory': 'Download', 'name': data['path'][9:]}


def messages_visible(data):
    if (set(data) not in ({'sender', 'body'}, {'sender', 'sender_phone', 'body'})
            or not isinstance(data['sender'], str) or not data['sender'] or '\n' in data['sender']
            or not isinstance(data['body'], str) or not data['body'] or '\n' in data['body']):
        raise ValueError('Messages pilot requires a sender label and nonempty single-line body')
    if 'sender_phone' in data and (not isinstance(data['sender_phone'], str)
                                   or not re.fullmatch(r'\+?[0-9]{7,15}', data['sender_phone'])):
        raise ValueError('Messages sender_phone is invalid')
    if not 'sender_phone' in data and not re.fullmatch(r'\+?[0-9]{7,15}', data['sender']):
        raise ValueError('Anonymous Messages sender must be a phone number')
    return data


def compile_capture_plan(source, allowed_unobserved_apps=()):
    source = Path(source).resolve()
    author = json.loads(source.read_text(encoding='utf8'))
    if not isinstance(author, dict) or set(author) - {'episode_id', 'public_group', 'baseline_ref',
                                                       'device_timezone', 'fixture_ref', 'packets', 'eof_at'}:
        raise ValueError('unrecognized author field')
    if not isinstance(author.get('fixture_ref'), str):
        raise ValueError('fixture_ref required')
    fixture_path = (source.parent / author['fixture_ref']).resolve()
    if not fixture_path.is_relative_to(source.parent):
        raise ValueError('fixture_ref escapes author asset directory')
    fixture = json.loads(fixture_path.read_text(encoding='utf8'))
    episode = author['episode_id']
    if not isinstance(episode, str) or not episode or fixture.get('episode_scope') != episode:
        raise ValueError('episode_scope mismatch')
    if not isinstance(author.get('baseline_ref'), str) or not author['baseline_ref']:
        raise ValueError('baseline_ref required')
    try:
        device_timezone = ZoneInfo(author['device_timezone'])
    except (KeyError, TypeError, ZoneInfoNotFoundError):
        raise ValueError('valid device_timezone required') from None
    if set(fixture) not in ({'episode_scope', 'common_records', 'world_deltas'},
                            {'episode_scope', 'common_records', 'world_deltas', 'ku_records'}):
        raise ValueError('fixture must declare extension records and optional KU records')
    if fixture['world_deltas']:
        raise ValueError('world_deltas require a validated event replay adapter')
    if not isinstance(fixture['common_records'], list) or not isinstance(fixture.get('ku_records', []), list):
        raise ValueError('fixture records must be lists')
    all_records = fixture['common_records'] + fixture.get('ku_records', [])
    for record in fixture['common_records']:
        if not isinstance(record, dict) or record.get('app_id') not in CAPABILITIES['extension_apps']:
            raise ValueError('common_records require an extension App')
    for record in all_records:
        if (not isinstance(record, dict) or not {'app_id', 'entity_type', 'semantic_ref', 'data'} <= record.keys()
                or not isinstance(record['semantic_ref'], str) or not record['semantic_ref']
                or not isinstance(record['data'], dict)):
            raise ValueError('invalid fixture record')
    records = {(r['app_id'], r['semantic_ref']): r for r in all_records}
    if len(records) != len(all_records):
        raise ValueError('duplicate semantic_ref')
    identity_fields = {'calendar': ('title',), 'mail': ('headers', 'subject'),
                       'clock': ('label',), 'contacts': ('phone',), 'files': ('path',),
                       'messages': ('body',)}
    identities = set()
    for record in fixture.get('ku_records', []):
        app = record['app_id']
        if app not in identity_fields:
            continue
        value = record['data']
        for field in identity_fields[app]:
            if not isinstance(value, dict):
                raise ValueError('invalid KU identity data')
            value = value.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError('duplicate or missing KU navigation identity')
        identity = (app, value)
        if identity in identities:
            raise ValueError('duplicate or missing KU navigation identity')
        identities.add(identity)
    if any((r['app_id'], r['entity_type']) not in {('calendar', 'events'), ('mail', 'mails'), ('clock', 'alarms'), ('contacts', 'contacts'), ('files', 'files'), ('messages', 'messages')}
           for r in fixture.get('ku_records', [])):
        raise ValueError('unverified KU record type')
    popup_apps = [r['app_id'] for r in fixture['common_records'] if r['entity_type'] == 'popup']
    if len(popup_apps) != len(set(popup_apps)):
        raise ValueError('only one popup per app can be displayed by the current adapter')
    packets = author.get('packets')
    if not isinstance(packets, list) or not packets:
        raise ValueError('packets required')
    seen_ids, seen_frames, prior_time, prior_delivery = set(), set(), None, None
    compiled = []
    for expected_sequence, packet in enumerate(packets, 1):
        if set(packet) - PUBLIC_PACKET - {'capture'}:
            raise ValueError('unrecognized packet field')
        if packet.get('sequence') != expected_sequence:
            raise ValueError('packet sequence must be contiguous')
        pid = packet.get('packet_id')
        if not isinstance(pid, str) or not pid or pid in seen_ids:
            raise ValueError('duplicate or missing packet_id')
        seen_ids.add(pid)
        if any(word in packet for word in PRIVATE_WORDS) or packet.get('screenshot_ref'):
            raise ValueError('private field or premature screenshot_ref in packet')
        observed = timestamp(packet.get('observed_at'))
        delivered = timestamp(packet.get('delivered_at', packet['observed_at']))
        if observed.astimezone(device_timezone).utcoffset() != observed.utcoffset():
            raise ValueError('packet timezone conflicts with device_timezone')
        if (delivered < observed or (prior_time is not None and observed < prior_time)
                or (prior_delivery is not None and delivered < prior_delivery)):
            raise ValueError('packet time order conflict')
        prior_time, prior_delivery = observed, delivered
        if packet.get('type') != 'gui' or packet.get('source') not in (CAPABILITIES['extension_apps'] | CAPABILITIES['core_apps']):
            raise ValueError('only declared GUI surfaces are supported')
        capture = packet.get('capture')
        if not isinstance(capture, dict) or capture.get('surface') != 'app_screen':
            raise ValueError('GUI packet requires app_screen capture')
        if set(capture) - {'surface', 'app_id', 'view_id', 'subject_ref', 'visible_assertions',
                           'timeout_seconds', 'frame_id', 'frames'} or ('frame_id' in capture) == ('frames' in capture):
            raise ValueError('capture must declare exactly one frame form')
        app, view, ref = capture.get('app_id'), capture.get('view_id'), capture.get('subject_ref')
        views = (CAPABILITIES['extension_apps'].get(app) or CAPABILITIES['core_apps'].get(app, {})).get('views', {})
        if app != packet['source'] or view not in views:
            raise ValueError('source or view is not supported')
        record = records.get((app, ref))
        if not record or record['entity_type'] != VIEW_ENTITY[view]:
            raise ValueError('capture subject has the wrong seeded entity type')
        fields = capture.get('visible_assertions')
        allowed = views[view]
        if not isinstance(fields, list) or not fields or len(fields) != len(set(fields)) or not set(fields) <= set(allowed):
            raise ValueError('unsupported or duplicate visible assertions')
        if view == 'article_detail' and 'title' not in fields:
            raise ValueError('article detail navigation requires title assertion')
        if app == 'clock' and set(fields) != {'label', 'time', 'enabled'}:
            raise ValueError('Clock alarm card requires label, time, and enabled assertions')
        if app == 'contacts' and set(fields) != set(record['data']):
            raise ValueError('Contacts detail requires assertions for every displayed field')
        if app == 'files' and set(fields) != ({'content'} if view == 'docreader_content' else {'directory', 'name'}):
            raise ValueError('Files view requires all declared visible assertions')
        visible = (calendar_visible(record['data']) if app == 'calendar' else
                   mail_visible(record['data']) if app == 'mail' else
                   clock_visible(record['data']) if app == 'clock' else record['data'])
        if app == 'contacts':
            visible = contact_visible(record['data'])
        if app == 'files':
            visible = file_visible(record['data'])
            if view == 'docreader_content':
                content = record['data']['content']
                if not content.isascii() or len(content) > 80 or '\n' in content:
                    raise ValueError('Docreader pilot requires short single-line ASCII content')
                visible = {'content': content}
        if app == 'messages':
            visible = messages_visible(record['data'])
        if app == 'calendar' and (timestamp(record['data']['start_at']).utcoffset() != observed.utcoffset()
                                  or timestamp(record['data']['end_at']).utcoffset() != observed.utcoffset()):
            raise ValueError('Calendar event timezone differs from observation')
        if app == 'files' and timestamp(record['data']['modified_at']) > observed:
            raise ValueError('Files modification time is after observation')
        if any(not isinstance(visible.get(field), str) or not visible[field] for field in fields):
            raise ValueError('visible assertion has no seeded value')
        timeout = capture.get('timeout_seconds', 20)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
            raise ValueError('invalid capture timeout')
        frames = capture.get('frames', [{'frame_id': capture.get('frame_id')}])
        if not isinstance(frames, list) or not frames:
            raise ValueError('frames required')
        if len(frames) != 1:
            raise ValueError('multi-frame capture requires a verified navigation adapter')
        for frame in frames:
            fid = frame.get('frame_id') if isinstance(frame, dict) else None
            if not isinstance(frame, dict) or set(frame) != {'frame_id'} or not isinstance(fid, str) or not fid or fid in seen_frames:
                raise ValueError('duplicate or missing frame_id')
            seen_frames.add(fid)
        if not isinstance(packet.get('text_projection'), str):
            raise ValueError('text_projection required for private QA')
        compiled.append({**{k: packet[k] for k in PUBLIC_PACKET if k in packet},
                         'capture': {'surface': 'app_screen', 'app_id': app, 'view_id': view,
                                     'subject_ref': ref, 'visible_assertions': fields,
                                     'expected_visible': {k: visible[k] for k in fields},
                                     'frames': frames, 'timeout_seconds': timeout}})
    eof = timestamp(author.get('eof_at'))
    if eof < prior_delivery:
        raise ValueError('EOF precedes last packet')
    apps = sorted({p['source'] for p in packets})
    if any(r['app_id'] not in apps and r['app_id'] not in allowed_unobserved_apps for r in all_records):
        raise ValueError('fixture contains an unplanned app')
    return {'schema_version': '1.0', 'episode_id': episode, 'baseline_ref': author['baseline_ref'],
            'device_timezone': author['device_timezone'],
            'source_assets': {'author': digest(source), 'fixture': digest(fixture_path)},
            'fixture': fixture, 'packets': compiled, 'eof_at': author['eof_at'],
            'public_group': author.get('public_group')}


def assert_public_equivalent(plans):
    def public(plan):
        records = {(r['app_id'], r['semantic_ref']): r['data'] for r in
                   plan['fixture']['common_records'] + plan['fixture'].get('ku_records', [])}
        result = []
        for p in plan['packets']:
            data = records[p['source'], p['capture']['subject_ref']]
            # Files Download listing does not render bytes; it does render the modification time.
            visible_record = ({'path': data['path'], 'modified_at': data['modified_at'],
                               'size': len(data['content'].encode())}
                              if p['capture']['view_id'] == 'download_file' else data)
            result.append((p['packet_id'], p['sequence'], p['type'], p['source'], p['observed_at'],
                           p.get('delivered_at'), p['text_projection'], p['capture']['expected_visible'],
                           visible_record, [f['frame_id'] for f in p['capture']['frames']]))
        return result
    if any(p['public_group'] != plans[0]['public_group'] or p.get('public_rule') != plans[0].get('public_rule')
           or public(p) != public(plans[0]) for p in plans[1:]):
        raise ValueError('counterfactual plans leak different pre-EOF public observations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('author', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    plans = [compile_capture_plan(p) for p in args.author]
    if len(plans) > 1:
        assert_public_equivalent(plans)
    payload = plans[0] if len(plans) == 1 else plans
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded, encoding='utf8')
    else:
        print(encoded, end='')
