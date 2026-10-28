"""每个有聊天的日子，这个人自己打了几条消息（日均）。对应 Merve 的 mean_daily_screens。"""
from collections import Counter

DESCRIPTION = ('Mean number of messages the participant typed per active day '
               '(days with at least one typed message; pasted text excluded). Analogue of mean_daily_screens.')
KIND = 'behavior'


def compute(person, study):
    out = {}
    for p in person.periods:
        per_day = Counter(m.at.date() for m in p.said())
        if not per_day:
            out[p.number] = (None, [])
            continue
        busiest = max(per_day, key=per_day.get)
        sample = [m for m in p.said() if m.at.date() == busiest][:3]
        out[p.number] = (sum(per_day.values()) / len(per_day),
                         [{'msg': m.id, 'why': f'busiest day ({busiest}, {per_day[busiest]} messages)'} for m in sample])
    return out
