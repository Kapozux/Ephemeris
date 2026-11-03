"""屏蔽（10-29 用户：「我能够选哪一天 / 哪些聊天 / Notion 日记不被『问』查找到，完全禁止」）。

三种：某一天（day，那天的一切）、某个窗口（conv）、某篇手写日记（journal，Notion 的 page_id）。只管「问」，日记页照常显示。
连带全挡（用户选的）：屏蔽的窗口 / 手写日记出现过的日子，那几天 Ephemeris 写的日记、情绪打分、主题记录也挡（会转述它）；
碰到屏蔽日子的窗口，窗口小结整段挡（小结跨好几天）；出处卡被挡了的档案条目也挡。

怎么挡：「问」查资料只能跑 look.py，look.py 一开库就调 shield(连接)：在 temp 里建同名视图（messages、cards……）
把要挡的行滤掉，没写前缀的查询先找到 temp 里的视图。它自己写的 SQL（look.py sql）另加授权检查：主库的表只能经这些视图读，
别的表（全文索引、重复窗口存档、备份……）和屏蔽清单本身一律读不到。
「问」进程外还会挑卡的两处（档案整理 memory、没 Claude / DeepSeek 时的兜底 qa.select）用 card_hidden() 滤。
"""
from datetime import datetime

import store

KINDS = ('day', 'conv', 'journal')

# ---- 规则只在这里（规矩 1）。都是对主库（main.）的子查询：在视图里、在主连接上都能用
DAYS = "SELECT ref FROM main.qa_hidden WHERE kind='day'"
PAGES = "SELECT ref FROM main.qa_hidden WHERE kind='journal'"
CONVS = "SELECT ref FROM main.qa_hidden WHERE kind='conv' UNION SELECT 'journal-' || ref FROM main.qa_hidden WHERE kind='journal'"
# 连带的日子：那天的 Ephemeris 日记、情绪打分、主题记录会转述屏蔽的窗口 / 手写日记
DERIVED_DAYS = (f"{DAYS} UNION SELECT date FROM main.messages WHERE conv_id IN ({CONVS}) "
                + f"UNION SELECT date FROM main.journal WHERE page_id IN ({PAGES})")
# 小结整段挡的窗口：屏蔽的 + 碰到屏蔽日子的
SUMMARY_CONVS = f"{CONVS} UNION SELECT conv_id FROM main.messages WHERE date IN ({DAYS})"


def _card(a):
    return f"(COALESCE({a}.date, '') IN ({DAYS}) OR COALESCE({a}.conv_id, '') IN ({CONVS}))"


HIDDEN_CARDS = f"SELECT x.id FROM main.cards x WHERE {_card('x')}"

# 「问」看得到的表 → 留下哪些行（None = 整张都留）。不在这里的表「问」读不到
VIEWS = {
    'messages': f"COALESCE(t.date, '') NOT IN ({DAYS}) AND COALESCE(t.conv_id, '') NOT IN ({CONVS})",
    'conversations': f"t.id NOT IN ({CONVS})",
    'cards': f"NOT {_card('t')}",
    'card_vecs': f"t.id NOT IN ({HIDDEN_CARDS})",
    'card_convs': f"t.conv_id NOT IN ({SUMMARY_CONVS})",
    'conv_notes': (f"t.conv_id NOT IN ({CONVS}) AND t.date NOT IN ({DAYS}) "
                   f"AND NOT (t.date = '' AND t.conv_id IN ({SUMMARY_CONVS}))"),
    'diaries': f"t.date NOT IN ({DERIVED_DAYS})",
    'mood_scores': f"t.date NOT IN ({DERIVED_DAYS})",
    'theme_days': f"t.date NOT IN ({DERIVED_DAYS})",
    'journal': f"t.page_id NOT IN ({PAGES}) AND t.date NOT IN ({DAYS})",
    'checkins': f"t.date NOT IN ({DAYS})",
    'mood_ratings': f"t.date NOT IN ({DAYS})",
    'notes': f"COALESCE(t.date, '') NOT IN ({DAYS})",              # 11-03 Notion 笔记库
    'browse_visits': f"COALESCE(t.date, '') NOT IN ({DAYS})",     # 11-03 用户：「问」要能读浏览记录（日记照样不进）
    'memory_facts': f"NOT EXISTS (SELECT 1 FROM json_each(COALESCE(t.cites, '[]')) j WHERE j.value IN ({HIDDEN_CARDS}))",
    'themes': None, 'theme_cats': None, 'memory_topics': None, 'qa_chat': None, 'qa_threads': None,
}


