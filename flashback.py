"""回忆卡：翻一张过去的某一天。

不是纯随机跳日记页：
- 只从有内容的天里抽（有 AI 日记，或手写 ≥ 100 字），最近 30 天不抽（你还记得）；
- 有手写、情绪明显、当天的事「后来还有下文」的天，权重高一点；最近翻过的不会马上再来；
- 卡片上最打动人的是「后来」：那天在弄的事，之后又做了多少天、最后一次是哪天、还是再也没碰。
  这是主题账本才给得出的。

另有「去年今天」：往年同一天，每天固定一张，当作每天打开的理由。纯本地查询，不调模型。
"""
import random
import re
from datetime import date as _date, datetime, timedelta

import checkin
import index as idx
import ledger
import mood

RECENT_DAYS = 30          # 这么近的不抽
SEEN_BLOCK = 25           # 最近翻过的这么多张不再抽


def ensure_table():
    with idx.LOCK:
        db = idx.connect()
        db.execute('CREATE TABLE IF NOT EXISTS flashback_seen (date TEXT, shown_at TEXT)')
        db.commit()


def _q(sql, *a):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, a).fetchall()]


def _today():
    return _date.fromisoformat(checkin.today())


# ------------------------------------------------------------------ 抽哪天

def candidates():
    """{date: 权重}"""
    cutoff = (_today() - timedelta(days=RECENT_DAYS)).isoformat()
    days = {r['date'] for r in _q('SELECT date FROM diaries WHERE date < ?', cutoff)}
    hand = {r['date'] for r in _q('SELECT date, SUM(chars) c FROM journal WHERE date < ? GROUP BY date HAVING c >= 100', cutoff)}
    days |= hand
    sig = mood.all_signals()
    # 当天碰过的主题里，有没有「之后还出现过」的
    later = {r['date'] for r in _q("""SELECT DISTINCT a.date FROM theme_days a
                                      WHERE EXISTS (SELECT 1 FROM theme_days b
                                                     WHERE b.theme_id = a.theme_id AND b.date > a.date)""")}
    seen = {r['date'] for r in _q('SELECT date FROM flashback_seen ORDER BY shown_at DESC LIMIT ?', SEEN_BLOCK)}
    w = {}
    for d in days:
        if d in seen:
            continue
        x = 1.0
        if d in hand:
            x += 1.5
        f = (sig.get(d) or {}).get('mood')
        if f is not None and abs(f) >= 1:
            x += 1.0
        if d in later:
            x += 1.0
        w[d] = x
    return w


def pick_random():
    ensure_table()
    w = candidates()
    if not w:
        return None
    ds = list(w)
    d = random.choices(ds, weights=[w[x] for x in ds], k=1)[0]
    with idx.LOCK:
        db = idx.connect()
        db.execute('INSERT INTO flashback_seen VALUES (?,?)', (d, datetime.now().isoformat(timespec='seconds')))
        db.commit()
    return d


def year_ago():
    """往年的今天：最近那一年有内容的同月同日。"""
    t = _today()
    for back in range(1, 6):
        try:
            d = t.replace(year=t.year - back).isoformat()
        except ValueError:                       # 2 月 29 日
            continue
        if _q('SELECT 1 FROM diaries WHERE date=? UNION SELECT 1 FROM journal WHERE date=? AND chars >= 30', d, d):
            return d
    return None


# ------------------------------------------------------------------ 卡片内容

_SKIP = re.compile(r'^(\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\[(图片|视频|文件|音频)|https?://|#+\s|———)')


_TEMPLATE = None


def _template_lines():
    """手写模板里的提示语（「今日关键点（Fact）：今天我学到了什么？」这种）：在 4 页以上都出现过的行。"""
    global _TEMPLATE
    if _TEMPLATE is None:
        from collections import Counter
        cnt = Counter()
        for r in _q('SELECT text FROM journal'):
            cnt.update({ln.strip() for ln in (r['text'] or '').split('\n') if ln.strip()})
        _TEMPLATE = {ln for ln, n in cnt.items() if n >= 4}
    return _TEMPLATE


def _quote(text, limit=90):
    """从手写里挑一句当卡片的门面：跳过日期抬头、链接、图片占位、模板提示语，取第一段像样的话。"""
    tpl = _template_lines()
    for line in (text or '').split('\n'):
        if line.strip() in tpl:
            continue
        s = line.strip().lstrip('-•☐☑>▸ ').strip()
        if len(s) < 8 or _SKIP.match(s) or s.rstrip().endswith(('？', '?')) and len(s) < 30:
            continue
        s = re.sub(r'^\d{1,2}月\d{1,2}日[，,\s]*', '', s)
        if len(s) <= limit:
            return s
        cut = max(s.rfind(p, 0, limit) for p in '。！？!?；;')
        return s[:cut + 1] if cut >= 20 else s[:limit] + '…'
    return ''


def card(date):
    d0 = _date.fromisoformat(date)
    ago = (_today() - d0).days
    dy = _q('SELECT headline FROM diaries WHERE date=?', date)
    hand = ''.join(r['text'] or '' for r in _q('SELECT text FROM journal WHERE date=? ORDER BY title', date))
    q = _quote(hand)
    sig = mood.day_signals(date)
    feel = sig.get('mood')

    # 那天在弄的事，以及后来
    threads = []
    chips = ledger.chips(date)
    for c in chips:
        c['after'] = _q("""SELECT COUNT(DISTINCT date) n, MAX(date) last FROM theme_days
                            WHERE theme_id=? AND date > ?""", c['id'], date)[0]
    # 有下文的排前面（「后来」才是这张卡的看点），再按当时已经做了几天
    for c in sorted(chips, key=lambda c: (-(c['after']['n'] > 0), -c['after']['n'], -c['nth']))[:3]:
        after = c['after']
        if not after['n']:
            later = '之后再没出现过'
        else:
            last = _date.fromisoformat(after['last'])
            if (_today() - last).days <= 7:
                later = f"后来又弄了 {after['n']} 天，到现在还在（最近 {last.month}-{last.day}）"
            else:
                when = f'{last.month}-{last.day}' if last.year == _today().year else last.isoformat()
                later = f"后来又弄了 {after['n']} 天，最后一次是 {when}"
        threads.append({'id': c['id'], 'name': c['name'], 'nth': c['nth'], 'note': c.get('note') or '', 'later': later})

    return {
        'date': date, 'ago': ago, 'weekday': '一二三四五六日'[d0.weekday()],
        'quote': q, 'quote_src': 'hand' if q else ('diary' if dy else None),
        'headline': dy[0]['headline'] if dy else '',
        'feel': {'word': feel['label'], 'v': feel['valence'], 'src': feel.get('src')} if feel else None,
        'threads': threads,
        'hand_chars': len(hand),
    }


def get(mode='random'):
    ya = year_ago()
    if mode == 'year' and ya:
        d = ya
    else:
        d = pick_random()
        mode = 'random'
    if not d:
        return {'card': None, 'mode': mode, 'has_year': bool(ya)}
    return {'card': card(d), 'mode': mode, 'has_year': bool(ya), 'year_date': ya}
