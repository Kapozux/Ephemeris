"""Cursor 的对话也进 Ephemeris（2026-10-14 用户：「Cursor 的你能看到吗」）：来源 cursor，一段对话（composer）一个窗口。

    sync()      读 Cursor 的全局数据库，新消息进 messages（只增不删，规矩 6）

在哪：~/Library/Application Support/Cursor/User/globalStorage/state.vscdb 的 cursorDiskKV 表——
composerData:<对话 id> 是一段对话（名字、开始时间、按顺序的气泡 id），bubbleId:<对话 id>:<气泡 id> 是一条（type 1 = 他、2 = AI，
text、createdAt）。~/.cursor/projects/*/agent-transcripts/ 是同一批对话的副本，没有时间，不读。
Cursor 开着的时候也能读：用 SQLite 的只读备份先拷一份到临时文件再读，不碰它的库。
存的：他打的字、AI 回的文字。不存：只有工具调用、没有文字的气泡。
跟 Claude Code 一样算「写代码」：日记材料里写代码的加起来只占 10%（slice.SOURCE_SHARE['cc']，用户 10-14：「所有 Coding 加起来」），
做证据卡只看他说的（cards._CARDABLE）。
"""
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import days
import index as idx
import store

SOURCE = 'cursor'
DB = os.path.expanduser(os.environ.get('EPHEMERIS_CURSOR_DB')
                        or '~/Library/Application Support/Cursor/User/globalStorage/state.vscdb')


def _snapshot():
    """只读打开 Cursor 的库，备份到临时文件（Cursor 开着也行，WAL 里的也带上）。返回临时文件路径。"""
    tmp = tempfile.NamedTemporaryFile(prefix='ephemeris-cursor-', suffix='.db', delete=False).name
    src = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return tmp


def _read(path):
    """→ [(对话 id, 名字, 开始时间, [(气泡 id, 时间 / None, role, 文字)])]，只要有气泡的对话。"""
    db = sqlite3.connect(path)
    try:
        bubbles = {}
        for k, v in db.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"):
            try:
                _, cid, bid = k.split(':', 2)
                bubbles[(cid, bid)] = json.loads(v)
            except (ValueError, TypeError):
                continue
        out = []
        for _, v in db.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"):
            try:
                d = json.loads(v)
            except (ValueError, TypeError):
                continue
            heads = d.get('fullConversationHeadersOnly') or []
            if not heads or not d.get('composerId'):
                continue
            cid, msgs = d['composerId'], []
            for h in heads:
                b = bubbles.get((cid, h.get('bubbleId'))) or {}
                text = (b.get('text') or '').strip()
                role = {1: 'human', 2: 'assistant'}.get(b.get('type', h.get('type')))
                if text and role:
                    msgs.append((h['bubbleId'], b.get('createdAt'), role, text))
            start = datetime.fromtimestamp(int(d.get('createdAt') or 0) / 1000, timezone.utc)
            out.append((cid, d.get('name') or '', start, msgs))
        return out
    finally:
        db.close()


def sync(say=print):
    """库改过才读。返回 {'files': 读了几次库（0 / 1）, 'new': 新消息条数, 'days': 涉及哪些天}。"""
    if not os.path.exists(DB):
        return {'files': 0, 'new': 0, 'days': []}
    st = os.stat(DB)
    wal = DB + '-wal'
    size = st.st_size + (os.path.getsize(wal) if os.path.exists(wal) else 0)
    mtime = int(max(st.st_mtime, os.path.getmtime(wal) if os.path.exists(wal) else 0))
    row = store.one('SELECT size, mtime FROM cc_files WHERE path=?', DB)          # 跟 Claude Code 共用「读过哪些文件」那张表
    if row and (row['size'], row['mtime']) == (size, mtime):
        return {'files': 0, 'new': 0, 'days': []}
    tmp = _snapshot()
    try:
        convs = _read(tmp)
    finally:
        os.remove(tmp)
    new, touched = 0, set()
    with store.tx() as db:
        for cid, name, start, msgs in convs:
            conv = f'{SOURCE}-{cid}'
            first = next((t for _, _, role, t in msgs if role == 'human'), '')
            if msgs:
                db.execute("""INSERT INTO conversations (id, source, title, title_fallback, first_import, last_import)
                              VALUES (?,?,?,0,'cursor','cursor') ON CONFLICT(id) DO NOTHING""",
                           (conv, SOURCE, f"Cursor·{name or ' '.join(first.split())[:40] or '（没有文字）'}"))
            for bid, ts, role, text in msgs:
                when = datetime.fromisoformat(ts.replace('Z', '+00:00')) if ts else start
                date, hhmm = days.bucket(when)
                # 不用 INSERT OR REPLACE：rowid 会变，全文索引对不上
                cur = db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                                 (f'{SOURCE}-{bid}', conv, SOURCE, when.isoformat(), date, hhmm, role, text, len(text)))
                if cur.rowcount:
                    new += 1
                    if role == 'human':
                        touched.add(date)
        db.execute('INSERT OR REPLACE INTO cc_files (path, size, mtime, synced_at) VALUES (?,?,?,?)',
                   (DB, size, mtime, datetime.now().isoformat(timespec='seconds')))
        if new:
            idx._recount(db)
    if say:
        say(f'[cursor] {len(convs)} 段对话，新消息 {new} 条，涉及 {len(touched)} 天')
    return {'files': 1, 'new': new, 'days': sorted(touched)}


if __name__ == '__main__':
    print(sync())
