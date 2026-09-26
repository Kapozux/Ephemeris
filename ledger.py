"""主题账本：让日记一天接一天连下去。

每篇日记只看当天的聊天，模型不知道昨天发生了什么；窗口【前情】只接得上**同一个窗口**，
换个窗口接着聊就断了。账本补的是跨窗口、跨天的那条线。

**只存事实，其余全靠算。** 表里只记「哪个主题 · 哪天 · 那天推进到哪」（theme_days）。
第几天、首次、上次、隔了多久，都是查询时按 `date < D` 现算的 —— 所以
重写中间某一天不会把后面的账算乱，也没有要级联更新的状态。

**模型只做判断，不做算术。** 写第 D 天时它拿到：截至 D 之前的账本（带编号、天数、
上次距今）+ 上一篇日记。它只回答「今天碰了哪几个编号、有没有新主题、各推进到哪」。
天数由 Python 数。
"""
import json
import re
from datetime import date as _date

import index as idx

ACTIVE_DAYS = 45        # 这么多天内出现过的主题，把现状也给模型看；更早的只给名字
MAX_ACTIVE = 60
MAX_DORMANT = 200


def ensure_tables():
    with idx.LOCK:
        db = idx.connect()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS themes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          created_on TEXT
        );
        CREATE TABLE IF NOT EXISTS theme_days (
          theme_id INTEGER NOT NULL,
          date TEXT NOT NULL,
          note TEXT,
          PRIMARY KEY (theme_id, date)
        );
        CREATE INDEX IF NOT EXISTS theme_days_date ON theme_days(date);
        """)
        # desc：手动合并过的主题，起头那天的 note 不一定代表整条（起头可能只是一条顺带的消息），
        # 这时用人写的一句话当身份锚
        if 'desc' not in [r[1] for r in db.execute('PRAGMA table_info(themes)')]:
            db.execute('ALTER TABLE themes ADD COLUMN desc TEXT')
        db.commit()


def _q(sql, *args):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, args).fetchall()]


def _gap(a, b):
    return (_date.fromisoformat(b) - _date.fromisoformat(a)).days


def as_of(date):
    """截至 date 之前（不含当天）的账本，最近出现的在前。"""
    ensure_tables()
    rows = _q("""SELECT t.id, t.name, t.desc, COUNT(*) n_days, MIN(d.date) first, MAX(d.date) last
                   FROM themes t JOIN theme_days d ON d.theme_id = t.id
                  WHERE d.date < ?
                  GROUP BY t.id ORDER BY last DESC, n_days DESC""", date)
    for r in rows:
        r['ago'] = _gap(r['last'], date)
        note = _q("SELECT note FROM theme_days WHERE theme_id=? AND date=?", r['id'], r['last'])
        r['state'] = note[0]['note'] if note else ''
        # 起头那天在干什么 —— 身份的锚。只给「当时到哪」的话，主题会一天漂一点：
        # 实测「CS IA」两周后装进了全部 CS 复习，最后连 Math 都算进去了
        first = _q("SELECT note FROM theme_days WHERE theme_id=? AND date=?", r['id'], r['first'])
        r['origin'] = r.get('desc') or (first[0]['note'] if first else '')
    return rows


def render_for_prompt(date):
    rows = as_of(date)
    if not rows:
        return '（账本还是空的——这是第一篇，今天出现的都是新主题。）'
    active = [r for r in rows if r['ago'] <= ACTIVE_DAYS][:MAX_ACTIVE]
    ids = {r['id'] for r in active}
    dormant = [r for r in rows if r['id'] not in ids][:MAX_DORMANT]

    def when(a):
        return '昨天' if a == 1 else f'{a} 天前'

    out = []
    for r in active:
        line = (f"#{r['id']} {r['name']}｜已聊 {r['n_days']} 天，今天再聊就是第 {r['n_days'] + 1} 天"
                f"｜上次 {when(r['ago'])}\n    "
                + (f"这条是：{r['desc']}" if r.get('desc') else f"起头（{r['first']}）：{r['origin']}"))
        if r['n_days'] > 1:
            line += f"\n    上次到哪：{r['state']}"
        out.append(line)
    if dormant:
        out.append('')
        out.append('更早的（只列名字，今天又聊到的话照样用编号）：')
        out.append('　'.join(f"#{r['id']} {r['name']}（{r['n_days']} 天，{when(r['ago'])}）"
                              for r in dormant))
    return '\n'.join(out)


def previous_diary(date):
    """date 之前最近的一篇日记。"""
    r = _q("""SELECT date, headline, narrative, open_loops FROM diaries
               WHERE date < ? ORDER BY date DESC LIMIT 1""", date)
    return r[0] if r else None


MATCH_PROMPT = """你在维护一本「主题账本」。每个主题是一件持续进行的具体的事。
下面是账本现有的主题，和今天日记里写到的几件事。逐条判断今天的每件事是不是账本里某个主题的**同一件事**。

