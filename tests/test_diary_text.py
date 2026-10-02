"""一篇日记怎么读：列表字段解开、拼成纯文本。主题账本、情绪打分、每周备份、Notion、日记页都用这一份。"""
import _env  # noqa: F401
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
