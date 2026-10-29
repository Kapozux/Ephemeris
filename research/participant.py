"""读一个参与者：库里的消息（只读打开）+ 量表日期 → 按期切好。指标插件只看这里给的东西。

一期 = 上一次量表到这一次量表之间（跟 Merve 的代码一样：period t 的特征来自 survey t 之前那段）。
时间一律换成参与者当地时间（tz 在研究方案的参与者表里，演示版来自 personas）。
"""
import csv
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import speech
from research import DATA, consent, db_path


@dataclass
class Msg:
    id: str
    conv: str
    source: str
    at: datetime          # 参与者当地时间
    role: str             # 'human' / 'assistant'
    text: str
    typed: bool           # 自己打的（不是贴进来的）；只对 human 有意义
    model: str = None     # 写这条回复的模型（来源记了才有，见 store 的 msg_models）


@dataclass
class Period:
    number: int           # 1 起；第 number 次量表结束这一期
    start: datetime
    end: datetime
    score: float
    msgs: list = field(default_factory=list)

    @property
    def days(self):
        return (self.end.date() - self.start.date()).days

    def said(self):
        """这一期这个人自己打的话（研究方案 speaker=human、exclude_pasted）。"""
        return [m for m in self.msgs if m.role == 'human' and m.typed]

    def active_days(self):
        return sorted({m.at.date() for m in self.said()})


@dataclass
class Participant:
    pid: str
    tz: str
    periods: list
    convs: dict           # conv_id -> 按时间排好的 [Msg]


def surveys(pid, path=None):
    with open(path or os.path.join(DATA, 'cesd.csv'), newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['pid'] == pid]
    return sorted((int(r['survey_number']), date.fromisoformat(r['survey_date']), float(r['cesd_score'])) for r in rows)


def load(pid, tz, sources=None):
    zone = ZoneInfo(tz)
    db = sqlite3.connect(f'file:{db_path(pid)}?mode=ro', uri=True)
    has_models = db.execute("SELECT 1 FROM sqlite_master WHERE name='msg_models'").fetchone()
    sql = ('SELECT m.id, m.conv_id, m.source, m.ts, m.role, m.text, ' + ('x.model' if has_models else 'NULL') +
           ' FROM messages m' + (' LEFT JOIN msg_models x ON x.msg_id = m.id' if has_models else '') +
           ' WHERE m.text IS NOT NULL')
    args = ()
    if sources:
        sql += f" AND m.source IN ({','.join('?' * len(sources))})"
        args = tuple(sources)
    msgs = [Msg(i, c, s, datetime.fromisoformat(ts).astimezone(zone), r, t, speech.typed(t), model)
            for i, c, s, ts, r, t, model in db.execute(sql + ' ORDER BY m.ts, m.rowid', args)]
    db.close()
    skip = set(consent.get(pid)['excluded_convs'])          # 参与者说不交的窗口：从所有指标里拿掉
    msgs = [m for m in msgs if m.conv not in skip]
    convs = {}
    for m in msgs:
        convs.setdefault(m.conv, []).append(m)
    sv = surveys(pid)
    periods = []
    for (_, d0, _), (n, d1, score) in zip(sv, sv[1:]):
        p = Period(n, datetime.combine(d0, time(), zone), datetime.combine(d1, time(), zone), score)
        p.msgs = [m for m in msgs if p.start <= m.at < p.end]
        periods.append(p)
    return Participant(pid, tz, periods, convs)


def sessions(msgs, gap_minutes=30):
    """按时间排好的消息切成会话：跟上一条隔了超过 gap_minutes 分钟就算新的一段（De Freitas 的做法），不管换没换平台 / 窗口。"""
    out, gap = [], timedelta(minutes=gap_minutes)
    for m in sorted(msgs, key=lambda m: m.at):
        if not out or m.at - out[-1][-1].at > gap:
            out.append([])
        out[-1].append(m)
    return out