判断标准：
- 同一件事 = 今天这件是那个主题的下一步（名字、「起头」「上次到哪」说的是它）。
- 只是同一门课、同一个领域、话题沾边，**不算**。例：「CS 期末复习」≠「CS IA：做 SWE Agent」；
  「经济 Paper1 备考」≠「经济 IA 改稿」；「看动漫」≠「某段人际关系」。
- 拿不准就判「不是」。建多了无害，并错了会把两件事搅成一锅。
- 确实是同一件事，就一定要对上，哪怕今天的说法不一样。

不是任何已有主题时，给它起个名字：只说一件事，2-10 字，不用「与」「和」「及」拼接两件事（人名里的「与某某」除外）。
今天的两件事如果对上同一个主题，都填那个编号。

【账本】
{ledger}

【今天的事】
{items}

只输出 JSON 数组，每件事一项，顺序和编号跟上面一致，不要 markdown：
[{{"i": 1, "id": 账本编号或 null, "name": "id 为 null 时的新名字"}}]
"""


def match(date, items, call_model):
    """今天的 [{name, note}] 对到账本编号上。单独一次调用：
    跟日记写在同一个 prompt 里时，模型被几万字聊天分了心，匹配规则基本不管用 ——
    实测 30 天里「CS IA」吞掉了全部 CS 复习，一个兴趣类主题吞掉了一整段不相干的生活线。"""
    items = [t for t in (items or []) if isinstance(t, dict) and (t.get('name') or '').strip()]
    if not items:
        return []
    led = render_for_prompt(date)
    if not as_of(date):                       # 账本是空的，全是新的，不用问
        return [{'id': None, 'name': t['name'], 'note': t.get('note', '')} for t in items]
    lines = '\n'.join(f"{i}. {t['name']}：{t.get('note', '')}" for i, t in enumerate(items, 1))
    raw = call_model(MATCH_PROMPT.format(ledger=led, items=lines))
    try:
        txt = re.search(r'\[.*\]', raw or '', re.S).group(0)
        got = {int(x['i']): x for x in json.loads(txt) if isinstance(x, dict) and 'i' in x}
    except (AttributeError, ValueError, TypeError, KeyError):
        got = {}                               # 解析不了就全当新的 —— 宁可多建，不乱并
    out = []
    for i, t in enumerate(items, 1):
        g = got.get(i) or {}
        out.append({'id': g.get('id'), 'name': (g.get('name') or t['name']), 'note': t.get('note', '')})
    return out


def record(date, threads):
    """把模型给的 threads 落进账本。返回实际记下的 [(theme_id, name)]。

    threads: [{"id": 12, "note": "..."} 或 {"id": null, "name": "新主题", "note": "..."}]
    编号不在账本里、或新主题跟已有的重名，都按名字对回已有主题 —— 模型偶尔会这么干。
    """
    ensure_tables()
    known = {r['id']: r['name'] for r in _q('SELECT id, name FROM themes')}
    by_name = {n: i for i, n in known.items()}
    done = {}
    with idx.LOCK:
        db = idx.connect()
        db.execute('DELETE FROM theme_days WHERE date=?', (date,))
        for t in threads or []:
            if not isinstance(t, dict):
                continue
            # 天数由这里算，模型写进 note 的「第 6 天」会跟真实数字打架，去掉
            note = re.sub(r'^[^，,。]{0,16}第\s*\d+\s*天[，,：:\s]*', '', (t.get('note') or '').strip())
            note = re.sub(r'第\s*\d+\s*天[，,]?', '', note).strip()[:120]
            tid = t.get('id')
            try:
                tid = int(str(tid).lstrip('#')) if tid not in (None, '') else None
            except ValueError:
                tid = None
            if tid not in known:
                name = (t.get('name') or '').strip()[:24]
                if not name:
                    continue
                tid = by_name.get(name)
                if tid is None:
                    cur = db.execute('INSERT INTO themes(name, created_on) VALUES (?,?)', (name, date))
                    tid = cur.lastrowid
                    known[tid] = name
                    by_name[name] = tid
            if tid in done:                      # 同一天同一主题只记一次，note 拼起来
                if note:
                    db.execute("UPDATE theme_days SET note = note || '；' || ? WHERE theme_id=? AND date=?",
                               (note, tid, date))
                continue
            db.execute('INSERT INTO theme_days VALUES (?,?,?)', (tid, date, note))
            done[tid] = known[tid]
        db.commit()
    return list(done.items())


def chips(date):
    """这一天碰了哪些主题，第几天、距上次隔多久 —— 日记页底下那一行。"""
    ensure_tables()
    rows = _q("""SELECT t.id, t.name, d.note FROM theme_days d JOIN themes t ON t.id = d.theme_id
                  WHERE d.date = ?""", date)
    for r in rows:
        prev = _q("""SELECT COUNT(*) n, MAX(date) last FROM theme_days
                      WHERE theme_id=? AND date < ?""", r['id'], date)[0]
        r['nth'] = prev['n'] + 1
        r['gap'] = _gap(prev['last'], date) if prev['last'] else None
    rows.sort(key=lambda r: -r['nth'])
    return rows


def all_themes():
    """主题页：每个主题一条时间线。"""
    ensure_tables()
    rows = _q("""SELECT t.id, t.name, COUNT(*) n_days, MIN(d.date) first, MAX(d.date) last
                   FROM themes t JOIN theme_days d ON d.theme_id = t.id
                  GROUP BY t.id ORDER BY last DESC, n_days DESC""")
    days = _q('SELECT theme_id, date, note FROM theme_days ORDER BY date')
    by = {}
    for d in days:
        by.setdefault(d['theme_id'], []).append({'date': d['date'], 'note': d['note']})
    for r in rows:
        r['days'] = by.get(r['id'], [])
        r['state'] = r['days'][-1]['note'] if r['days'] else ''
    return rows


def merge(into_name, theme_ids=(), days=()):
    """把几个主题（整条）和若干 (theme_id, date) 单天并成一条，叫 into_name。
    只改账本，不碰日记。同一天在几条里都有的，note 拼起来。返回新主题 id。"""
    ensure_tables()
    with idx.LOCK:
        db = idx.connect()
        row = db.execute('SELECT id FROM themes WHERE name=?', (into_name,)).fetchone()
        if row:
            tid = row[0]
        else:
            first = db.execute(f"SELECT MIN(date) FROM theme_days WHERE theme_id IN ({','.join('?' * len(theme_ids)) or 'NULL'})",
                               tuple(theme_ids)).fetchone()[0]
            tid = db.execute('INSERT INTO themes(name, created_on) VALUES (?,?)', (into_name, first)).lastrowid
        moves = [(i, d) for i in theme_ids
                 for (d,) in db.execute('SELECT date FROM theme_days WHERE theme_id=?', (i,)).fetchall()]
        moves += list(days)
        for src, d in moves:
            if src == tid:
                continue
            r = db.execute('SELECT note FROM theme_days WHERE theme_id=? AND date=?', (src, d)).fetchone()
            if not r:
                continue
            have = db.execute('SELECT note FROM theme_days WHERE theme_id=? AND date=?', (tid, d)).fetchone()
            if have:
                db.execute('UPDATE theme_days SET note=? WHERE theme_id=? AND date=?',
                           (f'{have[0]}；{r[0]}' if r[0] and r[0] not in have[0] else have[0], tid, d))
            else:
                db.execute('INSERT INTO theme_days VALUES (?,?,?)', (tid, d, r[0]))
            db.execute('DELETE FROM theme_days WHERE theme_id=? AND date=?', (src, d))
        db.execute('DELETE FROM themes WHERE id NOT IN (SELECT theme_id FROM theme_days)')
        db.commit()
    return tid


def reset():
    """全量重跑前清空。主题编号从 1 重来。"""
    ensure_tables()
    with idx.LOCK:
        db = idx.connect()
        db.execute('DELETE FROM theme_days')
        db.execute('DELETE FROM themes')
        db.execute("DELETE FROM sqlite_sequence WHERE name='themes'")
        db.commit()


# ------------------------------------------------------------------ 从现成日记补账本
# 旧日记是当时写的记录，不为了建账本去重写它们。每天读那天的日记（几百字），
# 抽出在推进的几件事，再走同一个 match() 对到账本上。

EXTRACT_PROMPT = """下面是一篇日记。列出这天实际花了功夫的几件事，通常 2-5 条，只顺口提了一句的不算。

