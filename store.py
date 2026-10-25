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
# 演示版（10-25，给 Reddit 截图）：EPHEMERIS_DEMO=1 且库不是真的 chats.db 才算。页面放行、不弹锁、角上标 DEMO，
# 不跑调度 / 同步 / 备份。设在真库上不起作用。
DEMO = os.environ.get('EPHEMERIS_DEMO') == '1' and os.path.abspath(DB_PATH) != os.path.join(HERE, 'chats.db')
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
-- 全文索引（search.py）：trigram 分词，中文子串也搜得到（2026-10-07 借 Verbatim 的；以前 unicode61 一个中文词都搜不到）。
-- 不另存一份正文（content=messages），也不记位置（detail=none）：片段在 Python 里切。
-- 由下面三个触发器跟着 messages 走：不管谁写消息（导入、claude.ai 同步、还原），索引都是新的。
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(
  text, content='messages', content_rowid='rowid', tokenize='trigram', detail=none
);
CREATE TRIGGER IF NOT EXISTS msg_fts_ai AFTER INSERT ON messages BEGIN
  INSERT INTO msg_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS msg_fts_ad AFTER DELETE ON messages BEGIN
  INSERT INTO msg_fts(msg_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS msg_fts_au AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO msg_fts(msg_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO msg_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT, kind TEXT, source TEXT,
  imported_at TEXT, n_convs INTEGER, n_convs_new INTEGER, n_msgs INTEGER, n_msgs_new INTEGER,
  days_touched INTEGER, diaries_invalidated INTEGER, note TEXT
);
-- claude.ai 同步（claude_sync.py）：每个窗口上次拉到的版本
CREATE TABLE IF NOT EXISTS claude_sync (
  uuid TEXT PRIMARY KEY, updated_at TEXT NOT NULL, synced_at TEXT NOT NULL
);
-- ChatGPT 同步（chatgpt_sync.py，2026-10-14）：每个对话上次拉到的 update_time（id 不带 chatgpt- 前缀）
CREATE TABLE IF NOT EXISTS chatgpt_sync (
  id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, ok INTEGER,
  n_listed INTEGER, n_fetched INTEGER, n_msgs_new INTEGER, days_touched INTEGER, note TEXT
);

-- ---------------------------------------------------------------- AI 写的
-- added_later：这篇写好之后，又导入了这天的几条我的消息（导入旧聊天时不重写旧日记，只记数）
CREATE TABLE IF NOT EXISTS diaries (
  date TEXT PRIMARY KEY, headline TEXT, narrative TEXT,
  topics TEXT, artifacts TEXT, open_loops TEXT, mood TEXT,
  model TEXT, generated_at TEXT, added_later INTEGER DEFAULT 0
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
-- stripped：去掉了几段 Ephemeris 追加进去的内容；digested：上次记进主题账本时正文的指纹（journal.digest）；
-- gone_at：Notion 里这页没了的时间（本地照样留着，这里是备份）
CREATE TABLE IF NOT EXISTS journal (
  page_id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT, text TEXT, chars INTEGER,
  stripped INTEGER DEFAULT 0, last_edited TEXT, fetched_at TEXT, digested TEXT, gone_at TEXT
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

-- ---------------------------------------------------------------- 证据卡 + 问答（cards.py / qa.py，2026-10-11）
-- 一张卡 = 某条消息里的一个要点，带逐字原话（quote 必须在那条消息里找得到，找不到的卡不收）。
-- id = 「消息 id·序号」；who：me | ai；topic：话题（跨窗口串起来用）。10-13 起按窗口做：
-- card_convs 记每个窗口做到哪条（upto / upto_id）、做过那段的指纹（变了整窗重做）、这个窗口的小结、谁做的（api | claude）。
-- card_days 是 10-13 之前按天做时的记账，不再写；cards_v1 是那时的卡，留着
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, conv_id TEXT, msg_id TEXT, who TEXT, kind TEXT,
  text TEXT, quote TEXT, made_at TEXT, topic TEXT
);
CREATE INDEX IF NOT EXISTS cards_conv ON cards(conv_id);
CREATE INDEX IF NOT EXISTS cards_msg ON cards(msg_id);
CREATE TABLE IF NOT EXISTS card_convs (
  conv_id TEXT PRIMARY KEY, fp TEXT, upto TEXT, upto_id TEXT, n INTEGER, dropped INTEGER, cost REAL,
  summary TEXT, src TEXT, made_at TEXT
);
-- 卡的向量（qa 按意思找；Gemini embedding）。h：算向量时卡内容的指纹，卡重做了对不上就重算
CREATE TABLE IF NOT EXISTS card_vecs (id TEXT PRIMARY KEY, h TEXT, model TEXT, vec BLOB);
CREATE INDEX IF NOT EXISTS cards_date ON cards(date);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
  text, quote, content='cards', content_rowid='rowid', tokenize='trigram', detail=none
);
CREATE TRIGGER IF NOT EXISTS cards_fts_ai AFTER INSERT ON cards BEGIN
  INSERT INTO cards_fts(rowid, text, quote) VALUES (new.rowid, new.text, new.quote);
END;
CREATE TRIGGER IF NOT EXISTS cards_fts_ad AFTER DELETE ON cards BEGIN
  INSERT INTO cards_fts(cards_fts, rowid, text, quote) VALUES ('delete', old.rowid, old.text, old.quote);
END;
CREATE TABLE IF NOT EXISTS card_days (
  date TEXT PRIMARY KEY, fp TEXT, n INTEGER, dropped INTEGER, cost REAL, made_at TEXT
);
-- 「问」那个聊天窗口的记录。cites：这条回答引用的卡 / 原话（存一份快照，卡重做了旧回答照样看得懂）；
-- sid：「问 Claude」那一轮的 Claude 会话（追问接着它，_migrate 补的列）；trace：深挖怎么查的 JSON {steps, secs}（10-17，界面上「查了 N 步」）；
-- replaced：重新跑 / 改了问题之后的旧版本（10-17，照 claude.ai），不删，界面上「‹ 1/2 ›」翻得到，给模型的前文里没有它
CREATE TABLE IF NOT EXISTS qa_chat (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, role TEXT, text TEXT, cites TEXT
);
-- 一段一段的对话（2026-10-13）：qa_chat.thread_id 指这里；title 先是第一个问题，named 0 = 还是它、1 = 模型起的短标题、2 = 用户改的。
-- 每句也写进 messages（来源 ask，窗口 id = ask-<id>）。pinned：左栏置顶（10-16）
CREATE TABLE IF NOT EXISTS qa_threads (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created TEXT, updated TEXT,
  named INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0);

-- 长聊清单（longchats.py，10-23）：他给长期在聊的窗口标的「要迁 / 不迁」（move 1 / 0，没标 NULL）、续集接着哪个窗口（after）
CREATE TABLE IF NOT EXISTS long_chats (conv_id TEXT PRIMARY KEY, move INTEGER, after TEXT);

-- 记忆（memory.py，10-25）：「问」先看的档案。kind：you（关于你 / 偏好）| area（领域）；off 1 = 不让「问」看；
-- last_seen：最近一张沾边的卡是哪天（30 天内的才进目录）。事实只加不删：换下来的记 old_by（新那条的 id；0 = 整理时没写进去；-1 = 他划掉的）
-- by：ai | hand（他自己写的，整理时模型必须留着）；cites：出处卡 id 的 JSON 列表；pos：页里的顺序
CREATE TABLE IF NOT EXISTS memory_topics (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
  summary TEXT, keywords TEXT, off INTEGER NOT NULL DEFAULT 0, last_seen TEXT, written_at TEXT, made_at TEXT);
CREATE TABLE IF NOT EXISTS memory_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL, text TEXT NOT NULL,
  as_of TEXT, cites TEXT, by TEXT NOT NULL DEFAULT 'ai', pos INTEGER, old_by INTEGER, made_at TEXT);
