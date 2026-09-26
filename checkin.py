"""每日打卡：点一个情绪词，可选写一句。可以补前几天、同一天加好几条、改、删。

为什么要有：情绪实验（2026-09-27）的结论是，从 AI 聊天推不出「那天过得怎么样」——
聊天是跟问题较劲的那部分，开心的事发生在聊天外。靠得住的只有本人自己说的。
手写日记只覆盖一半的天，打卡用最小的动作（10 秒）把剩下的补上。

「过得怎么样」的优先级：打卡（自己明确说的）> 手写日记打的分（模型读出来的）。

星座图：一个月一张，一天一颗星，高度 = 那天过得怎么样。配一句话，由模型根据算好的数字写，
不让它自己数。
"""
import hashlib
import json
from datetime import datetime, timedelta

import index as idx
from slice import DAY_CUTOFF_HOUR, LOCAL_TZ

# 词 → 分数（-2..+2）。顺序就是界面上的顺序：从好到糟
WORDS = [('兴奋', 2), ('开心', 1.5), ('满足', 1), ('平稳', 0), ('说不上来', 0), ('疲惫', -0.5),
         ('焦虑', -1), ('烦躁', -1), ('低落', -1.5), ('崩溃', -2)]
VALUE = dict(WORDS)


def ensure_table():
    with idx.LOCK:
        db = idx.connect()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS checkins (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          date TEXT NOT NULL, word TEXT NOT NULL, valence REAL NOT NULL,
          note TEXT DEFAULT '', created_at TEXT, updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS checkins_date ON checkins(date);
        CREATE TABLE IF NOT EXISTS star_notes (
          month TEXT PRIMARY KEY, fingerprint TEXT, text TEXT, made_at TEXT
        );
        """)
        # reply：记下之后 AI 回的那几句（打卡时的「陪伴」口吻，跟日记/回顾的「镜子」口吻分开）
        if 'reply' not in [r[1] for r in db.execute('PRAGMA table_info(checkins)')]:
            db.execute('ALTER TABLE checkins ADD COLUMN reply TEXT')
        db.commit()


def _q(sql, *a):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, a).fetchall()]


def _now():
    return datetime.now().isoformat(timespec='seconds')


def today():
    """按 Ephemeris 的一天：凌晨 4 点前还算前一天。"""
    local = datetime.now(LOCAL_TZ)
    if local.hour < DAY_CUTOFF_HOUR:
        local -= timedelta(days=1)
    return local.date().isoformat()


# ------------------------------------------------------------------ 增删改查

def add(date, word, note=''):
    ensure_table()
    if word not in VALUE:
        raise ValueError(f'不认识的词：{word}')
    datetime.strptime(date, '%Y-%m-%d')
    with idx.LOCK:
        db = idx.connect()
        cur = db.execute('INSERT INTO checkins(date, word, valence, note, created_at, updated_at) VALUES (?,?,?,?,?,?)',
                         (date, word, VALUE[word], (note or '').strip()[:500], _now(), _now()))
        db.commit()
        return cur.lastrowid


def update(cid, word=None, note=None):
    ensure_table()
    r = _q('SELECT * FROM checkins WHERE id=?', cid)
    if not r:
        raise KeyError(cid)
    r = r[0]
    word = word or r['word']
    if word not in VALUE:
        raise ValueError(f'不认识的词：{word}')
    with idx.LOCK:
        db = idx.connect()
        db.execute('UPDATE checkins SET word=?, valence=?, note=?, updated_at=? WHERE id=?',
                   (word, VALUE[word], (r['note'] if note is None else note).strip()[:500], _now(), cid))
        db.commit()


def delete(cid):
    with idx.LOCK:
        db = idx.connect()
        db.execute('DELETE FROM checkins WHERE id=?', (cid,))
        db.commit()


def for_date(date):
    ensure_table()
    return _q('SELECT id, date, word, valence, note, reply, created_at FROM checkins WHERE date=? ORDER BY created_at', date)


def day_values():
    """{date: 当天各条打卡的平均分}"""
    ensure_table()
    return {r['date']: round(r['v'], 2) for r in _q('SELECT date, AVG(valence) v FROM checkins GROUP BY date')}


def dates():
    ensure_table()
    return [r['date'] for r in _q('SELECT DISTINCT date FROM checkins ORDER BY date')]


# ------------------------------------------------------------------ 星座图

def month_stars(month):
    """一个月里每天的「过得怎么样」：有打卡用打卡（实心星），没有就用手写日记的分（空心星）。"""
    ensure_table()
    ck = {}
    for r in _q("SELECT date, word, valence, note FROM checkins WHERE date LIKE ? ORDER BY created_at", month + '%'):
        ck.setdefault(r['date'], []).append(r)
    hand = {r['date']: r for r in _q("""SELECT date, valence, label, evidence FROM mood_scores
                                          WHERE src='hand' AND date LIKE ?""", month + '%')}
    stars = []
    for d in sorted(set(ck) | set(hand)):
        if d in ck:
            rows = ck[d]
            stars.append({'date': d, 'v': round(sum(x['valence'] for x in rows) / len(rows), 2), 'src': 'checkin',
                          'word': rows[-1]['word'], 'n': len(rows),
                          'note': '；'.join(x['note'] for x in rows if x['note'])})
        else:
            h = hand[d]
            stars.append({'date': d, 'v': h['valence'], 'src': 'hand', 'word': h['label'] or '',
                          'n': 1, 'note': h['evidence'] or ''})
    return stars


def _stats(stars):
    if not stars:
        return None
    vs = [s['v'] for s in stars]
    good, bad = sum(v > 0 for v in vs), sum(v < 0 for v in vs)
    best = max(stars, key=lambda s: s['v'])
    worst = min(stars, key=lambda s: s['v'])
    # 最长连续好 / 糟的天数（日期相邻才算连续）
    def run(pred):
        longest = cur = 0
        prev = None
        for st in stars:
            d = datetime.strptime(st['date'], '%Y-%m-%d').date()
            if pred(st['v']):
                cur = cur + 1 if prev and (d - prev[0]).days == 1 and pred(prev[1]) else 1
            else:
                cur = 0
            longest = max(longest, cur)
            prev = (d, st['v'])
        return longest
    return {'days': len(stars), 'checkin_days': sum(s['src'] == 'checkin' for s in stars),
            'mean': round(sum(vs) / len(vs), 2), 'good': good, 'bad': bad,
            'best': {'date': best['date'], 'word': best['word'], 'note': best['note'][:60]},
            'worst': {'date': worst['date'], 'word': worst['word'], 'note': worst['note'][:60]},
            'good_streak': run(lambda v: v > 0), 'bad_streak': run(lambda v: v < 0)}


def _prev_month(month):
    y, m = map(int, month.split('-'))
    return f'{y - 1}-12' if m == 1 else f'{y}-{m - 1:02d}'


NOTE_PROMPT = """下面是一个人某个月每天「过得怎么样」的统计（-2 很糟 … +2 很好），以及上个月的同样统计。
写**一句话**概括这个月，给他自己看。

要求：
- 只用下面给的数字和事实，不要自己算新数字，不要编。
- **不要说「均值」「分数」「valence」这类词，不要出现小数**。用天数说话（「15 天糟、1 天好」「连着 10 天」），
  比上个月好还是差用 mean 判断，但只说「比上个月好/差」，不报那个数。日期写成「6 月 6 日」。
- 直白，先说最突出的一点（比上个月好还是差、哪段连着好/糟、最好/最糟那天是什么）。
- 不要比喻，不要抒情，不要安慰，不要建议。不超过 45 个字。用「你」称呼。
- 只输出这一句话，不要引号。

这个月（{month}）：{cur}
上个月（{prev}）：{prv}
"""


def month_note(month, call_model=None, refresh=False):
    """星座图下面那一句话。数据没变就用缓存；变了（新打卡、改了）才重写。"""
    ensure_table()
    cur = _stats(month_stars(month))
    if not cur:
        return ''
    prv = _stats(month_stars(_prev_month(month)))
    fp = hashlib.sha1(json.dumps([cur, prv], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    have = _q('SELECT fingerprint, text FROM star_notes WHERE month=?', month)
    if have and have[0]['fingerprint'] == fp and not refresh:
        return have[0]['text']
    if call_model is None:
        return have[0]['text'] if have else ''
    prompt = NOTE_PROMPT.format(month=month, prev=_prev_month(month), cur=json.dumps(cur, ensure_ascii=False),
                                prv=json.dumps(prv, ensure_ascii=False) if prv else '没有数据')
    text = (call_model(prompt) or call_model(prompt) or '').strip()      # 模型偶尔回空，再试一次
    text = text.strip('「」"“”').split('\n')[0][:80]
    if text:
        with idx.LOCK:
            db = idx.connect()
            db.execute('INSERT OR REPLACE INTO star_notes VALUES (?,?,?,?)', (month, fp, text, _now()))
            db.commit()
    return text


def month_stats(month):
    return _stats(month_stars(month))


# ------------------------------------------------------------------ 打卡页：进度、光球、月视图

def progress():
    """光球总数、打过卡的天数、连到今天的连续天数（今天还没打就从昨天往回数）。"""
    ensure_table()
    n = _q('SELECT COUNT(*) c FROM checkins')[0]['c']
    ds = set(dates())
    streak, d = 0, datetime.strptime(today(), '%Y-%m-%d').date()
    if d.isoformat() not in ds:
        d -= timedelta(days=1)
    while d.isoformat() in ds:
        streak += 1
        d -= timedelta(days=1)
    return {'moments': n, 'days': len(ds), 'streak': streak, 'today_done': today() in ds}


def orbs(limit=60):
    ensure_table()
    return _q('SELECT id, date, word, note, created_at FROM checkins ORDER BY created_at DESC LIMIT ?', limit)


def month_view(month):
    """日历每天一颗星（颜色 = 那天的词：打卡取最后一条，没打卡用手写读出来的词），外加这个月的构成比例。"""
    stars = month_stars(month)
    ck = {}
    for r in _q("SELECT word FROM checkins WHERE date LIKE ?", month + '%'):
        ck[r['word']] = ck.get(r['word'], 0) + 1
    # 构成：打卡按条数算（一刻一份），没打卡的天用手写读出来的词、一天一份
    comp = dict(ck)
    for st in stars:
        if st['src'] == 'hand' and st['word']:
            comp[st['word']] = comp.get(st['word'], 0) + 1
    total = sum(comp.values()) or 1
    order = [w for w, _ in WORDS]
    composition = [{'word': w, 'n': comp[w], 'pct': round(comp[w] / total * 100)}
                   for w in order if comp.get(w)]
    return {'month': month, 'stars': stars, 'composition': composition,
            'stats': month_stats(month), 'note': month_note(month)}


# ------------------------------------------------------------------ 打卡时的回话（陪伴口吻）

ORACLE_PROMPT = """你是一个高中生的陪伴者。他刚刚记下了此刻的心情，你回他几句。

口吻：
- 像一个懂他、站在他这边的朋友，不是老师、不是心理医生。先接住他的感受，再说别的。
- **具体**：尽量扣住他正在做的具体事情（下面给了他最近在忙什么、昨天的日记），不要空泛的鸡汤。
- 可以给一个很小、今天就能做的建议；也可以只是陪着说两句，不是每次都要建议。
- 不说教，不评判，不诊断，不用「亲」「宝」这类称呼，不用感叹号堆砌。
- 2 到 4 句，总共不超过 120 字，用中文，用「你」称呼他。
- 如果他流露出伤害自己的念头，温和但明确地建议他现在就联系信任的大人、学校心理老师或当地的心理援助热线。

他此刻：{word}{note}
今天更早记的：{earlier}
最近 7 天每天过得怎么样（-2 很糟 … +2 很好）：{recent}
他最近在忙的事：{themes}
昨天的日记：{yesterday}

只输出你要对他说的话。
"""


def _oracle_context(date):
    import ledger
    d0 = datetime.strptime(date, '%Y-%m-%d').date()
    recent = []
    for i in range(7, 0, -1):
        d = (d0 - timedelta(days=i)).isoformat()
        ck = _q('SELECT word FROM checkins WHERE date=?', d)
        hd = _q("SELECT label FROM mood_scores WHERE date=? AND src='hand'", d)
        w = ck[-1]['word'] if ck else (hd[0]['label'] if hd else None)
        if w:
            recent.append(f'{d[5:]} {w}')
    themes = [f"{r['name']}（上次到：{r['state'][:30]}）" for r in ledger.as_of(date)[:5] if r['ago'] <= 10]
    y = _q('SELECT headline, narrative FROM diaries WHERE date=?', (d0 - timedelta(days=1)).isoformat())
    yesterday = f"{y[0]['headline']}。{(y[0]['narrative'] or '')[:300]}" if y else '（没有）'
    return '；'.join(recent) or '（没有记录）', '；'.join(themes) or '（不清楚）', yesterday


def oracle(cid, call_model):
    """给一条打卡写回话，存下来。"""
    ensure_table()
    r = _q('SELECT * FROM checkins WHERE id=?', cid)
    if not r:
        return ''
    r = r[0]
    earlier = [f"{x['created_at'][11:16]} {x['word']}" + (f"：{x['note']}" if x['note'] else '')
               for x in for_date(r['date']) if x['id'] != cid and x['created_at'] < r['created_at']]
    recent, themes, yesterday = _oracle_context(r['date'])
    prompt = ORACLE_PROMPT.format(word=r['word'], note=f"——「{r['note']}」" if r['note'] else '（没写话）',
                                  earlier='；'.join(earlier) or '（没有）', recent=recent,
                                  themes=themes, yesterday=yesterday)
    text = (call_model(prompt) or call_model(prompt) or '').strip().strip('「」"“”')
    if text:
        with idx.LOCK:
            db = idx.connect()
            db.execute('UPDATE checkins SET reply=? WHERE id=?', (text[:400], cid))
            db.commit()
    return text
