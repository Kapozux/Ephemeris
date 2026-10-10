"""export_gemini.js 导出的 Gemini 对话（2026-10-10）：认得出格式、按轮进库、跟 Takeout 导进来过的重叠部分不重复。"""
import _env  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import index as idx
import store

SEC = 1782901821          # 2026-07-01T10:30:21Z


def export(path, convs):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'exporter': 'ephemeris-gemini', 'version': 1, 'conversations': convs}, f, ensure_ascii=False)


class GeminiWeb(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'diaries'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-gw-')
        self.p = os.path.join(self.tmp, 'gemini_export_2026-10-10.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_turns_become_messages(self):
        export(self.p, [{'id': 'c_abc', 'title': '学做菜', 'turns': [
            {'ts': [SEC, 602422000], 'user': '红烧肉怎么做', 'reply': '先焯水'},
            {'ts': [SEC + 60, 0], 'user': '要多久', 'reply': '一个小时'}]}])
        self.assertEqual(idx.sniff(self.p), ('gemini_web', 'gemini'))
        r = idx.ingest(store.connect(), self.p)
        self.assertEqual((r['n_convs'], r['n_msgs']), (1, 4))
        rows = store.q('SELECT conv_id, source, role, ts, text FROM messages ORDER BY ts')
        self.assertEqual(rows[0]['ts'], '2026-07-01T10:30:21.602000+00:00')       # 到毫秒，跟 Takeout 一个口径
        self.assertEqual(rows[1]['ts'], '2026-07-01T10:30:21.603000+00:00')       # 回复 +1 毫秒
        self.assertEqual({x['source'] for x in rows}, {'gemini'})
        self.assertEqual(rows[0]['conv_id'], 'gemini:c_abc')
        self.assertEqual(store.one('SELECT title FROM conversations')['title'], '学做菜')

    def test_turns_already_from_takeout_are_skipped(self):
        # 以前 Takeout 进来的那一轮：窗口 id 是指纹，文字后面多了附件说明，时间到毫秒一样
        with store.tx() as db:
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('fp1', 'gemini', 't')")
            db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', [
                ('m1', 'fp1', 'gemini', '2026-07-01T10:30:21.602000+00:00', '2026-07-01', '18:30', 'human', '红烧肉怎么做\n[附件：a.png]', 9),
                ('m2', 'fp1', 'gemini', '2026-07-01T10:30:21.603000+00:00', '2026-07-01', '18:30', 'assistant', '先焯水（HTML 转的）', 9)])
        export(self.p, [{'id': 'c_abc', 'title': '学做菜', 'turns': [
            {'ts': [SEC, 602422000], 'user': '红烧肉怎么做', 'reply': '先焯水'},
            {'ts': [SEC + 86400 * 50, 5000000], 'user': '八月又问了一次', 'reply': '好'}]}])
        r = idx.ingest(store.connect(), self.p)
        self.assertEqual(r['n_msgs_new'], 2)                                       # 只有八月那一轮是新的
        self.assertEqual(store.one("SELECT COUNT(*) n FROM messages WHERE text LIKE '红烧肉%'")['n'], 1)

    def test_whole_conversation_seen_creates_nothing(self):
        with store.tx() as db:
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('fp1', 'gemini', 't')")
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       ('m1', 'fp1', 'gemini', '2026-07-01T10:30:21.602000+00:00', '2026-07-01', '18:30', 'human', 'x', 1))
        export(self.p, [{'id': 'c_abc', 'title': '学做菜', 'turns': [{'ts': [SEC, 602422000], 'user': 'x', 'reply': 'y'}]}])
        idx.ingest(store.connect(), self.p)
        self.assertIsNone(store.one("SELECT 1 FROM conversations WHERE id='gemini:c_abc'"))

    def test_reimport_is_idempotent(self):
        export(self.p, [{'id': 'c_abc', 'title': 't', 'turns': [{'ts': [SEC, 0], 'user': '一', 'reply': '二'}]}])
        idx.ingest(store.connect(), self.p)
        r = idx.ingest(store.connect(), self.p)
        self.assertEqual(r['n_msgs_new'], 0)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages')['n'], 2)


if __name__ == '__main__':
    unittest.main()
