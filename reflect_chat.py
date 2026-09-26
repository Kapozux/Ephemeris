"""「回顾」：一段时间里我在跟 AI 聊什么。

和 getAudio/reflect.py 一个路子：
  compute()   纯数据，确定性，从 chats.db + diaries 表算，模型一步不掺和
  narrative   只让模型写一小段直白叙事（flash-lite），结果落 SQLite，指纹没变就不重算
  build()     先把旧的给出去，指纹变了在后台重算，前端轮询
"""
import hashlib
import json
import os
import sys
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'getAudio'))

import index as _index
RANGES = {'1w': 7, '1m': 30, '3m': 90, 'all': None}
_lock = _index.LOCK       # 全库一把锁
_regen = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reflect (
  range_key TEXT PRIMARY KEY, fingerprint TEXT, headline TEXT, narrative TEXT,
  topics_json TEXT, generated_at TEXT
);"""


def _ensure(conn):
    with _lock:
        conn.executescript(SCHEMA)


# ------------------------------------------------------------- 纯数据

def _rows(conn, sql, *a):
    with _lock:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]


def compute(conn, range_key='1m', now=None):
    _ensure(conn)
    now = now or datetime.now()
    days_n = RANGES.get(range_key, 30)
    last = _rows(conn, "SELECT MAX(date) d FROM messages WHERE role='human'")[0]['d']
    end = datetime.fromisoformat(last) if last else now
    if days_n:
        start = end - timedelta(days=days_n - 1)
        prev_start = start - timedelta(days=days_n)
    else:
        first = _rows(conn, "SELECT MIN(date) d FROM messages WHERE role='human'")[0]['d']
        start = datetime.fromisoformat(first) if first else end
        prev_start = start
    s, e, ps = start.date().isoformat(), end.date().isoformat(), prev_start.date().isoformat()

    day_rows = _rows(conn, """
        SELECT date, COUNT(*) msgs, SUM(chars) chars, COUNT(DISTINCT conv_id) threads,
               SUM(CASE WHEN source='gemini' THEN chars ELSE 0 END) g_chars
          FROM messages WHERE role='human' AND date BETWEEN ? AND ? GROUP BY date""", s, e)
    prev_rows = _rows(conn, """
        SELECT date, COUNT(*) msgs, SUM(chars) chars, COUNT(DISTINCT conv_id) threads
          FROM messages WHERE role='human' AND date >= ? AND date < ? GROUP BY date""", ps, s)
    by = {r['date']: r for r in day_rows}
    pby = {r['date']: r for r in prev_rows}

    def series(a, b, src):
        out, d = [], datetime.fromisoformat(a)
        while d.date().isoformat() <= b:
            k = d.date().isoformat(); r = src.get(k)
            out.append({'date': k, 'msgs': r['msgs'] if r else 0, 'chars': r['chars'] if r else 0,
                        'threads': r['threads'] if r else 0})
            d += timedelta(days=1)
        return out

    ser = series(s, e, by)
    pser = series(ps, (start - timedelta(days=1)).date().isoformat(), pby) if days_n else []
    pser = pser[-len(ser):]

    hr = defaultdict(int); wd = defaultdict(int)
    for r in _rows(conn, """SELECT substr(hhmm,1,2) h, date, COUNT(*) n FROM messages
                             WHERE role='human' AND date BETWEEN ? AND ? GROUP BY h, date""", s, e):
        hr[int(r['h'])] += r['n']
        wd[datetime.fromisoformat(r['date']).weekday()] += r['n']

    diaries = _rows(conn, "SELECT * FROM diaries WHERE date BETWEEN ? AND ?", s, e)
    tc, moods, n_art, n_open = Counter(), Counter(), 0, 0
    for d in diaries:
        try:
            for t in json.loads(d['topics'] or '[]'): tc[t.strip()] += 1
            n_art += len(json.loads(d['artifacts'] or '[]'))
            n_open += len(json.loads(d['open_loops'] or '[]'))
        except ValueError:
            pass
        if d.get('mood'): moods[d['mood']] += 1
    raw_tags = tc.most_common()          # 全部原始标签及次数；归组后再算占比

    long_convs = _rows(conn, """
        SELECT c.id, c.title, c.source, COUNT(DISTINCT m.date) days, COUNT(*) msgs,
               MIN(m.date) a, MAX(m.date) b
          FROM messages m JOIN conversations c ON c.id=m.conv_id
         WHERE m.role='human' AND m.date BETWEEN ? AND ?
         GROUP BY c.id HAVING days >= 2 ORDER BY days DESC, msgs DESC LIMIT 6""", s, e)

    tot = lambda k, rows: sum(r[k] or 0 for r in rows)
    active = [r for r in ser if r['msgs']]
    multi = sum(1 for r in day_rows if r['threads'] > 1)
    return {
        'range': range_key, 'period': {'start': s, 'end': e},
        'totals': {
            'days': len(active), 'msgs': tot('msgs', day_rows), 'chars': tot('chars', day_rows),
            'threads': sum(r['threads'] for r in day_rows), 'diaries': len(diaries),
            'prev_days': len(prev_rows), 'prev_msgs': tot('msgs', prev_rows), 'prev_chars': tot('chars', prev_rows),
            'gemini_chars': tot('g_chars', day_rows),
            'multi_window_days': multi, 'artifacts': n_art, 'open_loops': n_open,
        },
        'peak_hour': max(hr, key=hr.get) if hr else None,
        'most_active_weekday': max(wd, key=wd.get) if wd else None,
        'hour_counts': [hr.get(i, 0) for i in range(24)],
        'weekday_counts': [wd.get(i, 0) for i in range(7)],
        'series': ser, 'prev_series': pser,
        'raw_tags': raw_tags, 'moods': moods.most_common(),
        'long_conversations': long_convs,
        'busiest': sorted(active, key=lambda r: -r['chars'])[:5],
        '_diaries': diaries,
    }


# ------------------------------------------------------------- 叙事

PROMPT = """下面是 __NAME__ 在 {start} 到 {end} 之间每天的日记标题（他和 AI 聊天记录按天压成的），以及按出现次数算的话题占比。
有的日期后面跟着一个心情词，那是他那天**自己**打卡或手写日记里的感受（不是 AI 推的），没有就是没记。
请写一份简短的回顾。

