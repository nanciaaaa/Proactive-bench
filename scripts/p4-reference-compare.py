#!/usr/bin/env python3
"""P4 参考 APK T1/T3 对照：采集两侧主界面文本层级并保存对照数据。

我方侧：CDP 读六款扩展 App WebView DOM 文本（document.body.innerText）。
参考侧：uiautomator 文本层级（点掉首启隐私/权限弹窗后 dump）。
T2 视觉近似 / T4 逐页对照依赖视觉能力，按 _archive/contract-upgrade-20260926/read.md 版权与合规约定标记待复核。
"""
import json, re, subprocess, socket, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INST = ROOT / 'runtime/instances/p4-loop'
DUMP = INST / 'reference-dumps'
DUMP.mkdir(parents=True, exist_ok=True)
ADB = ['adb', '-s', '127.0.0.1:5556']

OUR = {
    'dudu': 'com.ku.simulator.dudu',
    'lianqi': 'com.ku.simulator.lianqi',
    'starrail': 'com.ku.simulator.starrail',
    'babaquna': 'com.ku.simulator.babaquna',
    'wangyiyun': 'com.ku.simulator.wangyiyun',
    'chaowei': 'com.ku.simulator.chaowei',
}
REF = {
    'dudu': ('滴滴', 'com.sdu.didi.psnger'),
    'lianqi': ('曹操出行', 'cn.caocaokeji.user'),
    'starrail': ('铁路12306', 'com.MobileTicket'),
    'babaquna': ('携程', 'ctrip.android.view'),
    'wangyiyun': ('网易云', 'com.netease.cloudmusic'),
    'chaowei': ('知乎', 'com.zhihu.android'),
}
AGREE_BTNS = ['同意', '同意并继续', '允许', '始终允许', '我知道了', '确定', '知道了',
              '跳过', '暂不', '仅在使用中允许', '仅使用期间允许', '下次再说']


def sh(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def launch(pkg):
    sh(ADB + ['shell', 'am', 'force-stop', pkg], timeout=30)
    sh(ADB + ['shell', 'monkey', '-p', pkg, '-c', 'android.intent.category.LAUNCHER', '1'], timeout=60)
    time.sleep(8)


def dump_uiauto():
    sh(ADB + ['shell', 'uiautomator', 'dump', '/sdcard/ref.xml'], timeout=120)
    p = sh(ADB + ['shell', 'cat', '/sdcard/ref.xml'], timeout=30)
    return p.stdout.decode('utf-8', errors='replace')


def texts(xml):
    return [t for t in re.findall(r'text="([^"]*)"', xml) if t.strip()]


def tap_text(target, xml=None):
    xml = xml or dump_uiauto()
    m = re.search(r'text="%s"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(target), xml)
    if not m:
        return False
    x = (int(m.group(1)) + int(m.group(3))) // 2
    y = (int(m.group(2)) + int(m.group(4))) // 2
    sh(ADB + ['shell', 'input', 'tap', str(x), str(y)], timeout=30)
    return True


def reach_main_ref(pkg, tries=6):
    seen = set()
    for _ in range(tries):
        xml = dump_uiauto()
        t = texts(xml)
        sig = tuple(t[:12])
        if sig in seen:
            time.sleep(4)
            continue
        seen.add(sig)
        hit = False
        for b in AGREE_BTNS:
            if b in t:
                if tap_text(b, xml):
                    hit = True
                    time.sleep(4)
                    break
        if not hit:
            break
    return texts(dump_uiauto())


def cdp_eval(pid, expr):
    """通过 WebView DevTools 协议读 DOM。"""
    import websocket  # websocket-client，suppress_origin=True 必须
    fwd = sh(ADB + ['forward', 'tcp:0', 'localabstract:webview_devtools_remote_%s' % pid], timeout=30)
    port = int(fwd.stdout.decode().strip())
    s = socket.socket()
    s.settimeout(10)
    for _ in range(20):
        try:
            s.connect(('127.0.0.1', port))
            break
        except OSError:
            time.sleep(1)
    s.close()
    # 直接 HTTP GET /json/list（无需 websocket 握手找 page，先取 ws url）
    import http.client
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    c.request('GET', '/json/list')
    r = c.getresponse()
    pages = json.loads(r.read().decode())
    c.close()
    target = None
    for p in pages:
        if p.get('type') == 'page':
            target = p
            break
    if not target:
        raise RuntimeError('no page target: %s' % [p.get('type') for p in pages])
    ws = websocket.create_connection(target['webSocketDebuggerUrl'], suppress_origin=True, timeout=30)
    ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                        'params': {'expression': expr, 'returnByValue': True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get('id') == 1:
            break
    ws.close()
    return msg['result']['result'].get('value', {})


def our_dom(pkg):
    launch(pkg)
    p = sh(ADB + ['shell', 'pidof', pkg], timeout=30)
    pids = p.stdout.decode().split()
    if not pids:
        return ''
    for pid in pids:
        try:
            return cdp_eval(pid, 'document.body.innerText')
        except Exception:
            continue
    return ''


def main():
    data = {'our': {}, 'ref': {}}
    for app in OUR:
        print('== our %s' % app, flush=True)
        data['our'][app] = our_dom(OUR[app])
    for app, (label, pkg) in REF.items():
        print('== ref %s (%s)' % (app, label), flush=True)
        launch(pkg)
        t = reach_main_ref(pkg)
        (DUMP / f'{app}-{label}.txt').write_text('\n'.join(t) + '\n')
        data['ref'][app] = {'label': label, 'pkg': pkg, 'texts': t}
    out = DUMP / 'compare-data.json'
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print('saved', out)


if __name__ == '__main__':
    main()
