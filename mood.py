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
    import llm
    d = llm.parse_json(raw, dict)
    if not d:
        return None
    try:
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
    import llm
    d = llm.parse_json(call_model(FRICTION_PROMPT.format(material=render_day(day))), dict) or {}
    try:
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
        feel = {'src': 'checkin', 'valence': round(v, 1), 'label': ck[0]['word'] if len(ck) == 1 else f'{len(ck)} 条的平均',
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


if __name__ == '__main__':
    import llm
    if sys.argv[1:2] == ['--fill']:
        print('DONE', fill(llm.ask, say=lambda m: print(m, flush=True)))
    else:
        f = run(llm.ask, say=lambda m: print(m, flush=True))
        print('DONE 失败', f)
