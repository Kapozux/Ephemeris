"""按窗口回顾：一个对话窗口在时间轴上的演进。

和日记正好是转置关系 ——
  日记   date-major：这一天我在所有窗口里干了什么
  这里   conv-major：这一个窗口，从头到尾是怎么走的

只对**跨天的窗口**有意义（1657 个窗口里 165 个跨天，其余 90% 一天聊完，日记已经覆盖）。

产出两样：
  note  这个窗口在某一天推进到哪一步（一两句）
  arc   整个窗口是干嘛的、怎么演变的（两三句，由所有 note 汇总而来）

都是**按需生成**，点开哪个窗口才算哪个，不做全量批处理。
"""
import os
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'getAudio'))

import index as idx  # noqa: E402

PER_DAY_CHARS = 9000      # 单日喂进去的上限，长消息按比例截
MAX_DAYS = 40             # 超过这么多活跃日就抽样（目前最多 27 天，留余量）

NOTE_PROMPT = """下面是 __NAME__ 在**同一个对话窗口**里、{date} 这一天发给 AI 的消息。
这个窗口从 {since} 开始，到今天为止一共聊了 {n_days} 天。

{prior}

写一句话，说清**这一天在这条线上推进到了哪一步**。

这句话会显示在一个左边已经标好日期的时间轴上，所以：
- **直接从动词或名词开头**。不要写「__NAME__」「他」「今天」「这一天」——主语和时间都是多余的。
  ✗ __NAME__ 今天推进了经济暑假作业的 Paper 1
  ✓ 推进经济暑假作业 Paper 1 的 15 分题，核对了公共物品与优点物品的定义

要求：
- 20-45 字，一句话，不分点。
- 只说这个窗口里的事。别的窗口、别的话题一概不提。
- 有具体的就留具体的：科目、报错、文件名、决定、数字、人名。
- 零散几句没实质推进的，就直说「没实质推进，只问了 X」。
- 不要评价他，不要给建议，不要用「仿佛」「不禁」这类修辞。

只输出这一句话本身，不要引号，不要任何前缀。

--- {date} 这天在本窗口的消息 ---
{body}
"""

ARC_PROMPT = """下面是同一个对话窗口在 {n_days} 天里的逐日小结，按时间正序。

窗口标题是「{title}」——**标题是他自己随手改的，不可靠，只当参考，以内容为准。**

写两到三句话，概括这个窗口：第一句说它是干嘛的，后面说它怎么演变、最后停在哪。

要求：
- 60-120 字。直白，不要比喻和抒情。
- 陈述事实。不要「你」「我」，也不要反复点名「__NAME__」——最多开头提一次，能不提就不提。
- 只写小结里有的。没有的不要补。

只输出这段话本身，不要标题，不要引号。

--- 逐日小结 ---
{notes}
"""

NOTE_PROMPT = NOTE_PROMPT.replace('__NAME__', idx.who())
ARC_PROMPT = ARC_PROMPT.replace('__NAME__', idx.who())


def _call_model(prompt):
    from reflect import _call_model as m
    return m(prompt)


# ------------------------------------------------------------------- 存储

LOCK = idx.LOCK


def ensure_table():
    with LOCK:
        c = idx.connect()
        c.execute("""CREATE TABLE IF NOT EXISTS conv_notes (
                        conv_id TEXT NOT NULL,
                        date    TEXT NOT NULL,     -- '' 表示整体 arc
                        text    TEXT NOT NULL,
                        generated_at TEXT NOT NULL,
                        PRIMARY KEY (conv_id, date))""")
        c.commit()


def _save(conv_id, date, text):
    # 时间用本地 isoformat，跟 diaries.generated_at 一个口径。
    # SQLite 的 datetime('now') 是 UTC，前端 rel() 按本地算，会差 8 小时。
    now = datetime.now().isoformat(timespec='seconds')
    with LOCK:
        c = idx.connect()
        c.execute('INSERT OR REPLACE INTO conv_notes VALUES (?,?,?,?)',
                  (conv_id, date, text, now))
        c.commit()


def notes_for(conv_id):
    """-> {'arc': str|None, 'days': {date: text}, 'at': iso|None}"""
    ensure_table()
    with LOCK:
        rows = list(idx.connect().execute(
            'SELECT date, text, generated_at FROM conv_notes WHERE conv_id=?', (conv_id,)))
    out = {'arc': None, 'days': {}, 'at': None}
    for r in rows:
        if r['date']:
            out['days'][r['date']] = r['text']
        else:
            out['arc'] = r['text']
            out['at'] = r['generated_at']
    return out


def clear(conv_id):
    ensure_table()
    with LOCK:
        c = idx.connect()
        c.execute('DELETE FROM conv_notes WHERE conv_id=?', (conv_id,))
        c.commit()


# ------------------------------------------------------------------- 取料

def resolve(conv_id):
    """允许用 id 前缀（命令行里列表是截断显示的）。"""
    with LOCK:
        c = idx.connect()
        if c.execute('SELECT 1 FROM conversations WHERE id=?', (conv_id,)).fetchone():
            return conv_id
        hit = c.execute('SELECT id FROM conversations WHERE id LIKE ? LIMIT 2',
                        (conv_id + '%',)).fetchall()
    return hit[0]['id'] if len(hit) == 1 else None


