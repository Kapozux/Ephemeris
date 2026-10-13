"""在终端里用「问」（2026-10-13 用户：网页被自律软件挡了，应急用）。调本机 5055 的 /api/qa/ask，记录跟网页那边是同一份。

python ask.py                    一直问，空行 / Ctrl-D 退出；/clear 清掉对话；/claude 切换「问 Claude」（深挖）开关
python ask.py 问题……              只问这一句（快：只看证据卡，十来秒）
python ask.py --claude 问题……     问 Claude：它自己去搜原文、翻卡、读前后文再答（一两分钟，用 Claude 订阅额度）
"""
import json
import re
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:5055'


def _post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:                 # 409 之类：后端把原因放在 JSON 里
        return json.load(e)


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def show(r):
    if r.get('error'):
        print(f'没答上：{r["error"]}\n')
        return
    n = {c['id']: i for i, c in enumerate(r['cites'], 1)}
    print('\n' + re.sub(r'\[\[([^\]]+)\]\]', lambda m: f'[{n[m.group(1)]}]' if m.group(1) in n else '', r['answer']))
    if r['cites']:
        print('\n出处：')
        for i, c in enumerate(r['cites'], 1):
            who = '你说' if c['who'] == 'me' else 'AI 说'
            print(f"  [{i}] {c['date']} · {who} · {c.get('title') or ''}\n      「{c['quote']}」")
    print()


def ask(q, deep=False):
    try:
        if not deep:
            show(_post('/api/qa/ask', {'question': q}))
            return
        r = _post('/api/qa/ask', {'question': q, 'deep': True})
        if r.get('error') or not r.get('started'):
            print(f"\n没开始：{r.get('error') or '上一个问题还在查'}\n")
            return
        shown = 0
        while True:                                      # 一边查一边把它在干什么打出来
            time.sleep(2)
            d = _get('/api/qa')['deep']
            for line in d['steps'][shown:]:
                print('  ' + line, flush=True)
            shown = len(d['steps'])
            if not d['running']:
                break
        show({'error': d['error']} if d.get('error') else d['result'])
    except OSError as e:
        print(f'连不上 Ephemeris（{e}）：应用开着吗？\n')


if __name__ == '__main__':
    args = sys.argv[1:]
    deep = '--claude' in args
    args = [a for a in args if a != '--claude']
    if args:
        ask(' '.join(args), deep)
        sys.exit()
    print('问吧（空行退出，/clear 清掉对话，/claude 切换深挖）。[n] 是出处。\n')
    while True:
        try:
            q = input('(Claude) > ' if deep else '> ').strip()
        except EOFError:
            break
        if not q:
            break
        if q == '/clear':
            _post('/api/qa/clear', {})
            print('清掉了。\n')
            continue
        if q == '/claude':
            deep = not deep
            print('问 Claude：开（它自己查，一两分钟）\n' if deep else '问 Claude：关（只看证据卡，十来秒）\n')
            continue
        print('Claude 在查…' if deep else '在翻卡…', flush=True)
        ask(q, deep)
