#!/usr/bin/env python3
"""Fail-closed, pixel-backed QA for currently supported GUI surfaces."""
import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from gui_calendar import child_text
from gui_capture import DEVICE_ENV_FIELDS, png_size
from gui_mail import bind_inbox_row

ROOT = Path(__file__).resolve().parents[1]
RULE = 'gui-pixel-ocr-v8'
KNOWN_RULES = {'mail-detail-ocr-v1', 'gui-pixel-ocr-v2', 'gui-pixel-ocr-v3', 'gui-pixel-ocr-v4',
               'gui-pixel-ocr-v5', 'gui-pixel-ocr-v6', 'gui-pixel-ocr-v7', RULE}
DEVICE_ENV_SNAPSHOT_FIELDS = ('resolution', 'density', 'font_scale', 'locale', 'theme')
MIN_CONFIDENCE = 80
MODEL_SHA256 = '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747'
OCR_MODELS = {'eng': MODEL_SHA256,
              'chi_sim': 'a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730'}
SUPPORTED_VIEWS = {'mail_detail', 'download_file', 'docreader_content', 'contact_detail',
                   'event_editor', 'calendar_list', 'mail_inbox', 'popup', 'article_detail',
                   'alarm_card', 'conversation_list'}
# Calendar day-list nodes are bound inside the day holder; mailbox rows have no
# resource ids, so they are bound by exact subject/sender/snippet text.
CALENDAR_LIST_FIELDS = {'day_label': 'top_value', 'event_title': 'event_item_title',
                        'event_time': 'event_item_time',
                        'event_description': 'event_item_description'}
CALENDAR_DAY_HOLDER = 'org.fossify.calendar:id/day_holder'
CALENDAR_EVENT_HOLDER = 'org.fossify.calendar:id/event_item_holder'
MAIL_INBOX_FIELD = re.compile(r'^row_(\d+)_(sender|subject|snippet)$')
# The Inbox preview truncates the message body with an ellipsis, so only the
# visible head of the full snippet is a pixel claim; a short or diverging read
# fails closed. The full snippet text is still bound exactly by the UI dump node.
PREVIEW_MIN_CHARS = 12
CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


def normalize(value):
    return ' '.join(value.split())


def fullwidth_folded(value):
    """Fold fullwidth ASCII forms and the ideographic space only."""
    folded = []
    for character in value:
        code = ord(character)
        if 0xFF01 <= code <= 0xFF5E:
            folded.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            folded.append(' ')
        else:
            folded.append(character)
    return ''.join(folded)


def canonical_text(value):
    """Width-folded, whitespace-insensitive text for CJK OCR comparisons."""
    return ''.join(fullwidth_folded(value).split())


def contains_cjk(value):
    return any(any(start <= ord(character) <= end for start, end in CJK_RANGES) for character in value)


def field_language(field, expected, default):
    """Read asserted fields with the OCR model of their expected script.

    ``default`` is the packet/app view language; the auxiliary status-bar clock
    keeps that default because it never carries app language.
    """
    if field != '_status_time' and contains_cjk(expected):
        return 'chi_sim'
    return default


def status_time_equivalent(recognized, expected):
    """Android may drop the leading hour zero; the clock region still bounds the read."""
    match = re.search(r'(\d{1,2}):(\d{2})', recognized)
    if match is None or not re.fullmatch(r'\d{2}:\d{2}', expected):
        return False
    return int(match.group(1)) == int(expected[:2]) and match.group(2) == expected[3:]


def android_wall_clock(frame):
    """The timezone-aware device wall clock the status bar actually rendered.

    Packet ``observed_at`` is the flow time, so navigation that crosses a minute
    boundary would assert the wrong status-bar minute. Missing, non-string,
    naive, or non-ISO ``android_time`` values fail closed instead of falling
    back to the flow time or relaxing the pixel gate.
    """
    value = frame.get('android_time')
    if not isinstance(value, str):
        raise ValueError('frame android_time is missing or not a string')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('frame android_time is not an ISO-8601 timestamp') from None
    if parsed.utcoffset() is None:
        raise ValueError('frame android_time lacks a timezone')
    return parsed


def field_matches(view, field, language, recognized, expected):
    if field == '_status_time':
        return status_time_equivalent(recognized, expected)
    if view == 'mail_inbox' and field.endswith('_snippet'):
        return preview_prefix_matches(recognized, expected)
    if (view == 'contact_detail' and field == 'phone'
            and re.sub(r'\D', '', recognized) == re.sub(r'\D', '', expected)):
        return True
    if language == 'chi_sim':
        return canonical_text(recognized) == canonical_text(expected)
    return normalize(recognized) == normalize(expected)


