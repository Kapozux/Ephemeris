"""深夜占比：每个有聊天的日子里，0–6 点（当地时间）打的消息占当天的比例，再按天平均。

跟 Merve 的 mean_daily_overnight_ratio 同一个算法（她是截图，这里是这个人自己打的消息）。
钟点在研究方案的 overnight_hours 里改。
"""
from collections import defaultdict

DESCRIPTION = ('Per active day, the share of the participant\'s typed messages sent between 00:00 and 06:00 local '
               'time; averaged over active days. Same construction as mean_daily_overnight_ratio.')
KIND = 'behavior'


def compute(person, study):
    lo, hi = study.get('overnight_hours', [0, 6])
    out = {}
    for p in person.periods:
        days = defaultdict(list)
        for m in p.said():
            days[m.at.date()].append(lo <= m.at.hour < hi)
        if not days:
            out[p.number] = (None, [])
            continue
        night, seen = [], set()               # 每个窗口取一条，免得五条证据都是同一段对话
        for m in p.said():
            if lo <= m.at.hour < hi and m.conv not in seen:
                seen.add(m.conv)
                night.append(m)
        out[p.number] = (sum(sum(v) / len(v) for v in days.values()) / len(days),
                         [{'msg': m.id, 'why': f'sent at {m.at:%H:%M} local time'} for m in night[:5]])
    return out
