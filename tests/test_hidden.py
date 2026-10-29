"""屏蔽（10-29 用户：选哪一天 / 哪些聊天 / Notion 日记「问」完全查不到）：look.py 的每条命令、它自己写的 SQL 都查不到；
连带的（证据卡、窗口小结、那几天的日记、引用它的档案条目）也查不到；恢复后原样回来；日记页那边的库照常读写。"""
import _env  # noqa: F401
import contextlib
import io
import json
import unittest

import hidden
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


class Hidden(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'cards', 'card_convs', 'journal', 'diaries', 'qa_hidden',
                      'memory_topics', 'memory_facts', 'checkins'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('secret', 'claude', '秘密窗口'), ('open', 'claude', '公开窗口')")
            db.execute("INSERT INTO card_convs(conv_id, summary) VALUES ('secret', '小结：紫色大象'), ('open', '小结：公开的事')")
            db.execute("INSERT INTO cards(id, date, conv_id, msg_id, who, kind, text, quote) VALUES "
                       "('k1', '2026-05-01', 'secret', 's1', 'me', '决定', '紫色大象的卡', '紫色大象'), "
                       "('k2', '2026-05-03', 'open', 'o1', 'me', '决定', '公开的卡', '公开')")
            db.execute("INSERT INTO diaries(date, headline, narrative) VALUES ('2026-05-01', '那天', '日记里转述了紫色大象'), "
                       "('2026-05-03', '另一天', '公开的一天'), ('2026-05-05', '手写那天', '日记里写了蓝色鲸鱼')")
            db.execute("INSERT INTO journal(page_id, date, title, text, chars) VALUES ('pg1', '2026-05-05', '手写', '蓝色鲸鱼的手写日记', 9)")
            cur = db.execute("INSERT INTO memory_topics(kind, name, summary) VALUES ('you', '关于你', '')")
            db.execute('INSERT INTO memory_facts(topic_id, text, cites, by) VALUES (?, ?, ?, ?), (?, ?, ?, ?)',
                       (cur.lastrowid, '他喜欢紫色大象', json.dumps(['k1']), 'ai', cur.lastrowid, '他喜欢公开', json.dumps(['k2']), 'ai'))
        add('s1', 'secret', '2026-05-01', '10:00', 'human', '紫色大象是我的秘密')
        add('o1', 'open', '2026-05-03', '10:00', 'human', '公开的话 大象')
        add('o2', 'open', '2026-05-04', '10:00', 'human', '四号的话 大象')

    def test_conv_and_everything_written_from_it(self):
        self.assertIn('紫色大象', run('msgs', '大象'))
        hidden.set_('conv', 'secret')
        self.assertEqual(store.one("SELECT label FROM qa_hidden WHERE ref='secret'")['label'], '秘密窗口')
        out = run('msgs', '大象')
        self.assertNotIn('紫色', out)
        self.assertIn('公开的话', out)
        self.assertIn('没有这条消息', run('read', 's1'))
        self.assertIn('没有这个窗口', run('conv', 'secret'))
        self.assertNotIn('秘密窗口', run('convs', '大象'))
        self.assertNotIn('紫色', run('cards', '紫色大象', '--kw', '紫色'))
        self.assertNotIn('转述了紫色大象', run('diaries', '--full'))           # 它出现那天的日记也挡
        self.assertIn('公开的一天', run('diaries', '--full'))
        self.assertNotIn('紫色', run('day', '2026-05-01'))
        self.assertNotIn('紫色', run('memory', '关于你'))                        # 出处被挡的档案条目
        self.assertIn('他喜欢公开', run('memory', '关于你'))
        self.assertEqual(hidden.card_hidden(), {'k1'})
        for q in ("SELECT text FROM messages", "SELECT summary FROM card_convs", "SELECT text FROM cards", "SELECT narrative FROM diaries",
                  "SELECT text FROM memory_facts", "SELECT title FROM conversations"):
            with self.subTest(q):
                self.assertNotIn('紫色', run('sql', q))
        hidden.set_('conv', 'secret', on=False)
        self.assertIn('紫色大象', run('msgs', '大象'))

    def test_day(self):
        hidden.set_('day', '2026-05-03')
        out = run('msgs', '大象')
        self.assertNotIn('公开的话', out)
        self.assertIn('四号的话', out)
        self.assertNotIn('公开的一天', run('diaries', '--full'))
        self.assertNotIn('公开的卡', run('sql', 'SELECT text FROM cards'))
        self.assertNotIn('公开的事', run('sql', 'SELECT summary FROM card_convs'))   # 窗口碰到这天：小结整段挡
        self.assertIn('2 条', run('stats').split('消息')[1][:10])

    def test_journal_page(self):
        self.assertIn('蓝色鲸鱼', run('journal', '鲸鱼'))
        hidden.set_('journal', 'pg1')
        self.assertNotIn('蓝色鲸鱼', run('journal', '鲸鱼'))
        self.assertIn('没有这页日记', run('read', 'journal-pg1'))
        self.assertNotIn('蓝色鲸鱼', run('diaries', '--full'))                    # 那天的日记会转述它
        self.assertNotIn('蓝色鲸鱼', run('sql', 'SELECT text FROM journal'))

    def test_sql_cannot_go_around(self):
        hidden.set_('conv', 'secret')
        for q in ("SELECT text FROM main.messages", "SELECT * FROM qa_hidden", "SELECT * FROM main.qa_hidden",
                  "SELECT text FROM msg_fts", "SELECT * FROM messages_dup", "SELECT * FROM conversations_dup"):
            with self.subTest(q):
                out = run('sql', q)
                self.assertNotIn('紫色', out)
                self.assertNotIn('secret', out)
        self.assertNotIn('qa_hidden', run('schema'))

    def test_diary_side_unaffected(self):
        hidden.set_('day', '2026-05-01')
        run('stats')
        add('s9', 'secret', '2026-05-01', '11:00', 'human', '写库照常')      # look 跑完把视图撤了：同一个连接照常写
        self.assertEqual(store.one("SELECT COUNT(*) n FROM messages WHERE date='2026-05-01'")['n'], 2)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            hidden.set_('week', 'x')
        with self.assertRaises(ValueError):
            hidden.set_('day', 'not-a-date')


if __name__ == '__main__':
    unittest.main()
