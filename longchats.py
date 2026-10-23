"""长聊清单（2026-10-23）。「设置 → 长聊」那一块。

    lines(today=None)      → 一条一条的长聊线（主窗口 + 接着它开的续集），按聊过的天数排；每条带要不要迁的标记
    mark(conv_id, move)    → 标「要迁」True /「不迁」False / 取消 None
    follow(conv_id, after) → 这个窗口是接着 after 开的续集（after=None 拆开）

要迁的不是聊天原文（这里都备份着），是这些窗口里攒下的：它知道你哪些事、事情推进到哪了、你给它定的规矩和口气。
长不长看「聊过多少天」，不看消息数：一天聊两千条的窗口不一定是常用的。

续集（MATH_2 接着 MATH 2027 SUMMER）：标题末尾带 _2 /（3）这种编号、前面那截跟一个更早的长聊标题开头对得上的，
猜它是续集，只给「像是接着「X」」的提示，他点了才算（猜错的代价是两条线被并错，不自己并）。
"""
import re
from datetime import date, timedelta

import days
import store

MIN_DAYS = 5          # 聊过 5 天以上才算长聊
ACTIVE_DAYS = 30      # 最近 30 天还聊过 = 还在用（要迁的多半在这里）
SOURCES = ('claude', 'chatgpt', 'gemini', 'lobehub')    # 换的是 AI 聊天账号；写代码的（cc / cursor / codex）和「问」不算

_NUMBERED = re.compile(r'^(.+?)[\s_\-]*[(（]?([2-9])[)）]?(?:\s*[:：].*)?$')


def _rows():
    marks = {r['conv_id']: r for r in store.q('SELECT conv_id, move, after FROM long_chats')}
    rows = store.q(f"""SELECT id, source, title, n_days, n_msgs, first_ts, last_ts FROM conversations
                       WHERE source IN ({','.join('?' * len(SOURCES))}) AND (n_days >= ? OR id IN (SELECT conv_id FROM long_chats))""",
                   *SOURCES, MIN_DAYS)
    for r in rows:
        m = marks.get(r['id']) or {}
        r['move'] = None if m.get('move') is None else bool(m['move'])
        r['after'] = m.get('after')
    return rows


def _guess(r, rows):
    """标题像续集（末尾带编号）：找一个更早开始、标题开头对得上的长聊，取结束得离它开始最近的那个。"""
    m = _NUMBERED.match((r['title'] or '').strip())
    if not m:
        return None
    base = m.group(1).strip().lower()
    if len(base) < 2:
        return None
    got = [x for x in rows if x['id'] != r['id'] and (x['first_ts'] or '') < (r['first_ts'] or '')
           and (x['title'] or '').strip().lower().startswith(base)]
    return max(got, key=lambda x: x['last_ts'] or '')['id'] if got else None


def _root(cid, after):
    seen = set()
    while after.get(cid) and cid not in seen:
        seen.add(cid)
        cid = after[cid]
    return cid


def lines(today=None):
    rows = _rows()
    by = {r['id']: r for r in rows}
    after = {r['id']: r['after'] for r in rows if r['after'] in by}
    groups = {}
    for r in rows:
        groups.setdefault(_root(r['id'], after), []).append(r)
    since = (date.fromisoformat(today or days.today()) - timedelta(days=ACTIVE_DAYS)).isoformat()
    out = []
    for root, members in groups.items():
        members.sort(key=lambda x: x['first_ts'] or '')
        ids = [x['id'] for x in members]
        n = store.one(f"SELECT count(DISTINCT date) n FROM messages WHERE conv_id IN ({','.join('?' * len(ids))})", *ids)['n']
        head = by[root]
        last = max((x['last_ts'] or '') for x in members)
        if n < MIN_DAYS and head['move'] is None:
            continue
        guess = None if head['after'] else _guess(head, rows)
        out.append({'id': root, 'title': head['title'], 'source': head['source'], 'move': head['move'],
                    'days': n, 'msgs': sum(x['n_msgs'] or 0 for x in members),
                    'first': (members[0]['first_ts'] or '')[:10], 'last': last[:10], 'active': last[:10] >= since,
                    'parts': [{'id': x['id'], 'title': x['title'], 'days': x['n_days']} for x in members if x['id'] != root],
                    'guess': guess and {'id': _root(guess, after), 'title': by[_root(guess, after)]['title']}})
    out.sort(key=lambda x: (-x['days'], x['last']))
    return out


def _upsert(conv_id, **kw):
    if not store.one('SELECT 1 FROM conversations WHERE id=?', conv_id):
        raise ValueError('没有这个窗口')
    store.run('INSERT INTO long_chats (conv_id) VALUES (?) ON CONFLICT(conv_id) DO NOTHING', conv_id)
    for k, v in kw.items():
        store.run(f'UPDATE long_chats SET {k}=? WHERE conv_id=?', v, conv_id)


def mark(conv_id, move):
    _upsert(conv_id, move=None if move is None else int(bool(move)))


def follow(conv_id, after):
    if after == conv_id:
        raise ValueError('不能接着自己')
    if after and not store.one('SELECT 1 FROM conversations WHERE id=?', after):
        raise ValueError('没有这个窗口')
    _upsert(conv_id, after=after or None)
