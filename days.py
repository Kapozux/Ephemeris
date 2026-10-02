"""「一天」的规则只在这里。

    today()                → 现在算哪天（凌晨 4 点前还算前一天）
    bucket(dt)             → (日期, 'HH:MM')：一条消息归到哪天
    feel(date)             → 那天过得怎么样，没有记录返回 None
    feel_range(start, end) → {日期: 同上}，两头都含

「过得怎么样」：
- 打卡（自己明确说的）优先；没打卡用手写日记打的分（模型读出来的）。
- 一天打了好几次卡：分数取平均；word 取最后一条（最新的心情）；
  words 按时间串成「崩溃→满足」，连着重复的词只算一次 —— 一天里的起伏也看得见。

一条 feel：{date, v, src: 'checkin'|'hand', word, words, n, note}

为什么要有它（2026-10-02 体检）：这两条规则以前散在 5 个地方，取平均 / 取第一个词 / 取最后一个词 /
按顺序串，各不相同 —— 日历、星座图、回顾面板、闪回卡、打卡回话看到的「那天」不一样。
"""
import os
from datetime import datetime, timedelta, timezone
import store

# Claude API 给的是 UTC，按天分桶必须先落到你所在的时区，否则晚上 8 点以后的消息会算进第二天。
LOCAL_TZ = timezone(timedelta(hours=8))          # Asia/Shanghai
# 一天的分界：4 表示凌晨 4 点前算前一天 —— 熬夜聊的东西归到前一天的日记里。
DAY_CUTOFF_HOUR = int(os.environ.get('DIARY_DAY_CUTOFF', '4'))


def _shift(local):
    return local - timedelta(days=1) if local.hour < DAY_CUTOFF_HOUR else local


def bucket(dt):
    """带时区的时间 → (日期, 'HH:MM')。时刻是本地钟面时间，不跟着日期往回挪。"""
    local = dt.astimezone(LOCAL_TZ)
    return _shift(local).date().isoformat(), local.strftime('%H:%M')


def today(now=None):
    return bucket(now or datetime.now(LOCAL_TZ))[0]


# ------------------------------------------------------------------ 过得怎么样

def _from_checkins(date, rows):
    words = []
    for r in rows:
        if not words or words[-1] != r['word']:
            words.append(r['word'])
    return {'date': date, 'v': round(sum(r['valence'] for r in rows) / len(rows), 2), 'src': 'checkin',
            'word': rows[-1]['word'], 'words': '→'.join(words), 'n': len(rows),
            'note': '；'.join(r['note'] for r in rows if r['note'])}


def _from_hand(r):
    return {'date': r['date'], 'v': r['valence'], 'src': 'hand', 'word': r['label'] or '',
            'words': r['label'] or '', 'n': 1, 'note': r['evidence'] or ''}


def feel_range(start='0000-00-00', end='9999-99-99'):
    with store.LOCK:
        db = store.connect()
        ck = {}
        for r in db.execute('SELECT date, word, valence, note FROM checkins WHERE date BETWEEN ? AND ? '
                            'ORDER BY created_at', (start, end)):
            ck.setdefault(r['date'], []).append(r)
        hand = {r['date']: r for r in db.execute(
            "SELECT date, valence, label, evidence FROM mood_scores WHERE src='hand' AND date BETWEEN ? AND ?",
            (start, end))}
    return {d: _from_checkins(d, ck[d]) if d in ck else _from_hand(hand[d]) for d in sorted(set(ck) | set(hand))}


def feel(date):
    return feel_range(date, date).get(date)