def preview_prefix_matches(recognized, expected):
    """The Inbox preview shows only the head of the snippet plus an ellipsis.

    A full-text pixel read is impossible on the real APK, so the pixel rule
    claims exactly the visible head: the canonical recognized text (with its
    trailing ellipsis dropped) must be a substantial leading substring of the
    full expected snippet. The exact full text is still machine-bound to the UI
    dump node, so a wrong or short read fails closed instead of being faked.
    """
    visible = canonical_text(recognized).rstrip('.…')
    target = canonical_text(expected)
    return len(visible) >= PREVIEW_MIN_CHARS and target.startswith(visible)


def fallback_attempts(field, language):
    """Retry a failed region with calibrated OCR parameters, never a looser gate."""
    if field == '_status_time':
        # The trailing status icon enters the crop past x=140 on the pinned
        # 1080x2400 AVD, so the retry widens only enough for a two-digit hour.
        return ({'scale': 2, 'pageseg': 7},
                {'rectangle': {'left': 0, 'top': 0, 'width': 140, 'height': 132},
                 'scale': 2, 'pageseg': 7})
    if language == 'chi_sim':
        return ({'pageseg': 6}, {'pageseg': 4, 'scale': 2})
    return ()


def run_ocr(image, regions, language, pixel_checks=None):
    request = {'image': str(image), 'regions': regions, 'language': language}
    if pixel_checks:
        request['pixel_checks'] = pixel_checks
    result = subprocess.run(['node', str(ROOT / 'scripts/gui_ocr.cjs')], input=json.dumps(request),
                            text=True, capture_output=True, timeout=120, env=os.environ)
    if result.returncode:
        raise RuntimeError('OCR engine failed: ' + result.stderr[:200])
    ocr = json.loads(result.stdout)
    if (ocr['engine'] != 'tesseract.js@7.0.0' or ocr['language'] != language
            or ocr['model_sha256'] != OCR_MODELS[language]):
        raise ValueError('OCR model or engine version changed')
    return ocr


def rectangle(node):
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.attrib.get('bounds', ''))
    if not match:
        raise ValueError('visible node has no pixel bounds')
    x1, y1, x2, y2 = map(int, match.groups())
    if x2 <= x1 or y2 <= y1:
        raise ValueError('visible node has empty pixel bounds')
    return {'left': x1, 'top': y1, 'width': x2 - x1, 'height': y2 - y1}


def calendar_list_nodes(root, package, expected):
    """Bind the Calendar day header and the unique asserted event row.

    The month grid stays in the same dump, so only the day holder can satisfy
    the assertion; the target row has to carry all three asserted row texts at
    once. Never binds an editor or a month-grid cell.
    """
    holders = [node for node in root.iter('node')
               if node.attrib.get('resource-id') == CALENDAR_DAY_HOLDER]
    if len(holders) != 1:
        raise ValueError('Calendar day list has no unique day holder')
    header = [node for node in holders[0].iter('node')
              if node.attrib.get('resource-id') == f'{package}:id/top_value'
              and node.attrib.get('text') == expected['day_label']]
    rows = [node for node in holders[0].iter('node')
            if node.attrib.get('resource-id') == CALENDAR_EVENT_HOLDER]
    target = [row for row in rows
              if child_text(row, 'event_item_title') == expected['event_title']
              and child_text(row, 'event_item_time') == expected['event_time']
              and child_text(row, 'event_item_description') == expected['event_description']]
    if len(target) != 1:
        raise ValueError('Calendar asserted event row is not unique')
    row_nodes = list(target[0].iter('node'))
    return {field: (header if field == 'day_label' else
                    [node for node in row_nodes
                     if node.attrib.get('resource-id') == f'{package}:id/{resource}'])
            for field, resource in CALENDAR_LIST_FIELDS.items()}


def mail_inbox_prefixes(fields):
    """Validate the per-row Inbox assertion fields and return their prefixes."""
    names = {}
    for field in fields:
        match = MAIL_INBOX_FIELD.fullmatch(field)
        if match is None:
            raise ValueError(f'unsupported Mail inbox visible assertion {field!r}')
        names.setdefault(match.group(1), set()).add(match.group(2))
    if not names or any(parts != {'sender', 'subject', 'snippet'} for parts in names.values()):
        raise ValueError('Mail inbox assertions must declare sender, subject and snippet per row')
    return sorted(names)