CREATE INDEX IF NOT EXISTS memory_facts_topic ON memory_facts(topic_id);

-- ---------------------------------------------------------------- 重复窗口的存档（dedup.py，2026-10-13）
-- 同一个窗口手动导入一份（指纹 id）、自动同步又一份（uuid）：多出来的那份整份挪到这里（JSON 原样），正常的表里看不到。
CREATE TABLE IF NOT EXISTS conversations_dup (id TEXT PRIMARY KEY, dup_of TEXT, archived_at TEXT, row TEXT);
CREATE TABLE IF NOT EXISTS messages_dup (id TEXT PRIMARY KEY, conv_id TEXT, row TEXT);
CREATE INDEX IF NOT EXISTS messages_dup_conv ON messages_dup(conv_id);

-- ---------------------------------------------------------------- Claude Code 的对话（claude_code.py，2026-10-13）
-- 读过的会话文件：大小、修改时间没变就不再读
CREATE TABLE IF NOT EXISTS cc_files (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, synced_at TEXT);

-- ---------------------------------------------------------------- Claude 用量（llm.claude，2026-10-13）
-- 每次 claude -p 一行：token（缓存读 / 写分开）、等值美元（订阅里不实扣，看额度用得快不快）
CREATE TABLE IF NOT EXISTS claude_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, purpose TEXT, model TEXT, effort TEXT,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER, usd REAL, secs REAL, ok INTEGER
);
-- 走 API 的调用（DeepSeek 官方 / 百炼 / OpenRouter 上的 DeepSeek，2026-10-14：只有 DeepSeek key 的电脑也要看得到花了多少）。
-- cache_hit：DeepSeek 上下文缓存命中的输入 token（按 1/10 价算）；usd 是按价目表估的
CREATE TABLE IF NOT EXISTS api_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, route TEXT, model TEXT, purpose TEXT,
  input INTEGER, output INTEGER, cache_hit INTEGER, usd REAL, secs REAL, ok INTEGER
);

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
  files_written INTEGER, files_total INTEGER, bytes_written INTEGER, warning TEXT
);
-- 每个备份文件上次写进去的内容指纹：没变就不写（也就不重传）。
-- 记在本地而不是去读 Drive 里的文件 —— Drive 的「仅在线」文件一读就会触发下载。
CREATE TABLE IF NOT EXISTS backup_files (path TEXT PRIMARY KEY, sha1 TEXT, size INTEGER, written_at TEXT);
-- 备份里每个核心数据文件现在有哪些行（键 + 内容指纹）：本地少了 / 改了哪行，不用去读 Drive 就知道（backup.py）
CREATE TABLE IF NOT EXISTS backup_keys (path TEXT NOT NULL, key TEXT NOT NULL, h TEXT, PRIMARY KEY (path, key));

