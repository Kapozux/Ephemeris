"""「问」不用记忆（10-30 用户：做一个不看「记忆」的版本，可勾选）：提示词里没有档案，只放行带 --no-memory 的 look.py；
开关换了不接上一轮的会话（那个会话看过 / 没看过档案）；回答记着这次没用记忆。"""
import _env  # noqa: F401
import json
import unittest

import app as appmod
import llm
import qa
import store
from test_cards_qa import deltas, seed

H = {'Origin': 'http://localhost:5055'}


class NoMemory(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        store.run('DELETE FROM app_lock')
        self.calls = []

        def run(args, stdin):
            self.calls.append((args, stdin))
            return {'result': '答上了。', 'session_id': f's{len(self.calls)}', 'events': deltas('答上了。')}
        llm.use_claude(run)
        self.t = qa.new_thread()

    def tearDown(self):
        llm.use_claude(None)

    def allowed(self, i):
        args = self.calls[i][0]
        return args[args.index('--allowedTools') + 1]

    def test_off(self):
        r = qa.answer_deep('我 ED 交哪', thread=self.t, use_memory=False)
        self.assertTrue(r['trace']['no_memory'])
        self.assertIn('--no-memory', self.allowed(0))
        self.assertNotIn('档案', self.calls[0][1])
        trace = json.loads(store.one("SELECT trace FROM qa_chat WHERE thread_id=? AND role='assistant'", self.t)['trace'])
        self.assertTrue(trace['no_memory'])

    def test_on_is_default(self):
        qa.answer_deep('我 ED 交哪', thread=self.t)
        self.assertNotIn('--no-memory', self.allowed(0))
        self.assertIn('档案', self.calls[0][1])

    def test_switch_does_not_resume(self):
        qa.answer_deep('我 ED 交哪', thread=self.t)
        qa.answer_deep('那后来呢', thread=self.t)                       # 同一个开关：接着上一轮
        self.assertIn('--resume', self.calls[1][0])
        qa.answer_deep('再后来呢', thread=self.t, use_memory=False)     # 换了：从头问
        self.assertNotIn('--resume', self.calls[2][0])
        self.assertIn('--no-memory', self.allowed(2))

    def test_route(self):
        c = appmod.app.test_client()
        seen = {}
        saved = qa.start_ask
        qa.start_ask = lambda *a, **kw: seen.update(kw) or True
        try:
            c.post('/api/qa/ask', json={'question': '我 ED 交哪', 'thread': self.t, 'memory': False}, headers=H)
            self.assertIs(seen['use_memory'], False)
            c.post('/api/qa/ask', json={'question': '我 ED 交哪', 'thread': self.t}, headers=H)
            self.assertIs(seen['use_memory'], True)
        finally:
            qa.start_ask = saved


if __name__ == '__main__':
    unittest.main()
