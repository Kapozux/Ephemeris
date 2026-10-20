"""一篇日记怎么读：列表字段解开、拼成纯文本。主题账本、情绪打分、每周备份、Notion、日记页都用这一份。"""
import _env  # noqa: F401
import json
import unittest

import diary

ROW = {'date': '2026-03-03', 'headline': '改经济论文', 'narrative': '一整天在改稿。',
       'topics': '["经济论文"]', 'artifacts': '["第二稿"]', 'open_loops': '["引用格式", "字数"]'}


class Parse(unittest.TestCase):
    def test_json_strings_become_lists(self):
        d = diary.parse(ROW)
        self.assertEqual((d['topics'], d['open_loops']), (['经济论文'], ['引用格式', '字数']))
        self.assertEqual(ROW['topics'], '["经济论文"]')            # 不改传进来的那行

    def test_bad_or_missing_become_empty(self):
        d = diary.parse({'headline': 'x', 'artifacts': '{坏了', 'open_loops': None})
        self.assertEqual((d['artifacts'], d['open_loops'], d['topics']), ([], [], []))

    def test_parse_twice_is_same(self):
        self.assertEqual(diary.parse(diary.parse(ROW)), diary.parse(ROW))


class Text(unittest.TestCase):
    def test_as_text(self):
        self.assertEqual(diary.as_text(ROW), '改经济论文\n一整天在改稿。\n做出来的：第二稿\n没做完的：引用格式；字数')

    def test_handwritten_shape_and_nothing(self):
        # 手写日记进账本时只有正文
        self.assertEqual(diary.as_text({'date': 'd', 'headline': '', 'narrative': '今天还行'}), '今天还行')
        self.assertEqual(diary.as_text(None), '')

    def test_loose_ends_skips_empty(self):
        self.assertEqual(diary.loose_ends({**ROW, 'artifacts': '[]'}), ['没做完的：引用格式；字数'])


if __name__ == '__main__':
    unittest.main()


class Shorten(unittest.TestCase):
    """写完数字数，超了让模型删（10-20）。"""
    def setUp(self):
        self.asked = []

    def model(self, *replies):
        it = iter(replies)
        def ask(prompt):
            self.asked.append(prompt)
            return next(it)
        return ask

    def test_too_long_is_sent_back_until_short(self):
        long = {'headline': '甲', 'narrative': '字' * 420, 'artifacts': ['a', 'b', 'c', 'd'], 'open_loops': []}
        out = diary._shorten(long, self.model(json.dumps({'narrative': '字' * 330, 'artifacts': ['a', 'b', 'c']}),
                                              json.dumps({'narrative': '字' * 280})))
        self.assertEqual(len(out['narrative']), 280)
        self.assertEqual(out['artifacts'], ['a', 'b', 'c'])
        self.assertEqual(out['headline'], '甲')                                  # 没让它改的留着
        self.assertIn('正文 420 字', self.asked[0])
        self.assertIn('4 条', self.asked[0])
        self.assertNotIn('条', self.asked[1].split('\n')[0])                     # 第二次只剩正文超

    def test_short_enough_is_not_sent(self):
        entry = {'narrative': '字' * 300 + '\n\n' + '  ', 'artifacts': ['a']}
        self.assertIs(diary._shorten(entry, self.model()), entry)
        self.assertEqual(self.asked, [])

    def test_gives_up_after_tries_and_keeps_last(self):
        out = diary._shorten({'narrative': '字' * 500}, self.model(*[json.dumps({'narrative': '字' * 400})] * 5))
        self.assertEqual(len(self.asked), diary.SHORTEN_TRIES)
        self.assertEqual(len(out['narrative']), 400)

    def test_broken_reply_keeps_draft(self):
        out = diary._shorten({'narrative': '字' * 500}, self.model('不是 JSON'))
        self.assertEqual(len(out['narrative']), 500)