风格：直接、具体、说人话。像朋友看完你这段时间的日记后直接告诉你"你这段时间主要在干什么"。
不要比喻，不要抒情，不要"仿佛""沉潜""画卷"这类修辞，不要评价好坏，不要给建议，不要罗列数字。
可以直接点名科目、项目、具体的事。

输出三部分：
1. headline：一句话概括这段时间在干什么，直接陈述，不要冒号、感叹号、书名号，不超过 20 个字。
2. narrative：一段话，80-150 字。做了什么，也说感觉怎样：第一句说最主要在弄什么；然后第二、第三大块；
   有心情词的话，用一句话说这段时间整体过得怎样、哪几天明显好或糟、跟在做的事有没有对得上（只按心情词说，不要猜）；
   有明显变化（比如后半段转向了别的）说一句。
3. groups：把下面的话题标签归成 5-8 组。每组给：
   name（不超过 10 个字，比标签更具体，例如「IB 各科复习和作业」）、desc（不超过 30 字，说这组实际在干什么）、
   keywords（2-5 个短词，凡是标签里含这个词就算这组，例如 ["IB","复习","作业","IA"]）、
   tags（这组最典型的原始标签，最多 15 个，必须原样抄）。
   标签只列典型的即可，长尾会按 keywords 自动归入；不好归的不要硬塞。

话题标签（标签 次数）：
{topics}

日记标题（共 {n} 天）：
{items}

