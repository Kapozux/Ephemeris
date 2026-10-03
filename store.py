"""数据库：一个连接、一把锁、所有表长什么样。

    q(sql, *a)    → [dict]
    one(sql, *a)  → dict 或 None
    run(sql, *a)  → 执行并提交，返回 lastrowid
    tx()          → with store.tx() as db: 几条写一起提交，出错就回滚
    connect()     → 底下那个共享连接（批量导入这种要自己控制提交的才用；用前先拿 LOCK）

整个应用（所有线程）共用一个 sqlite 连接，所有读写走 LOCK 这一把锁。
之前 app 和 reflect_chat 各用各的锁，导入和回顾重算同时写 → 'database disk image is malformed'（踩过）。

为什么要有它（2026-10-02 体检）：以前 9 个模块各抄一份 _q()，12 个 ensure_table() 散在 14 个文件里，
而且 connect() 每调一次就新开一个连接、把建表语句整个跑一遍。想知道库长什么样得翻 14 个文件。
现在：表结构全在下面的 SCHEMA，老库补列 / 改表在 _migrate()，第一次连接时跑一次。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DIARY_DB') or os.path.join(HERE, 'chats.db')
LOCK = threading.RLock()
_conn = None

SCHEMA = """
-- ---------------------------------------------------------------- 聊天（index.py 导入）
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, source TEXT, title TEXT, title_fallback INTEGER DEFAULT 0,
  first_ts TEXT, last_ts TEXT, n_msgs INTEGER, chars INTEGER,
  n_days INTEGER, first_import TEXT, last_import TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conv_id TEXT, source TEXT,
  ts TEXT, date TEXT, hhmm TEXT, role TEXT, text TEXT, chars INTEGER
);
CREATE INDEX IF NOT EXISTS idx_msg_date ON messages(date);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id);
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(
  text, conv_title, date UNINDEXED, msg_id UNINDEXED, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT, kind TEXT, source TEXT,
  imported_at TEXT, n_convs INTEGER, n_convs_new INTEGER, n_msgs INTEGER, n_msgs_new INTEGER,
  days_touched INTEGER, diaries_invalidated INTEGER, note TEXT
);
-- claude.ai 同步（claude_sync.py）：每个窗口上次拉到的版本
CREATE TABLE IF NOT EXISTS claude_sync (
  uuid TEXT PRIMARY KEY, updated_at TEXT NOT NULL, synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, ok INTEGER,
  n_listed INTEGER, n_fetched INTEGER, n_msgs_new INTEGER, days_touched INTEGER, note TEXT
);

-- ---------------------------------------------------------------- AI 写的
CREATE TABLE IF NOT EXISTS diaries (
  date TEXT PRIMARY KEY, headline TEXT, narrative TEXT,
  topics TEXT, artifacts TEXT, open_loops TEXT, mood TEXT,
  model TEXT, generated_at TEXT
);
-- 按窗口的小结（conv_review.py）。date='' 是整个窗口的走向。
-- fp：写这条时这个窗口这一天我发了几条、多少字；之后又聊了对不上 → 只重写这一天
CREATE TABLE IF NOT EXISTS conv_notes (
  conv_id TEXT NOT NULL, date TEXT NOT NULL, text TEXT NOT NULL, generated_at TEXT NOT NULL, fp TEXT,
  PRIMARY KEY (conv_id, date)
);
-- 回顾面板（reflect_chat.py），一个时间范围一行
CREATE TABLE IF NOT EXISTS reflect (
  range_key TEXT PRIMARY KEY, fingerprint TEXT, headline TEXT, narrative TEXT,
  topics_json TEXT, generated_at TEXT
);

-- ---------------------------------------------------------------- 主题账本（ledger.py / river.py）
-- desc：手动合并过的主题用人写的一句话当身份锚（起头那天的 note 不一定代表整条）
CREATE TABLE IF NOT EXISTS themes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_on TEXT, desc TEXT
);
-- src：ai（从聊天推出的日记）| hand（Notion 手写）。同一主题同一天两边都写到就是两行；「第几天」按不同日期数
CREATE TABLE IF NOT EXISTS theme_days (
  theme_id INTEGER NOT NULL, date TEXT NOT NULL, note TEXT, src TEXT NOT NULL DEFAULT 'ai',
  PRIMARY KEY (theme_id, date, src)
);
CREATE INDEX IF NOT EXISTS theme_days_date ON theme_days(date);
CREATE TABLE IF NOT EXISTS theme_cats (theme_id INTEGER PRIMARY KEY, cat TEXT NOT NULL);

-- ---------------------------------------------------------------- 我自己说的
-- Notion 手写日记（journal.py）。text 是 Markdown（图片指向 journal_files/），chars 按纯文字数；
-- stripped：去掉了几段 Ephemeris 追加进去的内容；digested：上次记进主题账本时正文的指纹（journal.digest）
CREATE TABLE IF NOT EXISTS journal (
  page_id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT, text TEXT, chars INTEGER,
  stripped INTEGER DEFAULT 0, last_edited TEXT, fetched_at TEXT, digested TEXT
);
CREATE INDEX IF NOT EXISTS journal_date ON journal(date);
CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
  text, date UNINDEXED, page_id UNINDEXED, tokenize='unicode61'
);
-- 打卡（checkin.py）。reply：记下之后 AI 回的那几句
CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, word TEXT NOT NULL, valence REAL NOT NULL,
  note TEXT DEFAULT '', created_at TEXT, updated_at TEXT, reply TEXT
);
CREATE INDEX IF NOT EXISTS checkins_date ON checkins(date);
CREATE TABLE IF NOT EXISTS star_notes (month TEXT PRIMARY KEY, fingerprint TEXT, text TEXT, made_at TEXT);

