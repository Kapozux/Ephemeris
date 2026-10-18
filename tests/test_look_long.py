"""原文格式（2026-10-18 体检）：长消息里命中在 4000 字之后默认读不到 → msgs 标位置、read --at 跳过去；
他发的超长消息标「可能是贴进来的」；没标题的窗口（Gemini sess_xxxx）用小结头一句；msgs 几个词 = 都要出现。"""
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


class LongAndUntitled(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'card_convs'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title, title_fallback) VALUES ('g', 'gemini', 'sess_0001', 1), ('c', 'claude', '经济复习', 0)")
            db.execute("INSERT INTO card_convs(conv_id, summary) VALUES ('g', '2025-12-18：深夜我和 AI 聊要不要坐到她旁边。后来又聊了物理')")
        look._UNTITLED = None
        add('long', 'c', '2026-05-01', '10:00', 'human', '笔记' * 4000 + '需求曲线向下倾斜' + '尾巴' * 100)
        add('m2', 'g', '2025-12-18', '23:00', 'human', '阿岚说下个月要搬家')
        add('m3', 'g', '2025-12-18', '23:01', 'assistant', '阿岚去哪')

    def test_far_hit_points_to_at(self):
        out = run('msgs', '需求曲线')
        self.assertIn('在第 8000 字处', out)
        self.assertIn('read long --at 需求曲线', out)
        self.assertIn('可能是贴进来的', out)

    def test_read_at_jumps(self):
        out = run('read', 'long', '--at', '需求曲线')
        self.assertIn('出现 1 处', out)
        self.assertIn('— 第 8000 字处 —', out)
        self.assertIn('向下倾斜', out)
        self.assertLess(len(out), 2500)

    def test_untitled_uses_summary(self):
        out = run('msgs', '阿岚')
        self.assertIn('《〔小结〕深夜我和 AI 聊要不要坐到她旁边》', out)
        self.assertNotIn('sess_0001', out)

    def test_words_all_must_appear(self):
        out = run('msgs', '阿岚', '搬')
        self.assertIn('m:m2', out)
        self.assertNotIn('m:m3', out)


if __name__ == '__main__':
    unittest.main()