-- ---------------------------------------------------------------- Chrome 浏览记录（browsing.py，2026-10-20）：只存不进日记；id = profile:visit_time
CREATE TABLE IF NOT EXISTS browse_visits (
  id TEXT PRIMARY KEY, profile TEXT NOT NULL, vt INTEGER NOT NULL, date TEXT, hhmm TEXT, url TEXT, title TEXT, secs INTEGER
);
CREATE INDEX IF NOT EXISTS idx_browse_profile ON browse_visits(profile, vt);

-- ---------------------------------------------------------------- 隐私锁（lock.py，2026-10-16）：只一行，密码的 PBKDF2 哈希
CREATE TABLE IF NOT EXISTS app_lock (id INTEGER PRIMARY KEY CHECK (id = 1), hash TEXT NOT NULL, set_at TEXT);
-- Touch ID 解锁（10-16）：这台 Mac 上登记过的通行密钥，只存公钥（SPKI DER）和算法号；去掉密码时一起清掉
CREATE TABLE IF NOT EXISTS app_lock_keys (id TEXT PRIMARY KEY, spki BLOB NOT NULL, alg INTEGER NOT NULL, added_at TEXT);
"""

# 老库补列：(表, 列, 类型)。新库的 SCHEMA 里已经有了，这里只对老库起作用。
_ADD_COLUMNS = [
    ('conversations', 'title_fallback', 'INTEGER DEFAULT 0'), ('conversations', 'n_days', 'INTEGER'),
    ('conversations', 'first_import', 'TEXT'), ('conversations', 'last_import', 'TEXT'),
    ('themes', 'desc', 'TEXT'), ('checkins', 'reply', 'TEXT'), ('diaries', 'added_later', 'INTEGER DEFAULT 0'),
    ('journal', 'gone_at', 'TEXT'), ('qa_chat', 'sid', 'TEXT'), ('qa_chat', 'thread_id', 'INTEGER'), ('qa_chat', 'trace', 'TEXT'), ('qa_chat', 'replaced', 'INTEGER'),
    ('qa_threads', 'named', 'INTEGER DEFAULT 0'), ('qa_threads', 'pinned', 'INTEGER DEFAULT 0'),
    ('backups', 'files_written', 'INTEGER'), ('backups', 'files_total', 'INTEGER'),
    ('backups', 'bytes_written', 'INTEGER'), ('backups', 'warning', 'TEXT'),
]


def _cols(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def _migrate(db):
    # 证据卡改成按窗口做（2026-10-13）：老的按天做的卡先整张表留一份，再补 topic 列（新卡写进来会按窗口把老卡替掉）
    if 'topic' not in _cols(db, 'cards'):
        db.execute('CREATE TABLE IF NOT EXISTS cards_v1 AS SELECT * FROM cards')
        db.execute('ALTER TABLE cards ADD COLUMN topic TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS cards_conv ON cards(conv_id)')
        db.execute('CREATE INDEX IF NOT EXISTS cards_msg ON cards(msg_id)')
    for table, col, typ in _ADD_COLUMNS:
        if col not in _cols(db, table):
            db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
    # 「问」分成一段一段的对话（2026-10-13）：以前那一条记录归进一段「以前的问答」
    if db.execute('SELECT 1 FROM qa_chat WHERE thread_id IS NULL LIMIT 1').fetchone():
        first, last = db.execute('SELECT MIN(ts), MAX(ts) FROM qa_chat WHERE thread_id IS NULL').fetchone()
        tid = db.execute("INSERT INTO qa_threads (title, created, updated) VALUES ('以前的问答', ?, ?)", (first, last)).lastrowid
        db.execute('UPDATE qa_chat SET thread_id=? WHERE thread_id IS NULL', (tid,))
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
    # msg_fts 换成 trigram（2026-10-07）：老的那张只有我的话、claude.ai 同步进来的不在里面、中文搜不到。重建一次约 20 秒
    sql = (db.execute("SELECT sql FROM sqlite_master WHERE name='msg_fts'").fetchone() or [''])[0] or ''
    if 'trigram' not in sql:
        db.execute('DROP TABLE IF EXISTS msg_fts')
        db.execute("""CREATE VIRTUAL TABLE msg_fts USING fts5(
                        text, content='messages', content_rowid='rowid', tokenize='trigram', detail=none)""")
        db.execute("INSERT INTO msg_fts(msg_fts) VALUES ('rebuild')")
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