-- ---------------------------------------------------------------- 心情分（mood.py）
-- src：hand（手写日记读出的「过得怎么样」，-2..+2）| friction（聊天里「卡得多狠」，0..4）| 实验留下的 ai / both
CREATE TABLE IF NOT EXISTS mood_scores (
  date TEXT NOT NULL, src TEXT NOT NULL, valence REAL, label TEXT, evidence TEXT, scored_at TEXT,
  PRIMARY KEY (date, src)
);
CREATE TABLE IF NOT EXISTS mood_ratings (date TEXT PRIMARY KEY, valence REAL, note TEXT, rated_at TEXT);
-- 情绪实验（mood_lab.py）
CREATE TABLE IF NOT EXISTS diaries_lab (
  date TEXT PRIMARY KEY, headline TEXT, narrative TEXT, feelings TEXT, mood TEXT, raw TEXT, made_at TEXT
);
CREATE TABLE IF NOT EXISTS mood_lab2_pick (date TEXT PRIMARY KEY);

-- ---------------------------------------------------------------- 杂项记账
CREATE TABLE IF NOT EXISTS notion_pages (       -- to_notion.py：推到 Notion 的那版
  date TEXT PRIMARY KEY, page_id TEXT NOT NULL,
  mode TEXT NOT NULL,                           -- create | append
  hash TEXT NOT NULL, pushed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flashback_seen (date TEXT, shown_at TEXT);
CREATE TABLE IF NOT EXISTS daily_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, steps TEXT
);
CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, ok INTEGER,
  week TEXT, dest TEXT, snapshot TEXT, size INTEGER, weeks_md INTEGER, imports_new INTEGER, note TEXT,
  files_written INTEGER, files_total INTEGER, bytes_written INTEGER
);
-- 每个备份文件上次写进去的内容指纹：没变就不写（也就不重传）。
-- 记在本地而不是去读 Drive 里的文件 —— Drive 的「仅在线」文件一读就会触发下载。
CREATE TABLE IF NOT EXISTS backup_files (path TEXT PRIMARY KEY, sha1 TEXT, size INTEGER, written_at TEXT);
"""

# 老库补列：(表, 列, 类型)。新库的 SCHEMA 里已经有了，这里只对老库起作用。
_ADD_COLUMNS = [
    ('conversations', 'title_fallback', 'INTEGER DEFAULT 0'), ('conversations', 'n_days', 'INTEGER'),
    ('conversations', 'first_import', 'TEXT'), ('conversations', 'last_import', 'TEXT'),
    ('themes', 'desc', 'TEXT'), ('checkins', 'reply', 'TEXT'),
    ('backups', 'files_written', 'INTEGER'), ('backups', 'files_total', 'INTEGER'),
    ('backups', 'bytes_written', 'INTEGER'),
]


def _cols(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def _migrate(db):
    for table, col, typ in _ADD_COLUMNS:
        if col not in _cols(db, table):
            db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
    # theme_days 加 src 进主键（2026-09-27）：主键变了只能重建表
    if 'src' not in _cols(db, 'theme_days'):
        db.executescript("""
        CREATE TABLE theme_days_new (
          theme_id INTEGER NOT NULL, date TEXT NOT NULL, note TEXT, src TEXT NOT NULL DEFAULT 'ai',
          PRIMARY KEY (theme_id, date, src));
        INSERT INTO theme_days_new SELECT theme_id, date, note, 'ai' FROM theme_days;
        DROP TABLE theme_days;
        ALTER TABLE theme_days_new RENAME TO theme_days;
        CREATE INDEX IF NOT EXISTS theme_days_date ON theme_days(date);
        """)
    # conv_notes 加 fp：老小结按现在的内容补上，当它是新的，免得一升级就把所有小结重写一遍
    if 'fp' not in _cols(db, 'conv_notes'):
        db.execute('ALTER TABLE conv_notes ADD COLUMN fp TEXT')
        db.execute("""UPDATE conv_notes SET fp = (
                        SELECT COUNT(*) || ':' || COALESCE(SUM(chars), 0) FROM messages m
                         WHERE m.conv_id = conv_notes.conv_id AND m.date = conv_notes.date AND m.role='human')
                      WHERE date <> ''""")
    # journal 加 digested（2026-10-03）：以前的手写都已经记进账本了，按现在的正文记一笔，免得一升级全部重抽一遍
    if 'digested' not in _cols(db, 'journal'):
        import journal                                # 指纹的算法在 journal 里；这里只跑这一次
        db.execute('ALTER TABLE journal ADD COLUMN digested TEXT')
        for pid, text in db.execute('SELECT page_id, text FROM journal').fetchall():
            db.execute('UPDATE journal SET digested=? WHERE page_id=?', (journal.digest(text or ''), pid))
    db.commit()


def connect():
    global _conn
    with LOCK:
        if _conn is None:
            db = sqlite3.connect(DB_PATH, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)     # 都是 IF NOT EXISTS：老库里已有的表不动，缺的补上
            _migrate(db)                 # 老表补列 / 改表
            _conn = db
        return _conn


def q(sql, *a):
    with LOCK:
        return [dict(r) for r in connect().execute(sql, a).fetchall()]


def one(sql, *a):
    rows = q(sql, *a)
    return rows[0] if rows else None


def run(sql, *a):
    with LOCK:
        db = connect()
        cur = db.execute(sql, a)
        db.commit()
        return cur.lastrowid


@contextmanager
def tx():
    with LOCK:
        db = connect()
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        db.commit()
