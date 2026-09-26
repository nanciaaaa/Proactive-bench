#!/usr/bin/env python3
"""P4 补救：参考 APK 证据诚实重采集（修正污染）。

对每个目标：force-stop 全部已知污染源 → 冷启动 → 等待 → 尽力 dump + logcat 崩溃证据。
无论成功与否都如实记录（crashed 标志 + 屏幕实际文本 + logcat 证据行），
绝不把屏幕残留文本归给目标 App。
"""
import json, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / 'runtime/instances/p4-loop/reference-dumps'
ADB = ['adb', '-s', '127.0.0.1:5556']

TARGETS = [
    {'key': 'wangyiyun', 'label': '网易云', 'pkg': 'com.netease.cloudmusic'},
    {'key': 'chaowei', 'label': '知乎', 'pkg': 'com.zhihu.android'},
]
ALL_PKGS = ['com.netease.cloudmusic', 'com.zhihu.android', 'ctrip.android.view',
            'com.sdu.didi.psnger', 'cn.caocaokeji.user', 'com.MobileTicket',
            'com.ku.simulator.dudu', 'com.ku.simulator.lianqi',
            'com.ku.simulator.starrail', 'com.ku.simulator.babaquna',
            'com.ku.simulator.wangyiyun', 'com.ku.simulator.chaowei']


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def dump_texts():
    """尽力 dump；失败返回 None。"""
    for attempt in range(3):
        r = sh(ADB + ['shell', 'uiautomator', 'dump'], timeout=90)
        if r.returncode == 0:
            c = sh(ADB + ['shell', 'cat', '/sdcard/window_dump.xml'], timeout=30)
            x = c.stdout.decode('utf-8', 'replace')
            m = re.search(r'<hierarchy.*?</hierarchy>', x, re.S)
            if m:
                texts = [t for t in re.findall(r'text="([^"]+)"', m.group(0)) if t]
                return texts
        time.sleep(4)
    return None


def logcat_evidence(pkg):
    r = sh(ADB + ['shell', 'logcat', '-d', '-t', '300',
                  'AndroidRuntime:E', 'ActivityManager:E', 'libc:F', '*:S'], timeout=90)
    lines = r.stdout.decode('utf-8', 'replace').splitlines()
    hits = [l for l in lines if pkg in l
            or 'has died' in l or 'Force removing' in l or 'FATAL' in l
            or 'keeps stopping' in l]
    return hits[-10:]


ref = json.loads((DUMP / 'reference-texts.json').read_text())

for t in TARGETS:
    key, label, pkg = t['key'], t['label'], t['pkg']
    # 杀干净，含我方 App，杜绝屏幕残留
    for p in ALL_PKGS:
        sh(ADB + ['shell', 'am', 'force-stop', p], timeout=30)
    sh(ADB + ['shell', 'logcat', '-c'], timeout=30)
    sh(ADB + ['shell', 'input', 'keyevent', '224'], timeout=30)  # 唤醒
    sh(ADB + ['shell', 'wm', 'dismiss-keyguard'], timeout=30)
    time.sleep(1)
    sh(ADB + ['shell', 'monkey', '-p', pkg, '-c', 'android.intent.category.LAUNCHER', '1'],
       timeout=60)
    time.sleep(15)
    texts = dump_texts()
    crashes = logcat_evidence(pkg)
    crashed = bool(crashes)
    # 若 dump 出的是启动器文本（app 秒退），视为崩溃且屏幕为启动器残留
    if texts:
        launcherish = any(t in texts for t in ('Mastodon', 'OpenDocument Reader', '淘店'))
        if launcherish and crashed:
            texts = ['[启动器残留文本——目标 App 已崩溃退出]'] + texts[:6]
    if texts is None:
        texts = ['[uiautomator dump 失败]']
    entry = {
        'label': label,
        'texts': texts,
        'crashed_on_avd': crashed,
        'logcat_evidence': crashes,
        'recaptured': True,
    }
    ref[key] = entry
    (DUMP / f'{key}-{label}.txt').write_text('\n'.join(texts) + '\n')
    print(f'== {key} crashed={crashed} n_texts={len(texts)} head={texts[:4]}')

(DUMP / 'reference-texts.json').write_text(
    json.dumps(ref, ensure_ascii=False, indent=1) + '\n')
print('reference-texts.json rewritten')
