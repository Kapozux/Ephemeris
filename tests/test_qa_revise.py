"""不是 Claude 时「问」的两道护栏（11-03，r1 上 Kimi 编了 22 处、同一件事换着词搜二十多遍）：
LookGuard 不重跑搜过的、连着搜不读原文要提醒；_revise 交出去之前核一遍，原文撑不住 / 没查到过 / 没标出处的具体说法让它照原文改。
后来又加两道（llm.deepseek_agent 的 notes / review）：每查几步记笔记、之前的工具输出换成笔记；第一次想交先过单子。假的接口，不发真请求。"""
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


class Agent(unittest.TestCase):
    """假的 OpenAI 兼容接口：按顺序吐预先写好的回复，记下每次发过去的 messages。"""
    def run_agent(self, replies, **k):
        sent, it = [], iter(replies)

        class R:
            status_code = 200

            def __init__(self, msg):
                self.msg = msg

            def json(self):
                return {'choices': [{'message': self.msg}], 'usage': {}}

        def post(url, **kw):
            sent.append(kw['json']['messages'])
            return R(next(it))
        route = ('deepseek', 'https://x.example/v1', 'k', 'm', {})
        with mock.patch('requests.post', post), mock.patch('providers.ask_route', lambda: route):
            out = llm.deepseek_agent('问', {'name': 'look', 'parameters': {}}, lambda a: '工具输出' + a['command'], **k)
        return out, sent

    @staticmethod
    def call(cmd):
        return {'content': '', 'tool_calls': [{'id': cmd, 'function': {'name': 'look', 'arguments': json.dumps({'command': cmd})}}]}

    def test_notes_replace_tool_output(self):
        replies = [self.call('msgs a'), self.call('msgs b'), {'content': '笔记：a 是真的 [m:m1]'}, self.call('read m:m1'),
                   {'content': '答好了'}]
        out, sent = self.run_agent(replies, notes=(2, '记笔记'))
        self.assertEqual(out, '答好了')
        self.assertEqual(sent[2][-1]['content'], '记笔记')                    # 查了两步：让它记
        after = json.dumps(sent[3], ensure_ascii=False)
        self.assertIn('笔记：a 是真的 [m:m1]', after)
        self.assertNotIn('工具输出msgs a', after)                             # 之前的工具输出换成了笔记
        self.assertIn('工具输出read m:m1', json.dumps(sent[4], ensure_ascii=False))

    def test_review_then_keep_draft(self):
        out, sent = self.run_agent([{'content': '草稿'}, {'content': '交'}], review='过单子')
        self.assertEqual(out, '草稿')
        self.assertEqual(sent[1][-1]['content'], '过单子')

    def test_review_then_search_more(self):
        out, sent = self.run_agent([{'content': '草稿'}, self.call('read m:m2'), {'content': '补过的回答'}], review='过单子')
        self.assertEqual((out, len(sent)), ('补过的回答', 3))                  # 只过一次单子，补完就交


if __name__ == '__main__':
    unittest.main()
