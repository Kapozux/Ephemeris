"""情绪：从两条线分别打分，再做实验看怎么合。

原来的 `diaries.mood` 是写日记时顺手给的一个词，实测 324 篇里没有一天是「开心」「兴奋」：
一半「平稳」，其余全是焦虑 / 烦躁。原因是材料 —— 找 AI 聊的多半是遇到了问题，
而 prompt 又写了「看不出就写平稳」。

这里不改那个字段，也不重写任何日记，另起一张表按来源各打一次：
  ai    只看 AI 日记（从聊天推出来的）
  hand  只看我自己在 Notion 手写的
  both  两份都给，告诉模型哪份是哪份
分数是 -2..+2 的效价，外加一个词（含正向的词）。怎么合成最终那一个数，由实验定（见 lab_*）。
"""
import json
import random
import re
import sys
from datetime import datetime

import index as idx
import journal

LABELS = ['开心', '满足', '兴奋', '平稳', '疲惫', '焦虑', '烦躁', '低落', '崩溃']
MAX_HAND = 5000            # 手写太长就截，情绪一般前面就看得出

PROMPT = """判断这个人这一天整体的情绪状态。

{material}

打分规则：
- valence：-2 到 +2 的整数或 .5。-2 很糟（崩溃、极度低落），-1 偏差，0 平平，+1 偏好，+2 很好（开心、兴奋、有成就感）。
- 看整天，不是某一句。好坏都有就按占主导的那一面，真的一半一半才给 0。
- **不要默认 0**。看得出一点倾向就给出来。
- 只根据材料里本人的话判断，不要脑补。
- label 从这些里选一个：{labels}
- evidence：最能支撑判断的一处，用材料里的原话或近似原话，不超过 30 字。

只输出 JSON，不要 markdown：
{{"valence": 0, "label": "平稳", "evidence": "..."}}
"""

AI_NOTE = ('【AI 日记】这是从他当天和 AI 的聊天记录里整理出来的日记。注意：他找 AI 多半是为了解决问题，'
           '所以这份材料天然偏向问题和烦恼，开心的事可能根本没聊。')
HAND_NOTE = '【手写日记】这是他当天自己在笔记里写的日记。'


def ensure_table():
    with idx.LOCK:
        db = idx.connect()
        db.execute("""CREATE TABLE IF NOT EXISTS mood_scores (
            date TEXT NOT NULL, src TEXT NOT NULL,          -- ai | hand | both
            valence REAL, label TEXT, evidence TEXT, scored_at TEXT,
            PRIMARY KEY (date, src))""")
        db.execute("""CREATE TABLE IF NOT EXISTS mood_ratings (
            date TEXT PRIMARY KEY, valence REAL, note TEXT, rated_at TEXT)""")
        db.commit()


def _q(sql, *args):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, args).fetchall()]


def ai_text(date):
    r = _q('SELECT headline, narrative, open_loops, artifacts FROM diaries WHERE date=?', date)
    if not r:
        return ''
    r = r[0]
    out = [r['headline'] or '', r['narrative'] or '']
    for k, lab in (('artifacts', '做出来的'), ('open_loops', '没做完的')):
        try:
            arr = json.loads(r[k] or '[]')
        except ValueError:
            arr = []
        if arr:
            out.append(lab + '：' + '；'.join(arr))
    return '\n'.join(x for x in out if x)


def hand_text(date):
    return '\n\n'.join(j['text'] for j in journal.for_date(date) if j['text'])[:MAX_HAND]


def material(date, src):
    a, h = ai_text(date), hand_text(date)
    if src == 'ai':
        return f'{AI_NOTE}\n\n{a}' if a else ''
    if src == 'hand':
        return f'{HAND_NOTE}\n\n{h}' if h.strip() else ''
    if a and h.strip():
        return f'{AI_NOTE}\n\n{a}\n\n{HAND_NOTE}\n\n{h}'
    return ''