严格按以下 JSON 输出，不要输出其他任何内容：
{{"headline": "...", "narrative": "...", "groups": [{{"name": "...", "desc": "...", "keywords": ["..."], "tags": ["原标签", "..."]}}]}}"""
PROMPT = PROMPT.replace('__NAME__', _index.who())


def _feel_words(d):
    """这段时间每天的心情词：打卡优先（最后一条），其次手写日记读出来的。"""
    s, e = d['period']['start'], d['period']['end']
    with _index.LOCK:
        db = _index.connect()
        hand = {r[0]: r[1] for r in db.execute(
            "SELECT date, label FROM mood_scores WHERE src='hand' AND date BETWEEN ? AND ?", (s, e))}
        try:
            ck = {r[0]: r[1] for r in db.execute(
                "SELECT date, word FROM checkins WHERE date BETWEEN ? AND ? ORDER BY created_at", (s, e))}
        except Exception:                                    # noqa: BLE001  还没建表
            ck = {}
    return {**{k: v for k, v in hand.items() if v}, **ck}


def _fingerprint(d):
    """回顾是基于日记写的，所以指纹必须跟着日记**内容**变。

    只看篇数不行：改了 prompt 把 291 篇全重写一遍，篇数没变，回顾还停在旧版上。
    这里把当期日记的 generated_at 也算进去。
    """
    t = d['totals']
    stamps = ''.join(sorted((x.get('generated_at') or '') for x in d.get('_diaries', [])))
    h = hashlib.sha1(stamps.encode()).hexdigest()[:10] if stamps else '0'
    # 心情词也算进去：新打了卡，回顾要跟着变
    f = hashlib.sha1(json.dumps(sorted(_feel_words(d).items()), ensure_ascii=False).encode()).hexdigest()[:8]
    return f"{d['period']['start']}|{d['period']['end']}|{t['days']}|{t['msgs']}|{t['diaries']}|{h}|{f}"


def _generate(d):
    from reflect import _call_model
    from diary import _extract_json
    feel = _feel_words(d)
    items = '\n'.join(f"{x['date']} | {x['headline']}" + (f" | {feel[x['date']]}" if x['date'] in feel else '')
                      for x in sorted(d['_diaries'], key=lambda x: x['date']))
    topics = '\n'.join(f"{k} {v}" for k, v in d['raw_tags'][:200])   # 按次数降序，长尾靠 keywords
    prompt = PROMPT.format(start=d['period']['start'], end=d['period']['end'],
                           topics=topics or '（还没有日记）', items=items or '（无）', n=len(d['_diaries']))
    for _ in range(2):                       # 长区间输出长，偶尔 JSON 断掉，重试一次
        out = _extract_json(_call_model(prompt))
        if out and out.get('headline'):
            return out
    return None


def _bg(conn, range_key, d):
    try:
        out = _generate(d)
        if not out:
            print('reflect', range_key, 'no usable output, keeping previous')
            return
        with _lock:
            conn.execute('INSERT OR REPLACE INTO reflect VALUES (?,?,?,?,?,?)',
                         (range_key, _fingerprint(d), out.get('headline', ''), out.get('narrative', ''),
                          json.dumps(out.get('groups') or [], ensure_ascii=False),
                          datetime.now().isoformat(timespec='seconds')))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        print('reflect', range_key, 'failed:', e)
    finally:
        _regen.discard(range_key)


def build(conn, range_key='1m', refresh=False):
    """旧的先给；指纹变了或强制刷新就后台重算。"""
    d = compute(conn, range_key)
    fp = _fingerprint(d)
    with _lock:
        row = conn.execute('SELECT * FROM reflect WHERE range_key=?', (range_key,)).fetchone()
    row = dict(row) if row else None
    stale = refresh or not row or row['fingerprint'] != fp
    if stale and range_key not in _regen and d['totals']['diaries']:
        _regen.add(range_key)
        # 传浅拷贝：下面马上会 d.pop('_diaries') 给接口瘦身，线程慢一步就拿不到日记了（踩过）
        threading.Thread(target=_bg, args=(conn, range_key, dict(d)), daemon=True).start()

    groups = []
    if row:
        try:
            groups = json.loads(row['topics_json'] or '[]')
        except ValueError:
            groups = []
    counts = dict(d['raw_tags'])
    total = sum(counts.values()) or 1
    assign = {}                                   # 原始标签 -> 组序号
    for gi, g in enumerate(groups):
        for t in g.get('tags', []):
            assign.setdefault(t, gi)
    for t in counts:                              # 长尾：标签里含某组 keyword 就归那组
        if t in assign:
            continue
        for gi, g in enumerate(groups):
            if any(k and k in t for k in g.get('keywords', [])):
                assign[t] = gi
                break
    sums = [0] * len(groups)
    for t, gi in assign.items():
        sums[gi] += counts.get(t, 0)
    topics = [{'tag': g.get('name', ''), 'name': g.get('name', ''), 'desc': g.get('desc', ''),
               'count': n, 'percent': round(n / total * 100)}
              for g, n in zip(groups, sums) if n]
    topics.sort(key=lambda x: -x['count'])
    rest = total - sum(t['count'] for t in topics)
    if rest > 0:
        topics.append({'tag': '__other__', 'name': '其它', 'desc': '' if groups else '还没归组，先按原始标签数',
                       'count': rest, 'percent': round(rest / total * 100)})
    d['topics'] = topics
    d['n_tags'] = len(counts)
    d.pop('_diaries', None)
    d.update({
        'headline': row['headline'] if row else '',
        'narrative': row['narrative'] if row else '',
        'generated_at': row['generated_at'] if row else None,
        'generated': bool(row and row['headline']),
        'regenerating': range_key in _regen,
    })
    return d


def warm(conn):
    """启动时把四个区间都算一遍（旧的在就不会重算）。"""
    for k in RANGES:
        try:
            build(conn, k)
        except Exception as e:  # noqa: BLE001
            print('reflect warm', k, e)