- 一条只说一件事：「CS IA」和「CS 期末复习」是两条，不要合成「CS 复习与 IA」。
- 不是科目或领域（「经济」「学习」不行），是具体在弄的那件事（「经济 IA 改稿」「经济 Paper1 备考」）。
- name 2-10 字；note 写这天在这件事上推进到哪，一句话，不超过 40 字，不要写「第几天」。

只输出 JSON 数组，不要 markdown：
[{{"name": "...", "note": "..."}}]

--- {date} ---
{text}
"""


def _diary_text(d):
    out = [d.get('headline') or '', d.get('narrative') or '']
    for key, label in (('artifacts', '做出来的'), ('open_loops', '没做完的')):
        try:
            arr = json.loads(d.get(key) or '[]')
        except ValueError:
            arr = []
        if arr:
            out.append(label + '：' + '；'.join(arr))
    return '\n'.join(x for x in out if x)


def extract(d, call_model):
    raw = call_model(EXTRACT_PROMPT.format(date=d['date'], text=_diary_text(d)))
    try:
        arr = json.loads(re.search(r'\[.*\]', raw or '', re.S).group(0))
        return [x for x in arr if isinstance(x, dict) and x.get('name')]
    except (AttributeError, ValueError, TypeError):
        return None


def backfill(call_model, start=None, say=print):
    """从 start（默认最早）起，按日记逐天补账本。只写 theme_days/themes，不碰 diaries。"""
    ensure_tables()
    days = _q("SELECT * FROM diaries WHERE date >= ? ORDER BY date", start or '0000')
    if not start:
        reset()
    else:
        with idx.LOCK:
            db = idx.connect()
            db.execute('DELETE FROM theme_days WHERE date >= ?', (start,))
            db.execute('DELETE FROM themes WHERE id NOT IN (SELECT theme_id FROM theme_days)')
            db.commit()
    fails = []
    for i, d in enumerate(days, 1):
        items = extract(d, call_model) or extract(d, call_model)     # 解析失败重试一次
        if items is None:
            fails.append(d['date'])
            say(f"{i}/{len(days)} {d['date']} 抽不出来")
            continue
        got = record(d['date'], match(d['date'], items, call_model))
        say(f"{i}/{len(days)} {d['date']} " + ' · '.join(n for _, n in got))
    return fails


if __name__ == '__main__':
    import sys
    import diary
    f = backfill(diary._call_model, start=sys.argv[1] if len(sys.argv) > 1 else None,
                 say=lambda m: print(m, flush=True))
    print('DONE', '失败：' + ','.join(f) if f else '')