def _parse(raw):
    m = re.search(r'\{.*\}', raw or '', re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        v = max(-2.0, min(2.0, float(d.get('valence'))))
    except (ValueError, TypeError):
        return None
    lab = d.get('label') if d.get('label') in LABELS else ''
    return {'valence': v, 'label': lab, 'evidence': (d.get('evidence') or '')[:80]}


def score(date, src, call_model):
    mat = material(date, src)
    if not mat:
        return None
    r = _parse(call_model(PROMPT.format(material=mat, labels='/'.join(LABELS))))
    if r:
        with idx.LOCK:
            db = idx.connect()
            db.execute('INSERT OR REPLACE INTO mood_scores VALUES (?,?,?,?,?,?)',
                       (date, src, r['valence'], r['label'], r['evidence'],
                        datetime.now().isoformat(timespec='seconds')))
            db.commit()
    return r


def plan():
    """要打哪些分：有 AI 日记的天打 ai，有手写的天打 hand，两者都有的再打 both。"""
    ensure_table()
    ai_days = {r['date'] for r in _q('SELECT date FROM diaries')}
    hand_days = {d for d, c in journal.dates().items() if c >= 30}
    done = {(r['date'], r['src']) for r in _q('SELECT date, src FROM mood_scores')}
    jobs = [(d, 'ai') for d in ai_days] + [(d, 'hand') for d in hand_days] + \
           [(d, 'both') for d in ai_days & hand_days]
    return sorted(j for j in jobs if j not in done), ai_days & hand_days


def run(call_model, say=print):
    jobs, both = plan()
    say(f'要打 {len(jobs)} 个分（两者都有的天 {len(both)}）')
    fails = []
    for i, (d, src) in enumerate(jobs, 1):
        r = score(d, src, call_model) or score(d, src, call_model)
        if not r:
            fails.append((d, src))
        if i % 50 == 0 or i == len(jobs):
            say(f'  {i}/{len(jobs)}  失败 {len(fails)}')
    return fails


def pick_for_rating(n=16, seed=7):
    """挑给我评的天：两者都有的天里，一半挑 AI 和手写分歧最大的（最能分出高下），一半随机。"""
    rows = _q("""SELECT a.date, a.valence ai, h.valence hand FROM mood_scores a
                   JOIN mood_scores h ON h.date=a.date AND h.src='hand'
                   JOIN mood_scores b ON b.date=a.date AND b.src='both'
                  WHERE a.src='ai'""")
    rows.sort(key=lambda r: -abs(r['ai'] - r['hand']))
    top = rows[:n // 2]
    rest = [r for r in rows[n // 2:]]
    random.Random(seed).shuffle(rest)
    picked = sorted(r['date'] for r in top + rest[:n - len(top)])
    return picked


# ------------------------------------------------------------------ 两个指标：过得怎么样 / 卡得多狠
# 实验结论（2026-09-27，40 天）：聊天里记下的是「跟问题较劲」的那部分 —— 骂 AI、做题卡住；
# 做出来之后的开心、跟家人出去，发生在聊天之外，只在手写里。所以不合成一个分，分开记：
#   过得怎么样 = 手写日记的 valence（src='hand'，-2..+2）；没手写的天空着，不拿聊天硬猜
#   卡得多狠   = 聊天原文的挫败程度（src='friction'，0..4，存在 valence 列里）

FRICTION_LABELS = ['顺', '有点卡', '卡', '很卡', '崩']

FRICTION_PROMPT = """下面是一个人某一天发给 AI 的全部消息（只有他说的话；贴进去的长材料只留了头尾）。
判断这一天他在和 AI 打交道时**卡得多狠**：被问题卡住、反复不对、烦躁、骂人、自我怀疑的程度。

- 只看他自己的话流露出来的，不看问题本身难不难。
- 0 顺：基本顺利，没什么情绪；1 有点卡：偶尔不顺或小抱怨；2 卡：明显受挫、烦躁；
  3 很卡：反复卡住、骂人、怀疑自己；4 崩：情绪崩溃、想放弃。
- evidence：最能说明的一句原话，不超过 30 字；0 分可以写空字符串。

只输出 JSON，不要 markdown：
{{"friction": 0, "evidence": "..."}}

{material}
"""


def score_friction(date, call_model):
    import bundle
    from slice import render_day
    day = bundle.day_bundle(date)
    if not day['threads']:
        return None
    raw = call_model(FRICTION_PROMPT.format(material=render_day(day)))
    m = re.search(r'\{.*\}', raw or '', re.S)
    try:
        d = json.loads(m.group(0))
        f = max(0, min(4, int(round(float(d.get('friction'))))))
    except (AttributeError, ValueError, TypeError):
        return None
    ev = (d.get('evidence') or '')[:80]
    with idx.LOCK:
        db = idx.connect()
        db.execute('INSERT OR REPLACE INTO mood_scores VALUES (?,?,?,?,?,?)',
                   (date, 'friction', f, FRICTION_LABELS[f], ev, datetime.now().isoformat(timespec='seconds')))
        db.commit()
    return {'friction': f, 'label': FRICTION_LABELS[f], 'evidence': ev}


def pending(dates_chat=None, dates_hand=None):
    """还没打的：有聊天没 friction 的天、有手写没 hand 的天。"""
    ensure_table()
    have = {(r['date'], r['src']) for r in _q("SELECT date, src FROM mood_scores WHERE src IN ('friction','hand')")}
    chat = dates_chat if dates_chat is not None else \
        [r['date'] for r in _q("SELECT DISTINCT date FROM messages WHERE role='human'")]
    hand = dates_hand if dates_hand is not None else [d for d, c in journal.dates().items() if c >= 30]
    return ([d for d in chat if (d, 'friction') not in have],
            [d for d in hand if (d, 'hand') not in have])


def fill(call_model, say=print):
    """补齐两个指标。每天 08:30 调度里跑一次；全量第一次也用它。"""
    chat, hand = pending()
    say(f'卡得多狠 要打 {len(chat)} 天，过得怎么样 要打 {len(hand)} 天')
    for i, d in enumerate(sorted(chat), 1):
        score_friction(d, call_model) or score_friction(d, call_model)
        if i % 25 == 0:
            say(f'  friction {i}/{len(chat)}')
    for d in sorted(hand):
        score(d, 'hand', call_model) or score(d, 'hand', call_model)
    return len(chat), len(hand)


def day_signals(date):
    """过得怎么样：有打卡用打卡（自己说的），没有用手写日记的分（模型读的）。卡得多狠：聊天。"""
    import checkin
    rows = {r['src']: r for r in _q("SELECT src, valence, label, evidence FROM mood_scores "
                                     "WHERE date=? AND src IN ('hand','friction')", date)}
    ck = checkin.for_date(date)
    if ck:
        v = round(sum(x['valence'] for x in ck) / len(ck), 2)
        feel = {'src': 'checkin', 'valence': v, 'label': ck[-1]['word'],
                'evidence': '；'.join(x['note'] for x in ck if x['note'])}
    else:
        feel = rows.get('hand')
    return {'mood': feel, 'friction': rows.get('friction'), 'checkins': ck}


def all_signals():
    import checkin
    out = {}
    for r in _q("SELECT date, src, valence FROM mood_scores WHERE src IN ('hand','friction')"):
        out.setdefault(r['date'], {})['mood' if r['src'] == 'hand' else 'friction'] = r['valence']
    for d, v in checkin.day_values().items():            # 打卡覆盖手写的分
        out.setdefault(d, {})['mood'] = v
        out[d]['checkin'] = True
    return out


# ------------------------------------------------------------------ 实验：哪种合法最接近我自己的判断

WEIGHTS = [0, 0.25, 0.5, 0.75, 1]          # 手写占的比重；0 = 只看 AI 日记，1 = 只看手写


def lab_days(n=16):
    """要我评的那几天。第一次要的时候挑好存下来，之后不变 —— 评到一半列表不能换。"""
    ensure_table()
    with idx.LOCK:
        db = idx.connect()
        db.execute('CREATE TABLE IF NOT EXISTS mood_lab_pick (date TEXT PRIMARY KEY)')
        db.commit()
    have = [r['date'] for r in _q('SELECT date FROM mood_lab_pick ORDER BY date')]
    if have:
        return have
    # 两份都有的天必须三种分都打完才挑 —— 否则只会从先打完的那部分里挑
    jobs, both = plan()
    # 允许零星几个打不出分的（模型对个别内容直接不回，重试也没用），多了说明还在跑
    if sum(1 for d, _ in jobs if d in both) > 3:
        return []
    picked = pick_for_rating(n)
    if len(picked) < n:
        return []
    with idx.LOCK:
        db = idx.connect()
        db.executemany('INSERT INTO mood_lab_pick VALUES (?)', [(d,) for d in picked])
        db.commit()
    return picked


def rate(date, valence, note=''):
    with idx.LOCK:
        db = idx.connect()
        db.execute('INSERT OR REPLACE INTO mood_ratings VALUES (?,?,?,?)',
                   (date, float(valence), note, datetime.now().isoformat(timespec='seconds')))
        db.commit()


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 2)


def lab_result():
    """每种方法的分跟我评的分差多少（平均绝对误差，越小越好）和相关系数（越接近 1 越好）。"""
    ensure_table()
    sc = {}
    for r in _q('SELECT date, src, valence FROM mood_scores'):
        sc.setdefault(r['date'], {})[r['src']] = r['valence']
    rated = {r['date']: r['valence'] for r in _q('SELECT date, valence FROM mood_ratings')}
    days = [d for d in rated if {'ai', 'hand', 'both'} <= set(sc.get(d, {}))]
    methods = [('只看 AI 日记', lambda s: s['ai']), ('只看手写', lambda s: s['hand']),
               ('两份一起给模型', lambda s: s['both'])]
    methods += [(f'加权：手写 {int(w * 100)}%', (lambda w: lambda s: w * s['hand'] + (1 - w) * s['ai'])(w))
                for w in WEIGHTS[1:-1]]
    out = []
    truth = [rated[d] for d in days]
    for name, f in methods:
        pred = [f(sc[d]) for d in days]
        mae = round(sum(abs(p - t) for p, t in zip(pred, truth)) / len(days), 2) if days else None
        out.append({'method': name, 'mae': mae, 'corr': _corr(pred, truth)})
    per_day = [{'date': d, 'me': rated[d], **{k: sc[d][k] for k in ('ai', 'hand', 'both')}} for d in sorted(days)]
    return {'n': len(days), 'methods': out, 'days': per_day}


def overview():
    """不需要我评就能看的：两条线整体差多少。"""
    ensure_table()
    by = {}
    for r in _q('SELECT src, valence, label FROM mood_scores'):
        by.setdefault(r['src'], []).append(r)
    dist = {src: {'n': len(v), 'mean': round(sum(x['valence'] for x in v) / len(v), 2),
                  'pos': round(sum(x['valence'] > 0 for x in v) / len(v), 2),
                  'neg': round(sum(x['valence'] < 0 for x in v) / len(v), 2)}
            for src, v in by.items()}
    pairs = _q("""SELECT a.valence ai, h.valence hand FROM mood_scores a
                    JOIN mood_scores h ON h.date=a.date AND h.src='hand' WHERE a.src='ai'""")
    return {'dist': dist, 'pairs': len(pairs),
            'corr_ai_hand': _corr([p['ai'] for p in pairs], [p['hand'] for p in pairs]),
            'hand_higher': round(sum(p['hand'] > p['ai'] for p in pairs) / len(pairs), 2) if pairs else None}


if __name__ == '__main__':
    import diary
    if sys.argv[1:2] == ['--fill']:
        print('DONE', fill(diary._call_model, say=lambda m: print(m, flush=True)))
    else:
        f = run(diary._call_model, say=lambda m: print(m, flush=True))
        print('DONE 失败', f)
