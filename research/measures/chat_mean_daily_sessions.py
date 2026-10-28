"""会话数：这个人自己打的消息，隔了超过 30 分钟（研究方案的 session_gap_minutes）就算新的一段；每个有聊天的日子
开了几段，按天平均。不管换没换平台 / 窗口。对应 Merve「碎片化」那一组的每日会话数。"""
from collections import Counter

from research.participant import sessions

DESCRIPTION = ('Mean number of sessions per active day. A session is a run of the participant\'s typed messages with '
               'no gap longer than 30 minutes (session_gap_minutes), across platforms and windows; counted on the day '
               'it starts. Analogue of the daily session count in Screenome fragmentation features.')
KIND = 'behavior'


def compute(person, study):
    gap = study.get('session_gap_minutes', 30)
    out = {}
    for p in person.periods:
        ss = sessions(p.said(), gap)
        if not ss:
            out[p.number] = (None, [])
            continue
        per_day = Counter(s[0].at.date() for s in ss)
        busiest = max(per_day, key=per_day.get)
        firsts = [s[0] for s in ss if s[0].at.date() == busiest]
        out[p.number] = (len(ss) / len(per_day),
                         [{'msg': m.id, 'why': f'starts a session on {busiest} ({per_day[busiest]} sessions that day)'}
                          for m in firsts[:5]])
    return out
