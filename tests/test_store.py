"""store：老库升级不丢数据；q / one / run / tx 的行为。"""
import _env  # noqa: F401
import os
import sqlite3
import tempfile
import unittest

import store

# 第一版的样子：后来才加的列（themes.desc、theme_days.src、conv_notes.fp、checkins.reply…）都没有
OLD = """
CREATE TABLE conversations (id TEXT PRIMARY KEY, source TEXT, title TEXT,
  first_ts TEXT, last_ts TEXT, n_msgs INTEGER, chars INTEGER);
CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, source TEXT,
  ts TEXT, date TEXT, hhmm TEXT, role TEXT, text TEXT, chars INTEGER);
CREATE TABLE themes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_on TEXT);
CREATE TABLE theme_days (theme_id INTEGER NOT NULL, date TEXT NOT NULL, note TEXT, PRIMARY KEY (theme_id, date));
CREATE TABLE conv_notes (conv_id TEXT NOT NULL, date TEXT NOT NULL, text TEXT NOT NULL,
  generated_at TEXT NOT NULL, PRIMARY KEY (conv_id, date));
CREATE TABLE checkins (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, word TEXT NOT NULL,
  valence REAL NOT NULL, note TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
INSERT INTO themes(name, created_on) VALUES ('经济论文', '2026-01-01');
INSERT INTO theme_days VALUES (1, '2026-01-01', '开题'), (1, '2026-01-02', '改稿');
INSERT INTO messages VALUES ('m1', 'c1', 'claude', 't', '2026-01-01', '10:00', 'human', 'abc', 3),
                            ('m2', 'c1', 'claude', 't', '2026-01-01', '10:01', 'human', 'de', 2);
INSERT INTO conv_notes VALUES ('c1', '2026-01-01', '小结', 'x'), ('c1', '', '走向', 'x');
INSERT INTO checkins(date, word, valence) VALUES ('2026-01-01', '开心', 1.5);
"""


def cols(db, t):
    return {r[1] for r in db.execute(f'PRAGMA table_info({t})')}


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.saved = store.DB_PATH, store._conn
        store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix='ephemeris-old-'), 'old.db')
        store._conn = None
        raw = sqlite3.connect(store.DB_PATH)
        raw.executescript(OLD)
        raw.close()

    def tearDown(self):
        store._conn.close()
        store.DB_PATH, store._conn = self.saved

    def test_old_db_gets_new_columns_and_keeps_rows(self):
        db = store.connect()
        self.assertIn('src', cols(db, 'theme_days'))
        self.assertIn('desc', cols(db, 'themes'))
        self.assertIn('reply', cols(db, 'checkins'))
        self.assertIn('n_days', cols(db, 'conversations'))
        self.assertEqual(store.q('SELECT date, note, src FROM theme_days ORDER BY date'),
                         [{'date': '2026-01-01', 'note': '开题', 'src': 'ai'},
                          {'date': '2026-01-02', 'note': '改稿', 'src': 'ai'}])
        # 老小结的 fp 按现在的内容补上（2 条、5 字），整体走向那行不补
        self.assertEqual({r['date']: r['fp'] for r in store.q('SELECT date, fp FROM conv_notes')},
                         {'2026-01-01': '2:5', '': None})
        # 没有的表都建上了
        self.assertIsNotNone(store.one("SELECT name FROM sqlite_master WHERE name='journal_fts'"))

    def test_connect_twice_is_same_and_idempotent(self):
        self.assertIs(store.connect(), store.connect())


class Helpers(unittest.TestCase):
    def test_run_one_q(self):
        rid = store.run("INSERT INTO flashback_seen VALUES ('2026-01-05', 'now')")
        self.assertTrue(rid)
        self.assertEqual(store.one("SELECT date FROM flashback_seen WHERE date='2026-01-05'"), {'date': '2026-01-05'})
        self.assertIsNone(store.one("SELECT date FROM flashback_seen WHERE date='1999-01-01'"))

    def test_tx_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with store.tx() as db:
                db.execute("INSERT INTO flashback_seen VALUES ('2026-01-06', 'now')")
                raise RuntimeError('中途出错')
        self.assertEqual(store.q("SELECT * FROM flashback_seen WHERE date='2026-01-06'"), [])


if __name__ == '__main__':
    unittest.main()