def window(conv_id):
    with LOCK:
        c = idx.connect()
        conv = c.execute('SELECT * FROM conversations WHERE id=?', (conv_id,)).fetchone()
        if not conv:
            return None
        days = list(c.execute("""SELECT date, COUNT(*) msgs, SUM(chars) chars,
                                        MIN(hhmm) a, MAX(hhmm) b
                                   FROM messages WHERE conv_id=? AND role='human'
                                  GROUP BY date ORDER BY date""", (conv_id,)))
    return {'conv': dict(conv), 'days': [dict(d) for d in days]}


def day_text(conv_id, date, budget=PER_DAY_CHARS):
    """这个窗口在这一天的消息，拼成喂模型的纯文本。长消息按人头均分预算。"""
    with LOCK:
        msgs = list(idx.connect().execute(
            """SELECT hhmm, text FROM messages
                WHERE conv_id=? AND date=? AND role='human' ORDER BY ts""", (conv_id, date)))
    if not msgs:
        return ''
    per = max(budget // len(msgs), 200)
    lines, used = [], 0
    for m in msgs:
        t = m['text']
        if len(t) > per:
            t = t[:per] + ' …（截断）'
        if used + len(t) > budget:
            lines.append('  …（后续省略）')
            break
        lines.append(f"  [{m['hhmm']}] {t}")
        used += len(t)
    return '\n'.join(lines)


def _prior(notes, days, i):
    """给第 i 天的 prompt 用的前情：前面几天的小结。"""
    if i == 0:
        return '这是这个窗口的第一天。'
    before = [f"  {d['date']}：{notes.get(d['date'], '')}" for d in days[:i] if notes.get(d['date'])]
    if not before:
        return '这是这个窗口的第一天。'
    tail = before[-4:]
    head = '【前面几天在这条线上聊到哪了（背景，不是今天的事）】\n'
    return head + '\n'.join(tail)


# ------------------------------------------------------------------- 生成

_jobs = {}        # conv_id -> {'running','total','done','current'}


def job(conv_id):
    return _jobs.get(conv_id) or {'running': False, 'total': 0, 'done': 0, 'current': None}


def generate(conv_id, refresh=False, progress=None):
    """把一个窗口的逐日小结 + 整体 arc 生成出来。已有的默认跳过。"""
    ensure_table()
    w = window(conv_id)
    if not w:
        return {'error': 'not found'}
    days = w['days']
    if len(days) > MAX_DAYS:                      # 太长就只取字数最多的那些天
        keep = sorted(sorted(days, key=lambda d: -(d['chars'] or 0))[:MAX_DAYS],
                      key=lambda d: d['date'])
        days = keep
    if refresh:
        clear(conv_id)
    have = notes_for(conv_id)
    notes = dict(have['days'])

    todo = [d for d in days if d['date'] not in notes]
    st = _jobs.setdefault(conv_id, {})
    st.update(running=True, total=len(todo) + 1, done=0, current=None)
    try:
        for i, d in enumerate(days):
            if d['date'] in notes:
                continue
            st['current'] = d['date']
            body = day_text(conv_id, d['date'])
            if not body:
                continue
            p = NOTE_PROMPT.format(date=d['date'], since=days[0]['date'], n_days=len(days),
                                   prior=_prior(notes, days, i), body=body)
            try:
                txt = (_call_model(p) or '').strip().strip('"').strip('「」')
            except Exception as e:                          # noqa: BLE001
                txt = f'（生成失败：{e}）'
            notes[d['date']] = txt.split('\n')[0][:200]
            _save(conv_id, d['date'], notes[d['date']])
            st['done'] += 1
            if progress:
                progress(st['done'], st['total'], d['date'])

        st['current'] = 'arc'
        joined = '\n'.join(f"{d['date']}：{notes.get(d['date'], '')}" for d in days
                           if notes.get(d['date']))
        title = w['conv'].get('title') or w['conv'].get('title_fallback') or ''
        try:
            arc = (_call_model(ARC_PROMPT.format(
                n_days=len(days), title=title, notes=joined)) or '').strip()
        except Exception as e:                              # noqa: BLE001
            arc = f'（生成失败：{e}）'
        _save(conv_id, '', arc.strip('"').strip('「」')[:800])
        st['done'] += 1
    finally:
        st.update(running=False, current=None)
    return notes_for(conv_id)


def generate_bg(conv_id, refresh=False):
    if job(conv_id)['running']:
        return
    threading.Thread(target=generate, args=(conv_id, refresh), daemon=True).start()


# -------------------------------------------------------------------- CLI

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('conv_id', nargs='?')
    ap.add_argument('--list', action='store_true', help='列出跨天的窗口')
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    if args.list or not args.conv_id:
        with LOCK:
            rows = list(idx.connect().execute("""
                SELECT c.id, c.title, COUNT(DISTINCT m.date) days, COUNT(*) msgs
                  FROM messages m JOIN conversations c ON c.id=m.conv_id
                 WHERE m.role='human' GROUP BY m.conv_id HAVING days > 1
                 ORDER BY days DESC"""))
        print(f'{len(rows)} 个跨天窗口')
        for r in rows[:40]:
            print(f"  {r['days']:3}天 {r['msgs']:5}条  {r['id'][:12]}  {(r['title'] or '')[:40]}")
        raise SystemExit

    def show(done, total, date):
        print(f'  [{done}/{total}] {date}', flush=True)

    cid = resolve(args.conv_id)
    if not cid:
        print(f'找不到窗口（或前缀不唯一）：{args.conv_id}'); raise SystemExit(1)

    r = generate(cid, refresh=args.refresh, progress=show)
    print('\n--- arc ---')
    print(r.get('arc'))
    print('\n--- 逐日 ---')
    for d, t in sorted(r['days'].items()):
        print(f'  {d}  {t}')
