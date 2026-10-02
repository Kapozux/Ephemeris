"""情绪实验第二轮：情绪信号到底在哪一步丢的。

参照 = 手写日记打的分（mood_scores src='hand'）。两份都有的天里抽 40 天，比：
  chat_lite   当天聊天原文（自己打的全留，粘贴只留头尾）→ flash-lite 打分     原文里有没有信号
  chat_flash  同上 → 3.5-flash 打分                                         是不是模型太弱
  ai          现有 AI 日记 → flash-lite（第一轮已经有）                       写日记这步丢了多少
  diary2      用允许记录情绪的新 prompt 重写一篇日记（只写进实验表）→ flash-lite   改好之后能不能追上原文

新日记只进 `diaries_lab`，现有日记一篇不动。
"""
import json
import os
import random
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle                   # noqa: E402

import diary                    # noqa: E402
import llm                      # noqa: E402
import index as idx             # noqa: E402
import mood                     # noqa: E402
from slice import render_day    # noqa: E402

LITE = 'gemini-3.5-flash-lite'
FLASH = 'gemini-3.5-flash'
N = 40


def call(prompt, model):
    """实验要能指定模型；走 llm.py，不再自己建 Gemini client。"""
    return llm.ask(prompt, model=model)


CHAT_NOTE = ('【聊天原文】这是他当天发给 AI 的全部消息（只有他自己说的，没有 AI 的回复）。'
             '他自己打的字全部保留；贴进去的长材料（文章、代码、字幕、AI 的回答）只留了头尾。')
DIARY2_NOTE = '【AI 日记】这是根据他当天和 AI 的聊天整理出来的日记。'

# 新日记 prompt：在原 prompt 上改三处 —— 允许写他自己说出来的感受、多一个 feelings 字段、mood 不再默认平稳
DIARY2_PROMPT = (diary.PROMPT
    .replace('- 只写材料里有的。不要推测他的情绪，不要评价他，不要给建议。没聊到的不要补。',
             '- 只写材料里有的。不要评价他，不要给建议。没聊到的不要补。\n'
             '- **他自己说出来的感受要写进去**（烦、累、焦虑、开心、崩溃、纠结、得意……），尽量用他的原话或接近原话。'
             '他没说出来的不要猜。感受是这天的一部分，不要为了「客观」把它删掉。')
    .replace('  "mood": "一个词：平稳/焦虑/兴奋/疲惫/烦躁/低落。只根据他自己说的话判断，看不出就写 平稳",',
             '  "feelings": "这天他自己表达出来的情绪和感受，1-3 句，带原话；完全没流露就写空字符串",\n'
             '  "mood": "一个词：开心/满足/兴奋/平稳/疲惫/焦虑/烦躁/低落/崩溃。看整天占主导的那一面，看得出倾向就别写平稳",'))
# 2026-09-27 之后线上的 diary.PROMPT 已经采用了这套写法（「做了啥感觉啥」），上面的替换找不到原句；
# 那就直接用线上的 —— 实验要比的「新 prompt」就是它。
if DIARY2_PROMPT == diary.PROMPT:
    DIARY2_PROMPT = diary.PROMPT


def _q(sql, *a):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, a).fetchall()]


def ensure():
    mood.ensure_table()
    with idx.LOCK:
        db = idx.connect()
        db.execute("""CREATE TABLE IF NOT EXISTS diaries_lab (
            date TEXT PRIMARY KEY, headline TEXT, narrative TEXT, feelings TEXT, mood TEXT, raw TEXT, made_at TEXT)""")
        db.execute('CREATE TABLE IF NOT EXISTS mood_lab2_pick (date TEXT PRIMARY KEY)')
        db.commit()


def pick():
    have = [r['date'] for r in _q('SELECT date FROM mood_lab2_pick ORDER BY date')]
    if have:
        return have
    both = [r['date'] for r in _q("""SELECT a.date FROM mood_scores a JOIN mood_scores h
                                       ON h.date=a.date AND h.src='hand' WHERE a.src='ai'""")]
    random.Random(11).shuffle(both)
    days = sorted(both[:N])
    with idx.LOCK:
        db = idx.connect()
        db.executemany('INSERT INTO mood_lab2_pick VALUES (?)', [(d,) for d in days])
        db.commit()
    return days


