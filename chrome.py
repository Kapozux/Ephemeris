"""最小的 Chrome 驱动：spawn + CDP，不装 playwright/selenium。

跟墨页 `server/md2pdf.mjs` 同一个路子（自己起浏览器、自己说 CDP），但有两处不同：

1. **用真 Chrome，不用 chrome-headless-shell。** 墨页拿它打印 PDF 没问题，
   但那是旧版无头模式，Cloudflare 一测一个准。claude.ai 在 CF 后面，必须真 Chrome。
2. **独立 profile**（`.chrome/`），不碰你日常那个。Chrome 不允许两个实例开同一个
   profile，共用的话你开着浏览器时定时任务就起不来。

登录态就存在这个 profile 里：头一次 headful 登进去，之后 headless 一直复用。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request

import websocket  # websocket-client，venv 里已经有

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, '.chrome')

CHROME_CANDIDATES = [
    os.environ.get('EPHEMERIS_CHROME') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
]
TASKPOLICY = '/usr/sbin/taskpolicy'


def find_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise RuntimeError('找不到 Chrome。装一个，或者用 EPHEMERIS_CHROME 指路径。')


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Chrome:
    """起一个 Chrome，用 CDP 跟它说话。用 with 保证收尾。"""

    def __init__(self, headless=True, profile=PROFILE, timeout=45):
        self.headless, self.profile, self.timeout = headless, profile, timeout
        self.proc = self.ws = None
        self.port = _free_port()
        self._id = 0

    # ---------------------------------------------------------- 起/停

    def kill_stale(self):
        """Chrome 一个 profile 只允许一个实例。上次跑崩留下的进程会让这次静默退出
        （表现是 /json/list 一直 connection refused），所以先清干净。"""
        subprocess.run(['pkill', '-f', f'--user-data-dir={self.profile}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(0.6)

    def __enter__(self):
        os.makedirs(self.profile, exist_ok=True)
        self.kill_stale()
        args = [
            find_chrome(),
            f'--remote-debugging-port={self.port}',
            # Chrome 111+ 按 Origin 拦 DevTools 的 WebSocket，websocket-client 会带 Origin 头
            '--remote-allow-origins=*',
            f'--user-data-dir={self.profile}',
            '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking',
            '--disable-features=Translate,MediaRouter',
            '--window-size=1280,900',
            'about:blank',
        ]
        if self.headless:
            # 必须是 =new。老的 --headless 就是 headless-shell，CF 直接拦。
            args.insert(1, '--headless=new')
        # launchd 的后台进程会把 CPU 限流传给子进程（墨页实测慢 4 倍），用 taskpolicy 摘出来
        if os.path.exists(TASKPOLICY):
            args = [TASKPOLICY, '-a'] + args
        # stderr 留着：起不来的时候这是唯一的线索（mac 上有一堆 CVDisplayLink 噪音，读的时候滤掉）
        self.log = os.path.join(self.profile, 'chrome.stderr.log')
        with open(self.log, 'w') as f:
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=f)
        self.ws = websocket.create_connection(self._page_ws(), timeout=self.timeout,
                                              max_size=256 * 1024 * 1024)
        return self

    def __exit__(self, *_):
        try:
            if self.ws:
                self.ws.close()
        except Exception:                                    # noqa: BLE001
            pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _page_ws(self):
        """等 /json/list 里出现 page 目标，拿它的 WebSocket 地址。"""
        deadline = time.time() + self.timeout
        last = ''
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f'http://127.0.0.1:{self.port}/json/list', timeout=2) as r:
                    for t in json.loads(r.read()):
                        if t.get('type') == 'page' and t.get('webSocketDebuggerUrl'):
                            return t['webSocketDebuggerUrl']
            except Exception as e:                           # noqa: BLE001
                last = str(e)
            if self.proc.poll() is not None:              # 已经退了，别再等满 45 秒
                break
            time.sleep(0.25)
        raise RuntimeError(f'Chrome 没起来（{self.timeout}s）：{last}\n' + self.stderr_tail())

    def stderr_tail(self, n=6):
        try:
            lines = [l for l in open(self.log, errors='replace').read().splitlines()
                     if l.strip() and not re.search(
                         r'CVDisplayLink|cv_display_link|XNNPACK|gcm/engine|allocator multiple', l)]
            return '\n'.join(lines[-n:])
        except Exception:                                    # noqa: BLE001
            return ''

    # ---------------------------------------------------------- CDP

    def call(self, method, **params):
        """发一条 CDP 命令，等对应 id 的回复（事件一律丢掉）。"""
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get('id') != mid:
                continue                                     # 是事件，不是回我的
            if 'error' in msg:
                raise RuntimeError(f"CDP {method} 失败：{msg['error']}")
            return msg.get('result', {})
        raise TimeoutError(f'CDP {method} 超时')

    def goto(self, url, wait=2.5):
        self.call('Page.enable')
        self.call('Page.navigate', url=url)
        time.sleep(wait)                                     # CF 挑战要点时间跑 JS

    def evaluate(self, expr, timeout=None):
        """在页面里跑 JS。支持 await（顶层 async）。返回值要能 JSON 化。"""
        old, self.timeout = self.timeout, timeout or self.timeout
        try:
            r = self.call('Runtime.evaluate', expression=expr,
                          awaitPromise=True, returnByValue=True, timeout=self.timeout * 1000)
        finally:
            self.timeout = old
        if r.get('exceptionDetails'):
            d = r['exceptionDetails']
            raise RuntimeError('页面里报错：' +
                               (d.get('exception', {}).get('description') or d.get('text', '')))
        return r.get('result', {}).get('value')

    def recover(self):
        """页面被导航/关掉之后执行上下文就作废了（CDP 报 "Inspected target navigated
        or closed"）。重新连一个 page 目标即可，浏览器和 profile 都还在。"""
        try:
            self.ws.close()
        except Exception:                                    # noqa: BLE001
            pass
        self.ws = websocket.create_connection(self._page_ws(), timeout=self.timeout,
                                              max_size=256 * 1024 * 1024)

    def set_cookie(self, name, value, domain='.claude.ai', path='/'):
        self.call('Network.enable')
        self.call('Network.setCookie', name=name, value=value, domain=domain, path=path,
                  secure=True, httpOnly=False)


def reset_profile():
    """登录态坏了就把 profile 删掉重来。"""
    shutil.rmtree(PROFILE, ignore_errors=True)
