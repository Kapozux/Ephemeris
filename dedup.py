"""同一个窗口存了两份（2026-10-13）：手动导出的文件没有 claude.ai 的窗口编号，index.ingest 只能用内容算指纹当 id；
后来自动同步按真编号（uuid）又进了一份。两份内容一样，窗口页、搜索、日历里就成对出现，证据卡也做了两遍。

    find(conn)                → [(多出来的那份, 留下的那份, 包含比例)]，以及拿不准的（两份都是同步来的）
    archive(conn, dup, keep)  → 多出来的那份整份挪进 conversations_dup / messages_dup（JSON 原样存），派生的卡和小结删掉
    restore(conn, dup)        → 挪回来（判断错了用）
    sweep(conn, ids=None)     → 找 + 挪；ids 给了只看这几个窗口（index.ingest 每次导入后调）
    archived(conn)            → 已经挪走的窗口 id（导入时跳过，免得重新导一次又冒出来）

规矩 6「只加不删」：挪走的消息还在库里（存档表）、也照常进备份，只是正常的表里看不到。
算重复的标准：第一句一样，而且要挪走的那份至少 MIN_SHARE 的消息原文在留下那份里也有。
留下的优先是有真编号的那份（同步来的，时间精确到毫秒，之后还会接着同步）；两份都有真编号的不动（可能是你自己开了两个窗口发同一句开头）。
"""
import json
import re
from datetime import datetime

MIN_SHARE = 0.9
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _key(text):
    """比对用：去掉空白、标点、Markdown 记号和 <br>，取前 80 个字。
    手动导出的文件会在每条 AI 回复后面拼一串「--- <br> === <br>」，逐字比整条就全对不上（10-13 实测只认出 25 个）。"""
    t = re.sub(r'<br\s*/?>', '', text or '', flags=re.I)
    return re.sub(r'[\W_]+', '', t)[:80]


def _real_id(cid):
    return bool(_UUID.match(cid or ''))


def _keys(conn, cid):
    return [_key(t) for (t,) in conn.execute('SELECT text FROM messages WHERE conv_id=? ORDER BY ts, id', (cid,))]


def _share(a, b):
    """a 的消息有多少比例在 b 里也有。"""
    if not a:
        return 0.0
    bs = set(b)
    return sum(1 for k in a if k in bs) / len(a)


def find(conn, ids=None):
    """→ (sure, unsure)。sure：[(dup, keep, 比例)]；unsure：两份都有真编号、内容也几乎一样的 [(a, b, 比例)]。"""
    first = {}
    for cid, src, text in conn.execute("""SELECT m.conv_id, m.source, m.text FROM messages m
                                           WHERE m.role='human' ORDER BY m.conv_id, m.ts, m.id"""):
        first.setdefault(cid, (src, _key(text)))
    groups = {}
    for cid, k in first.items():
        groups.setdefault(k, []).append(cid)
    sure, unsure, cache = [], [], {}
    keys = lambda c: cache.setdefault(c, _keys(conn, c))
    for members in groups.values():
        if len(members) < 2 or (ids is not None and not set(members) & set(ids)):
            continue
        gone = set()
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if a in gone or b in gone:
                    continue
                if _real_id(a) and _real_id(b):
                    s = min(_share(keys(a), keys(b)), _share(keys(b), keys(a)))
                    if s >= MIN_SHARE:
                        unsure.append((a, b, round(s, 3)))
                    continue
                # 先试着挪走没真编号的那份；它不在对方里面，再看反过来行不行
                dup, keep = (b, a) if _real_id(a) or (not _real_id(b) and len(keys(a)) >= len(keys(b))) else (a, b)
                s = _share(keys(dup), keys(keep))
                if s < MIN_SHARE and not _real_id(dup):
                    dup, keep = keep, dup
                    s = _share(keys(dup), keys(keep))
                if s >= MIN_SHARE and not _real_id(dup):
                    sure.append((dup, keep, round(s, 3)))
                    gone.add(dup)
    return sure, unsure


def archive(conn, dup, keep):
    now = datetime.now().isoformat(timespec='seconds')
    cols = [r[1] for r in conn.execute('PRAGMA table_info(conversations)')]
    row = conn.execute('SELECT * FROM conversations WHERE id=?', (dup,)).fetchone()
    if not row:
        return 0
    conn.execute('INSERT OR REPLACE INTO conversations_dup (id, dup_of, archived_at, row) VALUES (?,?,?,?)',
                 (dup, keep, now, json.dumps(dict(zip(cols, row)), ensure_ascii=False)))
    mcols = [r[1] for r in conn.execute('PRAGMA table_info(messages)')]
    msgs = conn.execute('SELECT * FROM messages WHERE conv_id=?', (dup,)).fetchall()
    conn.executemany('INSERT OR REPLACE INTO messages_dup (id, conv_id, row) VALUES (?,?,?)',
                     [(m[0], dup, json.dumps(dict(zip(mcols, m)), ensure_ascii=False)) for m in msgs])
    conn.execute('DELETE FROM messages WHERE conv_id=?', (dup,))          # 触发器顺手把全文索引里的也删了
    conn.execute('DELETE FROM conversations WHERE id=?', (dup,))
    # 派生出来的：留着就会在「问」和窗口小结里成对出现
    conn.execute('DELETE FROM card_vecs WHERE id IN (SELECT id FROM cards WHERE conv_id=?)', (dup,))
    for t in ('cards', 'card_convs', 'conv_notes'):
        conn.execute(f'DELETE FROM {t} WHERE conv_id=?', (dup,))
    return len(msgs)


def restore(conn, dup):
    """挪回来。卡和小结不回来（下次做卡会重做）。"""
    r = conn.execute('SELECT row FROM conversations_dup WHERE id=?', (dup,)).fetchone()
    if not r:
        return 0
    c = json.loads(r[0])
    conn.execute(f"INSERT OR IGNORE INTO conversations ({','.join(c)}) VALUES ({','.join('?' * len(c))})", list(c.values()))
    n = 0
    for (row,) in conn.execute('SELECT row FROM messages_dup WHERE conv_id=?', (dup,)).fetchall():
        m = json.loads(row)
        conn.execute(f"INSERT OR IGNORE INTO messages ({','.join(m)}) VALUES ({','.join('?' * len(m))})", list(m.values()))
        n += 1
    conn.execute('DELETE FROM messages_dup WHERE conv_id=?', (dup,))
    conn.execute('DELETE FROM conversations_dup WHERE id=?', (dup,))
    return n


def archived(conn):
    return {r[0] for r in conn.execute('SELECT id FROM conversations_dup')}


def sweep(conn, ids=None):
    """找 + 挪。返回 {'archived': [(dup, keep, 比例, 条数)], 'unsure': [...]}；不 commit（调用方的事务）。"""
    sure, unsure = find(conn, ids)
    done = [(d, k, s, archive(conn, d, k)) for d, k, s in sure]
    return {'archived': done, 'unsure': unsure}
