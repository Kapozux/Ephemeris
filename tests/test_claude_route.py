"""Claude 当首选（2026-10-13）：能用就先走它、记账；撞上限停掉并退回原来的路线；Session 第二次起只送任务（--resume）。"""
import _env  # noqa: F401
import time
import unittest

import llm
import store


class ClaudeRoute(unittest.TestCase):
    def setUp(self):
        store.run('DELETE FROM claude_usage')
        self.calls = []
        self._cheap, self._gemini = llm._cheap, llm._gemini
        llm._cheap = lambda prompt, why=None: 'deepseek 回的'
        llm._gemini = lambda prompt, model=None: 'gemini 回的'

    def tearDown(self):
        llm.use_claude(None)
        llm._cheap, llm._gemini = self._cheap, self._gemini
        llm._paused.pop('claude', None)

    def fake(self, reply):
        def run(args, stdin):
            self.calls.append((args, stdin))
            return reply(args, stdin) if callable(reply) else reply
        llm.use_claude(run)

    def test_claude_first_and_usage_recorded(self):
        self.fake({'result': 'Claude 回的', 'session_id': 's1', 'total_cost_usd': 0.01,
                   'usage': {'input_tokens': 5, 'output_tokens': 7, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20}})
        self.assertEqual(llm.ask('写日记', effort='low', purpose='diary'), 'Claude 回的')
        args = self.calls[0][0]
        self.assertEqual(args[args.index('--effort') + 1], 'low')
        self.assertIn('--no-session-persistence', args)
        self.assertEqual(llm.claude_week()['calls'], 1)
        self.assertEqual(store.one('SELECT purpose, cache_read FROM claude_usage'), {'purpose': 'diary', 'cache_read': 100})

    def test_limit_pauses_claude_and_falls_back(self):
        self.fake({'is_error': True, 'result': 'Claude AI usage limit reached|%d' % (time.time() + 3600)})
        self.assertEqual(llm.ask('写日记'), 'deepseek 回的')
        self.assertFalse(llm.claude_on())                       # 停到重置时间
        llm.ask('再写一篇')
        self.assertEqual(len(self.calls), 1)                     # 停着的时候不再去碰 Claude

    def test_cheap_route_skips_claude(self):
        self.fake({'result': '不该走到这'})
        self.assertEqual(llm.ask('一次性的大批量活', route='cheap'), 'deepseek 回的')
        self.assertEqual(self.calls, [])

    def test_session_sends_material_once(self):
        self.fake(lambda args, stdin: {'result': '好', 'session_id': 's9'})
        s = llm.Session('材料：一整天的聊天', purpose='morning')
        s.ask('写日记')
        s.ask('做证据卡')
        (a1, in1), (a2, in2) = self.calls
        self.assertIn('材料：一整天的聊天', in1)
        self.assertNotIn('--no-session-persistence', a1)          # 第一次留下会话
        self.assertEqual(a2[a2.index('--resume') + 1], 's9')
        self.assertEqual(in2, '做证据卡')                          # 第二次只送任务，材料走缓存

    def test_session_falls_back_with_material(self):
        seen = []
        llm._cheap = lambda prompt, why=None: (seen.append(prompt), 'deepseek 回的')[1]
        llm.use_claude(lambda args, stdin: {'is_error': True, 'result': 'Not logged in · Please run /login'})
        self.assertEqual(llm.Session('材料').ask('任务'), 'deepseek 回的')
        self.assertIn('材料', seen[0])                             # 退回时材料和任务一起送


if __name__ == '__main__':
    unittest.main()
