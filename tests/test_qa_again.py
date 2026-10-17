"""「重新问」（2026-10-17：问到一半服务重启了，回答丢了、问题还在）：重问这段对话里没答完的最后那句，问题不再记一遍。"""
import _env  # noqa: F401
import unittest

import app as appmod
import llm
import qa
import store
from test_cards_qa import deltas, seed

H = {'Origin': 'http://localhost:5055'}


class Again(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        store.run('DELETE FROM app_lock')
        self.prompts = []

        def run(args, stdin):
            self.prompts.append(stdin)
            return {'result': '答上了。', 'session_id': 's1', 'events': deltas('答上了。')}
        llm.use_claude(run)
        self.t = qa.new_thread()
        qa._say(self.t, 'user', '我第一次写代码是什么时候')          # 问了，服务重启了，没有回答

    def tearDown(self):
        llm.use_claude(None)

    def test_unanswered(self):
        self.assertEqual(qa.unanswered(self.t), '我第一次写代码是什么时候')
        qa.answer_deep('我第一次写代码是什么时候', thread=self.t, again=True)
        self.assertIsNone(qa.unanswered(self.t))

    def test_again_does_not_repeat_question(self):
        qa.answer_deep('我第一次写代码是什么时候', thread=self.t, again=True)
        self.assertEqual([t['role'] for t in qa.history(thread=self.t)], ['user', 'assistant'])
        self.assertEqual(store.one("SELECT COUNT(*) n FROM messages WHERE source='ask' AND role='human' AND text='我第一次写代码是什么时候'")['n'], 1)
        self.assertNotIn('问：我第一次写代码', self.prompts[0])                    # 这次的问题不当前文

    def test_route_again_needs_dangling_question(self):
        c = appmod.app.test_client()
        other = qa.new_thread()
        qa._say(other, 'user', '问')
        qa._say(other, 'assistant', '答')
        r = c.post('/api/qa/ask', json={'again': True, 'thread': other}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn('没答完', r.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
