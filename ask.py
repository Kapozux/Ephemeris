"""在终端里用「问」（2026-10-13 用户：网页被自律软件挡了，应急用）。调本机 5055 的 /api/qa/ask，记录跟网页那边是同一份。

python ask.py              一直问，空行 / Ctrl-D 退出；输入 /clear 清掉对话
python ask.py 问题……        只问这一句
"""
import json
import re
import sys
import urllib.request

BASE = 'http://127.0.0.1:5055'


def _post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r:
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


def ask(q):
    try:
        show(_post('/api/qa/ask', {'question': q}))
    except OSError as e:
        print(f'连不上 Ephemeris（{e}）：应用开着吗？\n')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        ask(' '.join(sys.argv[1:]))
        sys.exit()
    print('问吧（空行退出，/clear 清掉对话）。只凭证据卡回答，[n] 是出处。\n')
    while True:
        try:
            q = input('> ').strip()
        except EOFError:
            break
        if not q:
            break
        if q == '/clear':
            _post('/api/qa/clear', {})
            print('清掉了。\n')
            continue
        print('在翻卡…', end='', flush=True)
        ask(q)
