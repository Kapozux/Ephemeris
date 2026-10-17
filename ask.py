"""在终端里用「问」（2026-10-13：网页打不开时应急用）。调本机 5055 的 /api/qa/ask，记录跟网页那边是同一份。

python ask.py                    一直问，空行 / Ctrl-D 退出。/new 开一段新对话；/list 以前的对话；/open 编号 打开那段接着问
python ask.py /new               先开一段新对话，再接着一直问（/list、/open 编号 也能这样直接跟在后面）
python ask.py 问题……              问 Claude：它自己去搜原文、翻卡、读前后文再答（一两分钟，用 Claude 订阅额度）
问过的都记着（跟网页同一份），也会写进当天的日记。
设了隐私锁（lock.py）的话，服务器回 423 时这里问一次密码，解锁令牌只在这个进程里；开着的时候每 20 秒报到一次，
不然打一个长问题的工夫（超过 lock.IDLE）就又锁了。网页那边一解锁 / 一上锁，这边的令牌作废，下次请求再问密码。
"""
import getpass
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:5055'
TOKEN = [None]                                           # 隐私锁的解锁令牌（cookie eph_unlock）


def _send(path, body=None, timeout=60):
    """→ (状态码, JSON)。body=None 是 GET。"""
    head = {'Content-Type': 'application/json'}
    if TOKEN[0]:
        head['Cookie'] = f'eph_unlock={TOKEN[0]}'
    req = urllib.request.Request(BASE + path, headers=head, method='GET' if body is None else 'POST',
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for c in r.headers.get_all('Set-Cookie') or []:
                m = re.match(r'eph_unlock=([^;]+)', c)
                if m:
                    TOKEN[0] = m.group(1)
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:                 # 409 / 423 之类：后端把原因放在 JSON 里
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {'error': f'HTTP {e.code}'}


def _unlock():
    while True:
        try:
            pw = getpass.getpass('\nEphemeris 锁着，输密码（直接回车退出）：')
        except EOFError:
            pw = ''
        if not pw:
            sys.exit()
        code, r = _send('/api/lock/unlock', {'password': pw})
        if r.get('ok'):
            return
        print(r.get('error') or '没解开')


def _call(path, body=None, timeout=60):
    while True:
        code, r = _send(path, body, timeout)
        if code != 423:
            return r
        TOKEN[0] = None
        _unlock()


def _post(path, body):
    return _call(path, body, timeout=300)


def _get(path):
    return _call(path)


def _keepalive():
    while True:
        time.sleep(20)
        if TOKEN[0]:
            _send('/api/lock/ping', {})


CITE = re.compile(r'\[\[[^\]]*\]\]|\[c\d+[^\[\]\n]{0,80}\]')
VIZ = re.compile(r'```html[^\n]*\n.*?(?:```|$)', re.S)        # 回答里的图（同 qa.VIZ_RE）：终端画不了
NO_VIZ = '（这里有张图，去网页上看）'


def _draft(text, nums):
    """写到一半的回答：出处记号按出现先后编成 [1] [2]（跟写完后的编号一样），正写到一半的记号先不打。"""
    text = re.sub(r'\[\[?[^\]\n]*$', '', VIZ.sub(NO_VIZ, text or ''))
    return CITE.sub(lambda m: f'[{nums.setdefault(m.group(0), len(nums) + 1)}]', text)


def sources(cites):
    if cites:
        print('\n出处：')
        for i, c in enumerate(cites, 1):
            who = '你说' if c['who'] == 'me' else 'AI 说'
            print(f"  [{i}] {c['date']} · {who} · {c.get('title') or ''}\n      「{c['quote']}」")
    print()


THREAD = [None]                                          # 正在用的那段对话（None = 服务器上最近那段）


def ask(q):
    try:
        r = _post('/api/qa/ask', {'question': q, 'thread': THREAD[0]})
        if r.get('error') or not r.get('started'):
            print(f"\n没开始：{r.get('error') or '上一个问题还没答完'}\n")
            return
        steps = printed = 0
        shown, nums = '', {}
        while True:                                      # 边写边打：查找进度一行一行，回答一个字一个字
            time.sleep(0.4)
            d = _get('/api/qa/live')
            for s in d['steps'][steps:]:                 # 一步：{k, t, d}（qa._step）；它中途说的话（say）上面已经流出来了
                if isinstance(s, dict) and s.get('k') != 'say':
                    print(f"  · {s['t']}" + (f"：{s['d']}" if s.get('d') else ''), flush=True)
            steps = len(d['steps'])
            text = _draft(d['partial'], nums)
            if text.startswith(shown):
                print(('\n' if not printed and text else '') + text[len(shown):], end='', flush=True)
            else:                                        # 问 Claude 查一步说一句：新的一段从头开始
                nums.clear()
                text = _draft(d['partial'], nums)
                print('\n\n' + text, end='', flush=True)
            printed += len(text) - len(shown) if text.startswith(shown) else len(text)
            shown = text
            if not d['running']:
                break
        if d.get('error') or not d.get('result'):
            print(f"\n没答上：{d.get('error') or '没有结果'}\n")
            return
        final = d['result']
        if not shown.strip():                            # 走的不是 Claude（一次性给的）、或者一个字都没赶上：整段打
            n = {c['id']: i for i, c in enumerate(final['cites'], 1)}
            print('\n' + re.sub(r'\[\[([^\]]+)\]\]', lambda m: f'[{n[m.group(1)]}]' if m.group(1) in n else '', VIZ.sub(NO_VIZ, final['answer'])))
        else:
            print()
        sources(final['cites'])
    except OSError as e:
        print(f'连不上 Ephemeris（{e}）：应用开着吗？\n')


def threads():
    d = _get('/api/qa' + (f'?thread={THREAD[0]}' if THREAD[0] else ''))
    THREAD[0] = d['thread']
    for t in d['threads'][:20]:
        mark = ' ◀' if t['id'] == d['thread'] else ''
        print(f"  {t['id']:>4}  {t['updated'][5:16].replace('T', ' ')}  {t['n']:>3} 句  {t['title'] or '新对话'}{mark}")
    print()


def show_thread():
    d = _get(f'/api/qa?thread={THREAD[0]}')
    for t in d['history'][-6:]:
        n = {c['id']: i for i, c in enumerate(t['cites'], 1)}
        text = re.sub(r'\[\[([^\]]+)\]\]', lambda m: f'[{n[m.group(1)]}]' if m.group(1) in n else '', VIZ.sub(NO_VIZ, t['text']))
        print(('> ' if t['role'] == 'user' else '') + text + '\n')


HELP = '/new 新对话，/list 以前的对话，/open 编号 打开那段接着问'


def command(q):
    """/ 开头的是命令，不当问题发出去（10-17：「python ask.py /new」被当成问题问了 Claude 两遍）。"""
    word, _, rest = q.partition(' ')
    if word in ('/new', '/clear'):
        THREAD[0] = _post('/api/qa/new', {}).get('thread') or THREAD[0]
        print('开了一段新对话（以前的用 /list 看）。\n')
    elif word == '/list':
        threads()
    elif word == '/open' and rest.strip().isdigit():
        THREAD[0] = int(rest.strip())
        show_thread()
    else:
        print(f'不认识这个命令。能用的：{HELP}\n')


if __name__ == '__main__':
    threading.Thread(target=_keepalive, daemon=True).start()
    q = ' '.join(sys.argv[1:]).strip()
    if q and not q.startswith('/'):                      # python ask.py 问题……：问完就走
        ask(q)
        sys.exit()
    print(f'问吧（空行退出；{HELP}）。[n] 是出处。\n')
    if q:                                                # python ask.py /new：先做这条命令，再接着问
        command(q)
    while True:
        try:
            q = input('> ').strip()
        except EOFError:
            break
        if not q:
            break
        if q.startswith('/'):
            command(q)
            continue
        print('Claude 在查…', end='', flush=True)
        ask(q)