def _save(date, src, r):
    with idx.LOCK:
        db = idx.connect()
        db.execute('INSERT OR REPLACE INTO mood_scores VALUES (?,?,?,?,?,?)',
                   (date, src, r['valence'], r['label'], r['evidence'], datetime.now().isoformat(timespec='seconds')))
        db.commit()


def _score(material, model):
    return mood._parse(call(mood.PROMPT.format(material=material, labels='/'.join(mood.LABELS)), model))


def make_diary2(date):
    day = bundle.day_bundle(date)
    import ledger
    prompt = DIARY2_PROMPT.format(date=date, n_threads=day['n_threads'], body=render_day(day),
                                  prev=diary._render_prev(ledger.previous_diary(date), date),
                                  ledger=ledger.render_for_prompt(date) or '（账本还是空的。）')
    raw = call(prompt, LITE)
    d = llm.parse_json(raw, dict)
    if not d:
        return None
    with idx.LOCK:
        db = idx.connect()
        db.execute('INSERT OR REPLACE INTO diaries_lab VALUES (?,?,?,?,?,?,?)',
                   (date, d.get('headline', ''), d.get('narrative', ''), d.get('feelings', ''), d.get('mood', ''),
                    raw, datetime.now().isoformat(timespec='seconds')))
        db.commit()
    return d


def run(say=print):
    ensure()
    days = pick()
    done = {(r['date'], r['src']) for r in _q('SELECT date, src FROM mood_scores')}
    have2 = {r['date'] for r in _q('SELECT date FROM diaries_lab')}
    for i, d in enumerate(days, 1):
        chat = CHAT_NOTE + '\n\n' + render_day(bundle.day_bundle(d))
        for src, model in (('chat_lite', LITE), ('chat_flash', FLASH)):
            if (d, src) not in done:
                r = _score(chat, model) or _score(chat, model)
                if r:
                    _save(d, src, r)
        if d not in have2:
            make_diary2(d) or make_diary2(d)
        if (d, 'diary2') not in done:
            row = _q('SELECT headline, narrative, feelings FROM diaries_lab WHERE date=?', d)
            if row:
                x = row[0]
                r = _score(f"{DIARY2_NOTE}\n\n{x['headline']}\n{x['narrative']}\n感受：{x['feelings']}", LITE)
                if r:
                    _save(d, 'diary2', r)
        say(f'{i}/{len(days)} {d}')


def report():
    days = pick()
    sc = {}
    for r in _q('SELECT date, src, valence FROM mood_scores'):
        sc.setdefault(r['date'], {})[r['src']] = r['valence']
    rows = []
    for src, name in (('chat_lite', '聊天原文 · flash-lite'), ('chat_flash', '聊天原文 · 3.5-flash'),
                      ('ai', '现有 AI 日记 · flash-lite'), ('diary2', '新 prompt 日记 · flash-lite')):
        pairs = [(sc[d][src], sc[d]['hand']) for d in days if src in sc.get(d, {}) and 'hand' in sc[d]]
        if not pairs:
            continue
        xs, ys = zip(*pairs)
        rows.append({'src': src, 'name': name, 'n': len(pairs), 'corr': mood._corr(list(xs), list(ys)),
                     'mae': round(sum(abs(x - y) for x, y in pairs) / len(pairs), 2),
                     'mean': round(sum(xs) / len(xs), 2),
                     'pos': round(sum(x > 0 for x in xs) / len(xs), 2)})
    hand = [sc[d]['hand'] for d in days if 'hand' in sc.get(d, {})]
    return {'days': len(days), 'hand_mean': round(sum(hand) / len(hand), 2),
            'hand_pos': round(sum(x > 0 for x in hand) / len(hand), 2), 'rows': rows}


if __name__ == '__main__':
    if sys.argv[1:2] == ['--report']:
        print(json.dumps(report(), ensure_ascii=False, indent=1))
    else:
        run(say=lambda m: print(m, flush=True))
        print(json.dumps(report(), ensure_ascii=False, indent=1))
