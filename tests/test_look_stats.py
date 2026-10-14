"""「问 Claude」能答「你手上有多少数据」（2026-10-14：它之前只能拿「的」字搜一遍猜个下限）：look.py stats 给准数，
不算「问」自己的对话（来源 ask）。"""
import _env  # noqa: F401
import contextlib
import io
import unittest

import look
import store
from test_cards_qa import DAY, add, seed


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        look.main(list(argv))
    return buf.getvalue()


class Stats(unittest.TestCase):
    def setUp(self):
        seed()
        with store.tx() as db:
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('c2', 'gemini', '数学')")
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('ask-1', 'ask', '问')")
            db.execute("INSERT INTO cards(id, date, conv_id, msg_id, who, kind, text) VALUES ('k1', ?, 'c1', 'm1', 'me', '决定', 'ED 交 MIT')", (DAY,))
        add('m4', 'c2', '2025-08-01', '09:00', 'human', 'x')
        add('a1', 'ask-1', DAY, '04:00', 'human', '问自己')
        store.run("UPDATE messages SET source='ask' WHERE id='a1'")

    def test_totals(self):
        out = run('stats')
        self.assertIn('消息 4 条（你 3 · AI 1）', out)
        self.assertIn('窗口 2 个', out)
        self.assertIn('证据卡 1 张', out)
        self.assertIn('2025-08-01', out)
        self.assertIn('gemini', out)
        self.assertNotIn('ask', out.replace('ask-', ''))

    def test_range(self):
        out = run('stats', '--since', '2025-08-01', '--until', '2025-08-31')
        self.assertIn('消息 1 条（你 1 · AI 0）', out)


if __name__ == '__main__':
    unittest.main()
