"""主题账本 record()：模型给的编号/名字怎么落库。踩过：链式赋值把名字当成了编号（2026-09-26 KeyError）。"""
import _env  # noqa: F401
import unittest

import index as idx
import ledger


def rows(sql, *a):
    return [tuple(r) for r in idx.connect().execute(sql, a).fetchall()]


class Record(unittest.TestCase):
    def setUp(self):
        ledger.reset()

    def test_new_theme_then_reuse_by_name(self):
        ledger.record('2026-01-01', [{'id': None, 'name': '经济 IA', 'note': '开题'}])
        # 第二天模型给了个不存在的编号，但名字对得上 → 应该落到同一个主题
        ledger.record('2026-01-02', [{'id': 999, 'name': '经济 IA', 'note': '改稿'}])
        self.assertEqual(rows('SELECT COUNT(*) FROM themes'), [(1,)])
        self.assertEqual(rows('SELECT COUNT(*) FROM theme_days'), [(2,)])

    def test_two_new_themes_same_day(self):
        # 同一天两个新主题：曾因 by_name[name] = name 把第二个的编号弄成字符串
        got = ledger.record('2026-01-01', [{'id': None, 'name': 'A', 'note': ''}, {'id': None, 'name': 'B', 'note': ''},
                                           {'id': None, 'name': 'A', 'note': '又一次'}])
        self.assertEqual(sorted(n for _, n in got), ['A', 'B'])
        self.assertTrue(all(isinstance(i, int) for i, _ in got))

    def test_day_count_strips_model_day_numbers(self):
        ledger.record('2026-01-01', [{'id': None, 'name': 'CS IA', 'note': 'CS IA第 7 天，修 bug'}])
        self.assertEqual(rows('SELECT note FROM theme_days'), [('修 bug',)])

    def test_sources_are_separate_and_days_count_once(self):
        ledger.record('2026-01-01', [{'id': None, 'name': '感情', 'note': '聊天里'}], src='ai')
        tid = rows('SELECT id FROM themes')[0][0]
        ledger.record('2026-01-01', [{'id': tid, 'note': '手写里'}], src='hand')
        ledger.record('2026-01-02', [{'id': tid, 'note': '第二天'}], src='hand')
        chip = [c for c in ledger.chips('2026-01-02') if c['id'] == tid][0]
        self.assertEqual(chip['nth'], 2)                 # 两行来源算同一天，第二天就是第 2 天
        # 重写 AI 那一侧不能动手写的行
        ledger.record('2026-01-01', [], src='ai')
        self.assertEqual(rows("SELECT COUNT(*) FROM theme_days WHERE date='2026-01-01'"), [(1,)])


if __name__ == '__main__':
    unittest.main()
