"""问「某类话题一共多少字」（2026-10-14 用户：网文游戏一共多少字，它只翻到以前别人算的一张表）：
convs 能一次搜几个词、带每个窗口的字数；sum 把挑出来的窗口加起来，分「你打的 / AI 写的」。"""
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
        look.main(list(argv))
    return buf.getvalue()


class Sum(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'card_convs'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('g1', 'claude', 'GM 修仙'), ('g2', 'gemini', '网文推演'), ('x', 'claude', '数学')")
        add('m1', 'g1', '2026-05-01', '10:00', 'human', '我选择拔剑')                   # 5 字
        add('m2', 'g1', '2026-05-01', '10:01', 'assistant', '剑光一闪' * 10)          # 40 字
        add('m3', 'g2', '2026-06-01', '10:00', 'human', '继续写网文')                  # 5 字
        add('m4', 'x', '2026-06-02', '10:00', 'human', '求导')

    def test_convs_several_words_with_sizes(self):
        out = run('convs', '网文', 'GM')
        self.assertIn('g1', out)
        self.assertIn('g2', out)
        self.assertNotIn('《数学》', out)
        self.assertIn('你 5 字 · AI 40 字', out)

    def test_sum(self):
        out = run('sum', 'g1', 'g2')
        self.assertIn('合计 2 个窗口：你 10 字 · AI 40 字 · 共 50 字', out)
        self.assertIn('GM 修仙', out)

    def test_sum_unknown_id_is_reported(self):
        self.assertIn('没有这个窗口：nope', run('sum', 'g1', 'nope'))


if __name__ == '__main__':
    unittest.main()