# ---------------------------------------------------------------- 清单

def items():
    return store.q('SELECT kind, ref, label, added_at FROM qa_hidden ORDER BY added_at DESC')


def _label(kind, ref):
    if kind == 'conv':
        r = store.one('SELECT title FROM conversations WHERE id=?', ref)
        return (r or {}).get('title') or '无标题'
    if kind == 'journal':
        r = store.one('SELECT date, title FROM journal WHERE page_id=?', ref)
        return f"{r['date']} {r['title'] or ''}".strip() if r else ''
    return ''


def set_(kind, ref, on=True):
    if kind not in KINDS or not ref:
        raise ValueError('kind 只能是 day / conv / journal')
    if kind == 'day':
        datetime.strptime(ref, '%Y-%m-%d')
    if on:
        store.run('INSERT OR IGNORE INTO qa_hidden (kind, ref, label, added_at) VALUES (?, ?, ?, ?)',
                  kind, ref, _label(kind, ref), datetime.now().isoformat(timespec='seconds'))
    else:
        store.run('DELETE FROM qa_hidden WHERE kind=? AND ref=?', kind, ref)


def of_day(date):
    """日记页那天要标的：这天屏蔽了没有、屏蔽了的窗口 / 手写日记（页面只用得上这天出现的那几个）。"""
    rows = items()
    return {'day': any(r['kind'] == 'day' and r['ref'] == date for r in rows),
            'convs': [r['ref'] for r in rows if r['kind'] == 'conv'],
            'journal': [r['ref'] for r in rows if r['kind'] == 'journal']}


def card_hidden():
    """被挡的卡 id（「问」进程外挑卡时滤）。"""
    return {r['id'] for r in store.q(HIDDEN_CARDS)}


# ---------------------------------------------------------------- 给「问」的连接套上

MEMORY = ('memory_topics', 'memory_facts')       # 「问」不用记忆的时候（qa.LOOK_NO_MEMORY）：这两张读出来是空的


def shield(db, no_memory=False):
    """在这个连接的 temp 里建同名视图，之后没写前缀的 messages / cards / … 读到的都是滤过的。返回建了视图的表名。"""
    have = {r[0] for r in db.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    made = []
    for name, keep in VIEWS.items():
        if name not in have:
            continue
        if no_memory and name in MEMORY:
            keep = '0'
        db.execute(f'DROP VIEW IF EXISTS temp."{name}"')
        db.execute(f'CREATE TEMP VIEW "{name}" AS SELECT t.rowid AS rowid, t.* FROM main."{name}" t'
                   + (f' WHERE {keep}' if keep else ''))
        made.append(name)
    return made


def unshield(db):
    """撤掉 shield 建的视图（look.main 在同一个进程里被调用时——测试、devtools——跑完要还原，不然别处写 messages 会写到视图上）。"""
    for name in VIEWS:
        db.execute(f'DROP VIEW IF EXISTS temp."{name}"')


def authorize(action, arg1, arg2, dbname, source):
    """look.py sql 的授权：只许读；主库的表只能经 shield 建的视图读（source 是视图名），直接点名主库的表一律不行。"""
    import sqlite3
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, 'SQLITE_RECURSIVE', 33)):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if source in VIEWS or (dbname == 'temp' and arg1 in VIEWS):
            return sqlite3.SQLITE_OK
        if arg1 == 'sqlite_master' or arg1 == 'sqlite_temp_master':
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