def mail_inbox_nodes(root, package, expected):
    """Bind every asserted Inbox row to its exact sender/subject/snippet nodes."""
    parents = {child: parent for parent in root.iter('node') for child in parent}
    texts = [node for node in root.iter('node') if node.attrib.get('package') == package
             and node.attrib.get('class') == 'android.widget.TextView']
    rows = {}
    for prefix in mail_inbox_prefixes(expected):
        row = bind_inbox_row(parents, texts, expected[f'row_{prefix}_subject'],
                             expected[f'row_{prefix}_sender'], expected[f'row_{prefix}_snippet'])
        if row is None:
            raise ValueError(f'Mail inbox row {prefix} is not bound on screen')
        rows[f'row_{prefix}'] = row
    return rows


def frame_regions(capture_dir, frame, packet):
    view = packet['capture']['view_id']
    if view not in SUPPORTED_VIEWS:
        raise NotImplementedError('machine pixel rule does not support this view')
    ui_path = frame.get('ui_dump')
    if not isinstance(ui_path, str) or not re.fullmatch(r'ui/[0-9a-f]{32}\.xml', ui_path):
        raise ValueError('frame has no bound UI snapshot')
    raw = (capture_dir / ui_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != frame.get('ui_dump_sha256'):
        raise ValueError('UI snapshot hash mismatch')
    root = ElementTree.fromstring(raw)
    expected = packet['capture']['expected_visible']
    pixel_expected = {**expected}
    if view == 'download_file':
        pixel_expected['directory'] = 'Files in ' + expected['directory']
    regions = []
    pixel_checks = []
    card_nodes = None
    calendar_nodes, inbox_rows = None, None
    if view == 'calendar_list':
        if set(packet['capture']['visible_assertions']) != set(CALENDAR_LIST_FIELDS):
            raise ValueError('Calendar list frame must assert the day header and event row fields')
        calendar_nodes = calendar_list_nodes(root, frame['package'], expected)
    if view == 'mail_inbox':
        mail_inbox_prefixes(packet['capture']['visible_assertions'])
        inbox_rows = mail_inbox_nodes(root, frame['package'], expected)
    if view == 'alarm_card':
        if expected['enabled'] != 'false':
            raise NotImplementedError('enabled Clock switch has no calibrated pixel rule')
        cards = [node for node in root.iter('node') if node.attrib.get('package') == frame['package']
                 and node.attrib.get('resource-id') == 'com.google.android.deskclock:id/alarm_card_layout'
                 and any(child.attrib.get('resource-id') == 'com.google.android.deskclock:id/label'
                         and child.attrib.get('text') == expected['label'] for child in node.iter('node'))]
        if len(cards) != 1:
            raise ValueError('Clock target card is not unique')
        card_nodes = list(cards[0].iter('node'))
    for field in packet['capture']['visible_assertions']:
        value = pixel_expected[field]
        if view == 'calendar_list':
            nodes = calendar_nodes[field]
        elif view == 'mail_inbox':
            prefix, name = MAIL_INBOX_FIELD.fullmatch(field).groups()
            nodes = [inbox_rows[f'row_{prefix}'][name]]
        elif view == 'alarm_card':
            resource = {'label': 'label', 'time': 'digital_clock', 'enabled': 'onoff'}[field]
            nodes = [node for node in card_nodes if node.attrib.get('resource-id') ==
                     'com.google.android.deskclock:id/' + resource]
            if len(nodes) != 1 or (field == 'label' and nodes[0].attrib.get('text') != value) \
                    or (field == 'time' and ''.join(nodes[0].attrib.get('text', '').split()) != value) \
                    or (field == 'enabled' and nodes[0].attrib.get('checked') != value):
                raise ValueError(f'Clock {field} has no unique target-card region')
            if field == 'enabled':
                pixel_checks.append({'field': field, 'kind': 'switch_knob_left',
                                     'rectangle': rectangle(nodes[0])})
                continue
            if field == 'time':
                shown = nodes[0].attrib['text']
                parts = re.fullmatch(r'(\d{1,2}:\d{2})\s*(AM|PM)', shown)
                if not parts:
                    raise ValueError('Clock time has an unsupported pixel layout')
                bounds = rectangle(nodes[0])
                width, height = bounds['width'], bounds['height']
                split = int(width * .82)
                suffix = int(width * .81)
                suffix_top = int(height * .3)
                regions.extend([
                    {'field': '_time_digits', 'rectangle': {**bounds, 'width': split}},
                    {'field': '_time_meridiem', 'rectangle': {'left': bounds['left'] + suffix,
                        'top': bounds['top'] + suffix_top, 'width': width - suffix,
                        'height': height - suffix_top}, 'scale': 3}])
                pixel_expected['_time_digits'], pixel_expected['_time_meridiem'] = parts.groups()
                continue
            pixel_expected[field] = nodes[0].attrib['text']
        elif view == 'contact_detail' and field == 'phone':
            nodes = [node for node in root.iter('node') if node.attrib.get('package') == frame['package']
                     and re.sub(r'\D', '', node.attrib.get('text', '')) == value]
            if len(nodes) == 1:
                pixel_expected[field] = nodes[0].attrib['text']
        elif view == 'conversation_list' and field == 'sender':
            expected_digits = re.sub(r'\D', '', value)
            nodes = [node for node in root.iter('node') if node.attrib.get('package') == frame['package']
                     and node.attrib.get('resource-id') == frame['package'] + ':id/conversation_name'
                     and ((not expected_digits and node.attrib.get('text') == value)
                          or (expected_digits and (re.sub(r'\D', '', node.attrib.get('text', '')) == expected_digits
                              or (len(expected_digits) >= 10
                                  and re.sub(r'\D', '', node.attrib.get('text', '')) == expected_digits[-10:]))))]
            if len(nodes) == 1:
                pixel_expected[field] = nodes[0].attrib['text']
        elif view == 'conversation_list' and field == 'body':
            nodes = [node for node in root.iter('node') if node.attrib.get('package') == frame['package']
                     and node.attrib.get('resource-id') == frame['package'] + ':id/conversation_snippet'
                     and node.attrib.get('text') == value]
        else:
            nodes = [node for node in root.iter('node') if node.attrib.get('package') == frame['package']
                     and node.attrib.get('text') == value
                     and (view != 'contact_detail' or field != 'name'
                          or node.attrib.get('resource-id') == 'com.google.android.contacts:id/large_title')]
        if len(nodes) != 1:
            raise ValueError(f'{field} has no unique UI-backed pixel region')
        regions.append({'field': field, 'rectangle': rectangle(nodes[0]),
                        'scale': 3 if ((view == 'download_file' and field == 'name')
                                       or (view == 'contact_detail' and field == 'phone')) else 1})
    regions.append({'field': '_status_time', 'rectangle': {'left': 0, 'top': 0, 'width': 120, 'height': 132}})
    return regions, pixel_expected, pixel_checks


def resolution_size(value):
    match = re.search(r'Override size:\s*(\d+)x(\d+)', value or '')
    if match is None:
        match = re.search(r'Physical size:\s*(\d+)x(\d+)', value or '')
    if match is None:
        match = re.fullmatch(r'\s*(\d+)x(\d+)\s*', value or '')
    if match is None:
        raise ValueError('resolution field is not a parseable wm size')
    return int(match.group(1)), int(match.group(2))


def audit_device_env(capture_dir, receipt):
    frames = receipt.get('frames')
    results = []
    episode_env = None

    def rejected(reason):
        return {'schema_version': '1.0', 'status': 'reject', 'reason': reason,
                'fields': list(DEVICE_ENV_FIELDS), 'episode': episode_env, 'frames': results}

    if not isinstance(frames, list) or not frames:
        return rejected('episode has no frames to bind to environment fields')
    for frame in frames:
        result = {'packet_id': frame.get('packet_id'), 'frame_id': frame.get('frame_id'),
                  'png': frame.get('png'), 'sha256': frame.get('sha256'), 'status': 'reject'}
        results.append(result)
        environment = frame.get('device_env')
        if not isinstance(environment, dict):
            result['reason'] = 'frame has no per-frame device environment'
            return rejected(result['reason'])
        missing = sorted(field for field in DEVICE_ENV_FIELDS
                         if not isinstance(environment.get(field), str) or not environment[field].strip()
                         or environment[field].strip().lower() in ('null', 'unknown'))
        if missing:
            result['reason'] = 'device environment field missing or empty: ' + ', '.join(missing)
            return rejected(result['reason'])
        if episode_env is None:
            episode_env = {field: environment[field] for field in DEVICE_ENV_FIELDS}
        elif any(environment[field] != episode_env[field] for field in DEVICE_ENV_FIELDS):
            drift = ', '.join(field for field in DEVICE_ENV_FIELDS if environment[field] != episode_env[field])
            result['reason'] = 'device environment drifted within episode: ' + drift
            return rejected(result['reason'])
        try:
            raw = (capture_dir / frame['png']).read_bytes()
        except (KeyError, TypeError, OSError) as error:
            result['reason'] = f'frame PNG is unavailable: {error}'
            return rejected(result['reason'])
        if hashlib.sha256(raw).hexdigest() != frame.get('sha256'):
            result['reason'] = 'frame PNG hash does not match receipt'
            return rejected(result['reason'])
        try:
            width, height = png_size(raw)
            declared = resolution_size(environment['resolution'])
        except ValueError as error:
            result['reason'] = str(error)
            return rejected(result['reason'])
        if (width, height) != declared:
            result['reason'] = (f'resolution does not match PNG size: '
                                f'{declared[0]}x{declared[1]} != {width}x{height}')
            return rejected(result['reason'])
        result.update({'device_env': dict(episode_env), 'png_size': [width, height], 'status': 'pass'})
    snapshot = receipt.get('device')
    if isinstance(snapshot, dict):
        for field in DEVICE_ENV_SNAPSHOT_FIELDS:
            if field in snapshot and snapshot[field] != episode_env[field]:
                results[-1].update(status='reject',
                                   reason=f'episode device record differs from frames: {field}')
                return rejected(results[-1]['reason'])
    return {'schema_version': '1.0', 'status': 'pass', 'reason': None, 'fields': list(DEVICE_ENV_FIELDS),
            'episode': episode_env, 'frames': results}


def audit_capture(capture_dir):
    capture_dir = Path(capture_dir)
    receipt = json.loads((capture_dir / 'receipt.json').read_text())
    plan_bytes = (capture_dir / 'private-plan.json').read_bytes()
    plan = json.loads(plan_bytes)
    if (receipt.get('status') != 'capture_complete' or receipt.get('reset_verified') is not True
            or receipt.get('episode_id') != plan['episode_id']
            or receipt.get('plan_sha256') != hashlib.sha256(plan_bytes).hexdigest()
            or receipt.get('source_assets') != plan['source_assets']):
        raise ValueError('capture did not pass seed, EOF and reset checks')
    packets = {packet['packet_id']: packet for packet in plan['packets']}
    expected_frames = [(packet['packet_id'], item['frame_id']) for packet in plan['packets']
                       for item in packet['capture']['frames']]
    if [(frame['packet_id'], frame['frame_id']) for frame in receipt['frames']] != expected_frames:
        raise ValueError('capture frame binding is incomplete')
    device_env = audit_device_env(capture_dir, receipt)
    if device_env['status'] != 'pass':
        results = [{'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'], 'png': frame['png'],
                    'sha256': frame['sha256'], 'status': 'reject', 'fields': [],
                    'reason': device_env['reason']} for frame in receipt['frames']]
        return {'schema_version': '1.0', 'rule_version': RULE, 'ocr_model_sha256': MODEL_SHA256,
                'ocr_models': OCR_MODELS, 'plan_sha256': receipt['plan_sha256'],
                'capture_run_id': receipt['capture_run_id'], 'device_env': device_env,
                'status': 'reject', 'frames': results}
    results = []
    for frame in receipt['frames']:
        packet = packets[frame['packet_id']]
        item = {'packet_id': frame['packet_id'], 'frame_id': frame['frame_id'],
                'png': frame['png'], 'sha256': frame['sha256'], 'status': 'reject', 'fields': []}
        try:
            view = packet['capture']['view_id']
            if view not in SUPPORTED_VIEWS:
                item.update(status='unsupported', reason='no machine pixel rule for view')
                results.append(item)
                continue
            if view == 'alarm_card' and packet['capture']['expected_visible']['enabled'] != 'false':
                item.update(status='unsupported', reason='enabled Clock switch has no calibrated pixel rule')
                results.append(item)
                continue
            image = capture_dir / frame['png']
            raw = image.read_bytes()
            if hashlib.sha256(raw).hexdigest() != frame['sha256'] or png_size(raw) != (frame['width'], frame['height']):
                raise ValueError('original PNG integrity mismatch')
            expected_package = {'mail_detail': 'com.gmailclone', 'download_file': 'com.google.android.documentsui',
                                'docreader_content': 'at.tomtasche.reader',
                                'contact_detail': 'com.google.android.contacts',
                                'event_editor': 'org.fossify.calendar',
                                'calendar_list': 'org.fossify.calendar',
                                'mail_inbox': 'com.gmailclone',
                                'alarm_card': 'com.google.android.deskclock',
                                'conversation_list': 'com.google.android.apps.messaging'}.get(view)
            if view in ('popup', 'article_detail'):
                if packet['source'] not in ('dudu', 'lianqi', 'starrail', 'babaquna', 'wangyiyun', 'chaowei'):
                    raise ValueError('extension source app is unsupported')
                expected_package = 'com.ku.simulator.' + packet['source']
            if frame['package'] != expected_package or expected_package + '/' not in frame['activity']:
                raise ValueError('declared source is not foreground')
            android_time = android_wall_clock(frame)
            regions, pixel_expected, pixel_checks = frame_regions(capture_dir, frame, packet)
            expected = {**pixel_expected,
                        '_status_time': android_time.strftime('%H:%M')}
            default_language = 'chi_sim' if view in ('popup', 'article_detail') else 'eng'
            languages = [field_language(region['field'], expected[region['field']], default_language)
                         for region in regions]
            groups = {}
            for index, language in enumerate(languages):
                groups.setdefault(language, []).append(index)
            recognized = {}
            models = {}
            checks = []
            for position, (language, indices) in enumerate(groups.items()):
                ocr = run_ocr(image.resolve(), [regions[index] for index in indices], language,
                              pixel_checks if position == 0 else None)
                models[language] = ocr['model_sha256']
                if position == 0:
                    checks = ocr['checks']
                for index, region in zip(indices, ocr['regions']):
                    recognized[index] = region
            mixed_languages = len(groups) > 1
            if mixed_languages:
                item['language'] = '+'.join(sorted(groups))
                item['ocr_model_sha256'] = {language: models[language] for language in sorted(models)}
            else:
                item['language'] = next(iter(groups))
                item['ocr_model_sha256'] = models[item['language']]
            for index, region in enumerate(regions):
                field = region['field']
                language = languages[index]
                attempt = recognized[index]
                if not (attempt['confidence'] >= MIN_CONFIDENCE
                        and field_matches(view, field, language, attempt['text'], expected[field])):
                    for fallback in fallback_attempts(field, language):
                        candidate = run_ocr(image.resolve(), [{**region, **fallback}], language)['regions'][0]
                        attempt = candidate
                        if (candidate['confidence'] >= MIN_CONFIDENCE
                                and field_matches(view, field, language, candidate['text'], expected[field])):
                            break
                passed = (attempt['confidence'] >= MIN_CONFIDENCE
                          and field_matches(view, field, language, attempt['text'], expected[field]))
                output_field = 'time' if field in ('_time_digits', '_time_meridiem') else field
                preview = view == 'mail_inbox' and field.endswith('_snippet')
                entry = {**attempt, 'field': output_field,
                         **({'part': field[6:]} if output_field == 'time' else {}),
                         **({'language': language} if mixed_languages else {}),
                         **({'match': 'visible_preview_prefix'} if preview else {}),
                         'expected': expected[field],
                         'minimum_confidence': MIN_CONFIDENCE, 'passed': passed}
                item['fields'].append(entry)
            for check in checks:
                if check['field'] != 'enabled' or check['kind'] != 'switch_knob_left':
                    raise ValueError('unexpected pixel check')
                item['fields'].append({**check, 'expected': 'false', 'minimum_difference': 50,
                                       'passed': check['difference'] >= 50})
            if (len(item['fields']) != len(regions) + len(pixel_checks)
                    or not all(field['passed'] for field in item['fields'])):
                raise ValueError('pixel OCR assertion failed')
            item.update(status='pass', reason=None)
        except (ValueError, RuntimeError, OSError, ElementTree.ParseError, subprocess.TimeoutExpired) as error:
            item['reason'] = str(error)
        results.append(item)
    return {'schema_version': '1.0', 'rule_version': RULE, 'ocr_model_sha256': MODEL_SHA256,
            'ocr_models': OCR_MODELS,
            'plan_sha256': receipt['plan_sha256'], 'capture_run_id': receipt['capture_run_id'],
            'device_env': device_env,
            'status': 'pass' if all(item['status'] == 'pass' for item in results) else 'reject',
            'frames': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('capture_dir', type=Path)
    args = parser.parse_args()
    report = audit_capture(args.capture_dir)
    (args.capture_dir / 'qa-machine.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'frames': len(report['frames'])}))
    raise SystemExit(report['status'] != 'pass')
