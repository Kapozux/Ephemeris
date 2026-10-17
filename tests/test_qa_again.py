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



class Redo(unittest.TestCase):
    """重新跑 / 改了问题再跑（10-17，照 claude.ai）：只对最后一问；旧版本标 replaced 不删；给模型的前文里没有旧版本；不接上一轮的会话。"""
    def setUp(self):
        llm.uncancel()
        seed()
        store.run('DELETE FROM app_lock')
        self.calls = []

        def run(args, stdin):
            self.calls.append((args, stdin))
            return {'result': f'第 {len(self.calls)} 版。', 'session_id': f's{len(self.calls)}', 'events': deltas(f'第 {len(self.calls)} 版。')}
        llm.use_claude(run)
        self.t = qa.new_thread()
        qa.answer_deep('我第一次写代码是什么时候', thread=self.t)
        self.c = appmod.app.test_client()

    def tearDown(self):
        llm.use_claude(None)

    def wait(self):
        import time
        for _ in range(200):
            if not qa.live()['running']:
                return
            time.sleep(0.02)

    def test_retry_keeps_old_answer_as_version(self):
        r = self.c.post('/api/qa/ask', json={'redo': 'retry', 'thread': self.t}, headers=H)
        self.assertTrue(r.get_json()['started'])
        self.wait()
        now = qa.history(thread=self.t)
        self.assertEqual([(t['role'], t['text']) for t in now], [('user', '我第一次写代码是什么时候'), ('assistant', '第 2 版。')])
        every = qa.history(thread=self.t, versions=True)
        self.assertEqual([(t['text'], t['replaced']) for t in every if t['role'] == 'assistant'], [('第 1 版。', 1), ('第 2 版。', None)])
        self.assertNotIn('--resume', self.calls[-1][0])                              # 不接上一轮的会话（里面有换掉的那版）

    def test_edit_replaces_question_and_answer(self):
        self.c.post('/api/qa/ask', json={'redo': 'edit', 'question': '我第一次写 Python 是什么时候', 'thread': self.t}, headers=H)
        self.wait()
        self.assertEqual([t['text'] for t in qa.history(thread=self.t)], ['我第一次写 Python 是什么时候', '第 2 版。'])
        self.assertEqual(len(qa.history(thread=self.t, versions=True)), 4)          # 旧的问题和回答都还在
        self.assertNotIn('我第一次写代码是什么时候', self.calls[-1][1])                 # 旧问题不进前文

    def test_empty_edit_refused(self):
        r = self.c.post('/api/qa/ask', json={'redo': 'edit', 'question': ' ', 'thread': self.t}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(qa.history(thread=self.t, versions=True)), 2)          # 什么都没动


if __name__ == '__main__':
    unittest.main()
