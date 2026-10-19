"""只有 AI 消息的窗口不再被删掉窗口那行（10-19 体检：39 个窗口、63 条消息哪儿都看不到）。"""
import _env  # noqa: F401
import unittest

import index as idx
import store


class RecountKeepsAiOnlyWindows(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            db.execute('DELETE FROM messages')
            db.execute('DELETE FROM conversations')
            db.execute("INSERT INTO conversations (id, source, title) VALUES ('empty', 'claude', '空壳')")
            db.execute("INSERT INTO conversations (id, source, title) VALUES ('ai-only', 'gemini', 'Canvas')")
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       ('m1', 'ai-only', 'gemini', '2026-01-01T10:00', '2026-01-01', '10:00', 'assistant', '[Canvas]\n一段代码', 9))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',             # 以前被删了窗口行的
                       ('m2', 'lost', 'gemini', '2026-01-02T10:00', '2026-01-02', '10:00', 'assistant', '回答\n第二行', 6))

    def test_ai_only_kept_lost_restored_empty_dropped(self):
        with store.tx() as db:
            idx._recount(db)
        rows = {r['id']: r for r in store.q('SELECT id, source, title, title_fallback, n_msgs, first_ts FROM conversations')}
        self.assertEqual(sorted(rows), ['ai-only', 'lost'])
        self.assertEqual(rows['ai-only']['title'], 'Canvas')
        self.assertEqual((rows['lost']['source'], rows['lost']['title'], rows['lost']['title_fallback'], rows['lost']['n_msgs']),
                         ('gemini', '回答 第二行', 1, 0))


if __name__ == '__main__':
    unittest.main()
