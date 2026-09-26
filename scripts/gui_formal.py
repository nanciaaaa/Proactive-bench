#!/usr/bin/env python3
"""Compile a versioned, private benchmark asset bundle into a GUI capture plan."""
import argparse
import copy
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from gui_contract import (assert_public_equivalent, calendar_visible, clock_visible, compile_capture_plan, digest,
                          mail_visible, messages_visible, timestamp)

REFS = ('global_base', 'cluster_base', 'world_variant', 'passive', 'compatibility', 'oracle')


def compile_formal_plan(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if set(manifest) != {'episode_id', 'public_group', 'baseline_ref', 'device_timezone',
                         'eof_at', *(name + '_ref' for name in REFS)}:
        raise ValueError('formal manifest has missing or unknown fields')
    if any(not isinstance(manifest[name + '_ref'], str) for name in REFS):
        raise ValueError('formal asset reference must be a string')
    paths = {name: (manifest_path.parent / manifest[name + '_ref']).resolve() for name in REFS}
    if any(not path.is_relative_to(manifest_path.parent) for path in paths.values()):
        raise ValueError('formal asset reference escapes manifest directory')
    assets = {name: json.loads(path.read_text()) for name, path in paths.items()}
    variant, passive = assets['world_variant'], assets['passive']
    legal = assets['compatibility']['edges']
    if {'passive_id': passive['passive_id'], 'variant_id': variant['variant_id']} not in legal:
        raise ValueError('World and Passive are not a legal combination')
    records = copy.deepcopy(assets['global_base']['records'] + assets['cluster_base']['records'])
    index = {(r['app_id'], r['semantic_ref']): r for r in records}
    if len(index) != len(records):
        raise ValueError('base records have duplicate entity identity')
    base_data = {key: copy.deepcopy(record['data']) for key, record in index.items()}
    for patch in variant['patches']:
        if patch.get('op') != 'update' or set(patch) != {'op', 'app_id', 'semantic_ref', 'set'}:
            raise ValueError('unsupported World patch')
        record = index.get((patch['app_id'], patch['semantic_ref']))
        if record is None or not isinstance(patch['set'], dict):
            raise ValueError('World patch target is missing')
        for field, value in patch['set'].items():
            if field not in record['data'] or record['data'][field] == value:
                raise ValueError('World patch must change an existing field')
            record['data'][field] = value
    changed = {f'{app}.{ref}.{field}' for (app, ref), record in index.items()
               for field, value in record['data'].items() if base_data[app, ref][field] != value}
    if changed != set(variant['allowed_changed_fields']):
        raise ValueError('World patch differs from declared changed fields')
    fixture = {'episode_scope': manifest['episode_id'],
               'common_records': [r for r in records if r['app_id'] in ('dudu', 'lianqi', 'starrail',
                                                                        'babaquna', 'wangyiyun', 'chaowei')],
               'ku_records': [r for r in records if r['app_id'] not in ('dudu', 'lianqi', 'starrail',
                                                                         'babaquna', 'wangyiyun', 'chaowei')],
               'world_deltas': []}
    author = {key: manifest[key] for key in ('episode_id', 'public_group', 'baseline_ref',
                                              'device_timezone', 'eof_at')}
    author.update({'fixture_ref': 'fixture.json', 'packets': passive['packets']})
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'author.json').write_text(json.dumps(author))
        (root / 'fixture.json').write_text(json.dumps(fixture))
        goal_data = assets['cluster_base'].get('goal')
        goal_app = goal_data.get('app_id') if isinstance(goal_data, dict) else None
        plan = compile_capture_plan(root / 'author.json', allowed_unobserved_apps=(goal_app,))
    oracle = assets['oracle']
    if oracle.get('episode_id') != manifest['episode_id'] or oracle.get('variant_id') != variant['variant_id']:
        raise ValueError('oracle does not bind this Episode and World')
    goal = assets['cluster_base'].get('goal')
    if not isinstance(goal, dict):
        raise ValueError('unsupported or missing cluster goal')
    target = index.get((goal.get('app_id'), goal.get('semantic_ref')))
    if goal.get('type') == 'ensure_file_content' and set(goal) == {'type', 'app_id', 'semantic_ref', 'approved_content'}:
        if target is None or target['app_id'] != 'files' or not isinstance(goal['approved_content'], str):
            raise ValueError('cluster goal target or approved content is invalid')
        need = [] if target['data']['content'] == goal['approved_content'] else [
            {'type': 'restore_file_content', 'target': goal['semantic_ref'], 'content': goal['approved_content']}]
        public_rule = (f"Keep the approved content {goal['approved_content']} in "
                       f"{target['data']['path']}; restore it if the current bytes differ.")
    elif goal.get('type') == 'sync_event_time' and set(goal) == {'type', 'app_id', 'semantic_ref', 'source_mail_ref', 'source_sender', 'approved_start_at', 'approved_end_at'}:
        mail = index.get(('mail', goal['source_mail_ref']))
        if target is None or target['app_id'] != 'calendar' or mail is None:
            raise ValueError('appointment goal target or source mail is missing')
        approved = (goal['approved_start_at'], goal['approved_end_at'])
        calendar_visible(target['data'])
        mail_visible(mail['data'])
        start, end = map(timestamp, approved)
        if (start >= end or start.date() != end.date() or start.utcoffset() != end.utcoffset()
                or mail['data']['headers']['sender'] != goal['source_sender']):
            raise ValueError('appointment goal time or sender is invalid')
        message = mail['data']['body']
        if not all(value in message for value in (target['data']['title'], *approved)):
            raise ValueError('appointment goal differs from source mail body')
        need = [] if (target['data']['start_at'], target['data']['end_at']) == approved else [
            {'type': 'update_calendar_event', 'target': goal['semantic_ref'],
             'start_at': approved[0], 'end_at': approved[1]}]
        public_rule = (f"For {target['data']['title']}, synchronize the Calendar event time with "
                       f"the update from {goal['source_sender']}: {approved[0]} to {approved[1]}; "
                       "preserve other event fields.")
    elif goal.get('type') == 'sync_contact_identity' and set(goal) == {
            'type', 'app_id', 'semantic_ref', 'source_message_ref', 'source_sender',
            'approved_phone', 'approved_body'}:
        message = index.get(('messages', goal['source_message_ref']))
        if (target is None or target['app_id'] != 'contacts' or message is None
                or message['entity_type'] != 'messages'):
            raise ValueError('contact goal target or source message is missing')
        messages_visible(message['data'])
        if (message['data']['sender'] != goal['source_sender']
                or message['data']['body'] != goal['approved_body']):
            raise ValueError('contact goal source label or body is invalid')
        approved_phone = goal['approved_phone']
        if not isinstance(approved_phone, str) or not approved_phone.isdigit() or not 7 <= len(approved_phone) <= 15:
            raise ValueError('contact goal approved phone is invalid')
        if target['data']['phone'] != approved_phone or message['data'].get('sender_phone') != approved_phone:
            need = [{'type': 'update_contact_phone', 'target': goal['semantic_ref'], 'phone': approved_phone}]
        else:
            need = []
        public_rule = (f"Keep the contact label {goal['source_sender']} bound to phone {approved_phone} "
                       f"for the incoming message: {goal['approved_body']}; preserve the message body.")
    elif goal.get('type') == 'ensure_reminder_coverage' and set(goal) == {
            'type', 'app_id', 'semantic_ref', 'source_mail_ref', 'source_sender', 'activity_title',
            'approved_start_at', 'approved_end_at', 'approved_reminder_minutes_before', 'declared_channel'}:
        if goal['declared_channel'] != 'clock':
            raise ValueError('reminder pilot only supports the restricted Clock single-channel scope')
        mail = index.get(('mail', goal['source_mail_ref']))
        if (target is None or target['app_id'] != 'clock' or target['entity_type'] != 'alarms'
                or mail is None or mail['entity_type'] != 'mails'):
            raise ValueError('reminder goal target or source mail is missing')
        if len(variant['patches']) != sum(
                (patch['app_id'], patch['semantic_ref']) == (goal['app_id'], goal['semantic_ref'])
                for patch in variant['patches']):
            raise ValueError('reminder World may only change the private target alarm')
        if any(set(patch['set']) - {'time', 'enabled'} for patch in variant['patches']):
            raise ValueError('reminder World may only change the private target alarm time or enabled state')
        if any(isinstance(packet.get('capture'), dict)
               and packet['capture'].get('app_id') == goal['app_id']
               and packet['capture'].get('subject_ref') == goal['semantic_ref']
               for packet in passive['packets']):
            raise ValueError('reminder goal target must stay private until EOF')
        mail_visible(mail['data'])
        clock_visible(target['data'])
        title = goal['activity_title']
        if not isinstance(title, str) or not title.isascii() or not title.isalnum():
            raise ValueError('reminder goal activity title is invalid')
        lead = goal['approved_reminder_minutes_before']
        start, end = map(timestamp, (goal['approved_start_at'], goal['approved_end_at']))
        if (start >= end or start.date() != end.date() or start.utcoffset() != end.utcoffset()
                or not isinstance(lead, int) or isinstance(lead, bool) or not 1 <= lead <= 1439):
            raise ValueError('reminder goal time is invalid')
        zone = ZoneInfo(manifest['device_timezone'])
        if (start.astimezone(zone).utcoffset() != start.utcoffset()
                or end.astimezone(zone).utcoffset() != end.utcoffset()):
            raise ValueError('reminder goal time zone differs from the device time zone')
        reminder_at = start - timedelta(minutes=lead)
        if reminder_at.date() != start.date():
            raise ValueError('reminder goal lead must stay on the activity date')
        body = mail['data']['body']
        if (mail['data']['headers']['sender'] != goal['source_sender'] or title not in body
                or not all(value in body for value in (goal['approved_start_at'], goal['approved_end_at']))
                or f'{lead} minutes before' not in body):
            raise ValueError('reminder goal differs from source mail')
        if target['data']['label'] != title:
            raise ValueError('reminder goal alarm label differs from the activity title')
        observed = [timestamp(packet.get('observed_at')) for packet in passive['packets']
                    if isinstance(packet.get('capture'), dict)
                    and packet['capture'].get('subject_ref') == goal['source_mail_ref']]
        if (not observed or max(observed) >= reminder_at
                or timestamp(manifest['eof_at']) >= reminder_at):
            raise ValueError('reminder goal is not observable before the target reminder time')
        reminder_time = reminder_at.strftime('%H:%M')
        need = [] if (target['data']['time'] == reminder_time and target['data']['enabled'] is True) else [
            {'type': 'update_clock_alarm', 'target': goal['semantic_ref'], 'time': reminder_time, 'enabled': True}]
        public_rule = (f"Keep the Clock alarm {title} enabled at {reminder_time} as the {lead}-minute reminder "
                       f"for the activity {goal['approved_start_at']} to {goal['approved_end_at']}; the declared "
                       "reminder channel for this Episode is Clock only.")
    else:
        raise ValueError('unsupported or missing cluster goal')
    if oracle.get('expected_need_set') != need or oracle.get('passive_class') != 'active_required':
        raise ValueError('oracle Need Set does not match resolved World')
    plan['source_assets'] = {'manifest': digest(manifest_path),
                             **{name: digest(path) for name, path in paths.items()}}
    plan['source_paths'] = {'manifest': str(manifest_path),
                            **{name: str(path) for name, path in paths.items()}}
    plan['public_rule'] = public_rule
    plan['formal'] = {'passive_id': passive['passive_id'], 'variant_id': variant['variant_id'],
                      'oracle': oracle}
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    plans = [compile_formal_plan(path) for path in args.manifest]
    if len(plans) > 1:
        assert_public_equivalent(plans)
    encoded = json.dumps(plans[0] if len(plans) == 1 else plans, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end='')
