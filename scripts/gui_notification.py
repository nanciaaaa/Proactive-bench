#!/usr/bin/env python3
"""Capture a real Android heads-up notification; never substitutes an app popup."""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from xml.etree import ElementTree


SOURCES = {'com.ku.simulator.dudu': '.NotificationReceiver'}


def adb(serial, *args, binary=False):
    result = subprocess.run(['adb', '-s', serial, *args], capture_output=True,
                            text=not binary, timeout=20)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:300])
    return result.stdout


def dump_ui(serial):
    adb(serial, 'shell', 'uiautomator', 'dump', '/sdcard/gui-notification.xml')
    raw = adb(serial, 'shell', 'cat', '/sdcard/gui-notification.xml').encode()
    return ElementTree.fromstring(raw), raw


def java_hash(value):
    result = 0
    for char in value:
        result = (31 * result + ord(char)) & 0xffffffff
    return result - 0x100000000 if result & 0x80000000 else result


def active_notification(serial, package, notification_id):
    rows = adb(serial, 'shell', 'cmd', 'notification', 'list').splitlines()
    token = f'|{package}|{java_hash(notification_id)}|'
    return any(token in row for row in rows)


def capture(serial, notification_id, channel, title, body, output, timeout=8):
    package = 'com.ku.simulator.dudu'
    receiver = f'{package}/{SOURCES[package]}'
    adb(serial, 'shell', 'pm', 'grant', package, 'android.permission.POST_NOTIFICATIONS')
    adb(serial, 'shell', 'cmd', 'notification', 'set_dnd', 'off')
    adb(serial, 'shell', 'input', 'keyevent', '3')
    adb(serial, 'shell', 'am', 'broadcast', '-n', receiver, '-a', f'{package}.PUBLISH_NOTIFICATION',
        '--es', 'notification_id', notification_id, '--es', 'channel', channel,
        '--es', 'title', title, '--es', 'body', body)
    notification_dump = adb(serial, 'shell', 'dumpsys', 'notification')
    if f'pkg={package}' not in notification_dump or f"mId='{channel}'" not in notification_dump:
        raise RuntimeError('notification source package or channel was not recorded')
    if not active_notification(serial, package, notification_id):
        raise RuntimeError('published notification is not active')
    deadline = time.monotonic() + timeout
    last_window = ''
    while time.monotonic() < deadline:
        root, ui = dump_ui(serial)
        texts = {node.attrib.get('text', '') for node in root.iter('node')
                 if node.attrib.get('package') == 'com.android.systemui'}
        last_window = adb(serial, 'shell', 'dumpsys', 'window', 'windows')
        systemui = adb(serial, 'shell', 'dumpsys', 'activity', 'service',
                       'com.android.systemui/.SystemUIService')
        heads_up = ('HeadsUp' in last_window or 'heads_up' in last_window.lower()
                    or 'headsUpIsVisible: true' in systemui)
        if title in texts and body in texts and heads_up:
            raw = adb(serial, 'exec-out', 'screencap', '-p', binary=True)
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)
            return {'status': 'pass', 'package': package, 'notification_id': notification_id,
                    'title': title, 'body': body, 'ui_sha256': hashlib.sha256(ui).hexdigest(),
                    'png': str(output), 'png_sha256': hashlib.sha256(raw).hexdigest()}
        time.sleep(.25)
    raise RuntimeError('real System UI heads-up banner was not observed; no screenshot published')


def cancel(serial, notification_id):
    package = 'com.ku.simulator.dudu'
    adb(serial, 'shell', 'am', 'broadcast', '-n', f'{package}/{SOURCES[package]}',
        '-a', f'{package}.CANCEL_NOTIFICATION', '--es', 'notification_id', notification_id)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not active_notification(serial, package, notification_id):
            return
        time.sleep(.1)
    raise RuntimeError('notification cleanup left an active notification')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--notification-id', required=True)
    parser.add_argument('--channel', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--body', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = capture(args.serial, args.notification_id, args.channel, args.title, args.body, args.output)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ElementTree.ParseError) as error:
        result = {'status': 'reject', 'reason': str(error)}
    finally:
        try:
            cancel(args.serial, args.notification_id)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            result.setdefault('cleanup_error', 'notification cleanup failed')
    args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result['status'] != 'pass')
