"""「问 Claude」自己写只读 SQL（2026-10-14 用户：「你自己看能给多少给多少」）：现成命令不够时，schema 看表、sql 自己查，
统计、加总、分组都能算准数。只读：库按只读打开，写语句一律拒绝；输出有上限。另有 journal（搜手写日记）、diaries（读一段日期的日记）。"""
import _env  # noqa: F401
import contextlib
import io
import unittest

import look
import store
from test_cards_qa import add


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            look.main(list(argv))
        except SystemExit:
            pass
    return buf.getvalue()


class Sql(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'journal', 'diaries'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('g1', 'claude', 'GM 修仙')")
            db.execute("INSERT INTO journal(page_id, date, text, chars) VALUES ('p1', '2026-05-01', '今天写了很多网文', 8)")
            db.execute("INSERT INTO diaries(date, headline, narrative) VALUES ('2026-05-01', '玩 GM', '一整天在推剧情'), ('2026-05-02', '数学', '做题')")
        add('m1', 'g1', '2026-05-01', '10:00', 'human', '我选择拔剑')
        add('m2', 'g1', '2026-05-01', '10:01', 'assistant', '剑光一闪')

    def test_select_works(self):
        out = run('sql', "SELECT role, SUM(chars) n FROM messages GROUP BY role ORDER BY role")
        self.assertIn('assistant\t4', out)
        self.assertIn('human\t5', out)

    def test_writes_are_refused(self):
        for q in ("DELETE FROM messages", "UPDATE messages SET text='x'", "SELECT 1; DELETE FROM messages",
                  "WITH x AS (SELECT 1) DELETE FROM messages", "ATTACH 'x.db' AS y", "PRAGMA writable_schema=1"):
            with self.subTest(q):
                out = run('sql', q)
                self.assertIn('只能查', out)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages')['n'], 2)

    def test_output_is_capped(self):
        out = run('sql', "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM r WHERE i < 1000) SELECT i FROM r", '--n', '20')
        self.assertIn('只列前 20 行', out)
        self.assertLess(len(out.splitlines()), 30)

    def test_schema_lists_tables(self):
        out = run('schema')
        for t in ('messages', 'conversations', 'cards', 'diaries', 'journal', 'checkins'):
            self.assertIn(t, out)

    def test_journal_and_diaries(self):
        self.assertIn('今天写了很多网文', run('journal', '网文'))
        out = run('diaries', '--since', '2026-05-01', '--until', '2026-05-02')
        self.assertIn('玩 GM', out)
        self.assertIn('数学', out)


if __name__ == '__main__':
    unittest.main()
