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

    def test_one_shot_calls_skip_cache_writes(self):
        self.assertEqual(llm._env(['-p', '--no-session-persistence'])['DISABLE_PROMPT_CACHING'], '1')
        self.assertIsNone(llm._env(['-p']))                                  # Session 第一次：要留缓存给后面读
        self.assertIsNone(llm._env(['-p', '--resume', 's1']))                # 接着问

    def test_session_falls_back_with_material(self):
        seen = []
        llm._cheap = lambda prompt, why=None: (seen.append(prompt), 'deepseek 回的')[1]
        llm.use_claude(lambda args, stdin: {'is_error': True, 'result': 'Not logged in · Please run /login'})
        self.assertEqual(llm.Session('材料').ask('任务'), 'deepseek 回的')
        self.assertIn('材料', seen[0])                             # 退回时材料和任务一起送


class DaySession(unittest.TestCase):
    """写日记、打「卡得多狠」共用一个会话：当天记录只送一次。"""
    DAY = '2026-09-20'

    def setUp(self):
        import diary
        diary._sessions.clear()
        with store.tx() as db:
            for t in ('messages', 'conversations', 'mood_scores'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('c1', 'claude', '申请季')")
        text = '我今天把 MIT 的文书第二段重写了三遍，越写越烦。'
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'm1', 'c1', 'claude', f'{self.DAY}T10:00', self.DAY,
                  '10:00', 'human', text, len(text))
        self.calls = []
        llm.use_claude(lambda args, stdin: (self.calls.append((args, stdin)),
                                            {'result': '{"friction": 2, "evidence": "越写越烦"}', 'session_id': 'd1'})[1])

    def tearDown(self):
        llm.use_claude(None)

    def test_friction_reuses_the_day_session(self):
        import diary
        import mood
        diary.session_for(self.DAY).ask('写日记')
        r = mood.score_friction(self.DAY, call_model=lambda p: self.fail('不该整段再送'))
        self.assertEqual(r['friction'], 2)
        (a1, in1), (a2, in2) = self.calls
        self.assertIn('越写越烦', in1)                          # 第一次：当天记录 + 任务
        self.assertEqual(a2[a2.index('--resume') + 1], 'd1')
        self.assertNotIn('MIT 的文书', in2)                       # 第二次：只有打分要求
        self.assertIs(diary.session_for(self.DAY), diary.session_for(self.DAY))


if __name__ == '__main__':
    unittest.main()
