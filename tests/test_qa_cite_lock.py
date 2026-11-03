"""引用锁（11-03，照 Agent Zero Memory 的 citation lock）：回答里的出处要是这一轮 look 真吐出来过的；
没看过的不删（id 是真的），标 unseen，聊天页提示「这轮没查到过」。追问时前几轮看过的照样认。"""
import _env  # noqa: F401
import unittest

import llm
import qa
from test_cards_qa import deltas, seed


def tool(cmd, result):
    return [{'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'command': cmd}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': result}]}}]


class CiteLock(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        self.t = qa.new_thread()

    def tearDown(self):
        llm.use_claude(None)

    def ask(self, answer, events, q='我 ED 交哪'):
        llm.use_claude(lambda a, s: {'result': answer, 'session_id': 's1', 'events': events + deltas(answer)})
        return qa.answer_deep(q, thread=self.t)

    def test_seen_ok_unseen_flagged(self):
        r = self.ask('交 MIT [[m:m1]]，AI 建议收窄 [[m:m2]]。',
                     tool('python3 look.py msgs MIT', '  [m:m1] 2026-09-01 01:00 我 我决定 ED 交 MIT'))
        by = {c['id']: c for c in r['cites']}
        self.assertNotIn('unseen', by['m:m1'])
        self.assertTrue(by['m:m2']['unseen'])
        self.assertEqual(r['trace']['unseen'], 1)
        stored = qa.history(thread=self.t)[-1]['cites']                # 存进库里的也带着
        self.assertTrue(next(c for c in stored if c['id'] == 'm:m2')['unseen'])

    def test_card_line_counts_its_message(self):
        """卡那行末尾「(原话 m:…)」：看到卡就算看过那句原话。"""
        r = self.ask('交 MIT [[m:m1]]。', tool('python3 look.py cards MIT', '  [c:x/1·a] 我 决定 · 申请 我决定 ED 交 MIT「ED 交 MIT」 (原话 m:m1)'))
        self.assertNotIn('unseen', r['cites'][0])

    def test_follow_up_keeps_earlier_seen(self):
        self.ask('交 MIT。', tool('python3 look.py read m:m2', '[m:m2] 2026-09-01 02:00 AI 可以，但建议…'))
        r = self.ask('AI 说要收窄 [[m:m2]]。', [], q='AI 当时怎么说')
        self.assertNotIn('unseen', r['cites'][0])

    def test_seen_ids_regex(self):
        self.assertEqual(qa._seen_ids('[m:m1] x (原话 m:abc-1) [c:conv/2·k], m:z.'), {'m:m1', 'm:abc-1', 'c:conv/2·k', 'm:z'})


if __name__ == '__main__':
    unittest.main()
