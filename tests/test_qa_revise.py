"""不是 Claude 时「问」的两道护栏（11-03，r1 上 Kimi 编了 22 处、同一件事换着词搜二十多遍）：
LookGuard 不重跑搜过的、连着搜不读原文要提醒；_revise 交出去之前核一遍，原文撑不住 / 没查到过 / 没标出处的具体说法让它照原文改。"""
import _env  # noqa: F401
import json
import os
import unittest
from unittest import mock

import llm
import qa
from test_cards_qa import seed


class Guard(unittest.TestCase):
    def setUp(self):
        self.ran = []
        self.g = qa.LookGuard(lambda a: self.ran.append(a['command']) or '结果')

    def test_same_search_not_rerun(self):
        self.g({'command': 'msgs 面诊 医院 --since 2026-01-01'})
        out = self.g({'command': 'msgs 医院 面诊 --since 2026-01-01'})          # 词换了顺序也算同一条
        self.assertEqual(len(self.ran), 1)
        self.assertIn('第 1 步已经搜过', out)
        self.g({'command': 'msgs 医院 面诊 --since 2026-03-01'})                 # 时间范围变了：照跑
        self.assertEqual(len(self.ran), 2)

    def test_nudge_after_many_searches_and_reset_on_read(self):
        outs = [self.g({'command': f'msgs 词{i}'}) for i in range(qa.SEARCH_RUN)]
        self.assertNotIn('提醒', outs[-2])
        self.assertIn('提醒', outs[-1])
        self.g({'command': 'read m:m1'})
        self.assertNotIn('提醒', self.g({'command': 'msgs 新词'}))


class Revise(unittest.TestCase):
    def setUp(self):
        seed()
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def run_revise(self, text, replies):
        it = iter(replies)
        llm.use(lambda p: (self.prompts.append(p), next(it))[1])
        cites = qa._deep_cites(text)[1]
        with mock.patch.dict(os.environ, {'EPHEMERIS_QA_REVISE': 'on'}):
            return qa._revise(qa._deep_cites(text)[0], cites)

    def test_rewrites_with_problems_listed(self):
        text = '你决定 ED 交 MIT [[m:m1]]。AI 劝你放弃申请 [[m:m2]]。你在 3 月 5 日哭了一整晚。'
        fixed = '你决定 ED 交 MIT [[m:m1]]。AI 建议你把论点收窄 [[m:m2]]。'
        out, n = self.run_revise(text, [json.dumps({'bad': [{'i': 2, 'why': 'AI 只是建议收窄论点'}]}, ensure_ascii=False), fixed])
        self.assertEqual((out, n), (fixed, 2))
        p = self.prompts[1]
        self.assertIn('原文撑不住：AI 劝你放弃申请（原文实际是：AI 只是建议收窄论点）', p)
        self.assertIn('没标出处的具体说法：你在 3 月 5 日哭了一整晚。', p)

    def test_clean_answer_untouched(self):
        text = '你决定 ED 交 MIT [[m:m1]]。'
        out, n = self.run_revise(text, [json.dumps({'bad': []})])
        self.assertEqual((n, len(self.prompts)), (0, 1))                       # 只核了一次，没让它改

    def test_broken_rewrite_keeps_original(self):
        text = '你决定 ED 交 MIT [[m:m1]]。AI 劝你放弃申请 [[m:m2]]。' * 3
        out, n = self.run_revise(text, [json.dumps({'bad': [{'i': 2, 'why': 'x'}]}), '好'])
        self.assertEqual(n, 0)
        self.assertIn('放弃申请', out)


if __name__ == '__main__':
    unittest.main()
