"""聊天页能自己选模型和 effort（2026-10-14：照 claude.ai 那个菜单，除了 Fable 太贵）。
选的模型管这一次问的所有 Claude 调用；effort 只管写回答那一步（拆问题那一步还是 low）。没选就是 Opus 5.5 · Max（10-17 起；之前默认 High）。"""
import _env  # noqa: F401
import json
import threading
import time
import unittest

import app as appmod
import cards
import llm
import qa
import store
from test_cards_qa import deltas, out, seed


def arg(args, name):
    return args[args.index(name) + 1]


class Pick(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        llm.use(lambda p: out([{'m': 1, 'k': '决定', 'p': '大学申请', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'}]))
        cards.build_conv('c1')
        llm.use(None)
        self.calls = []
        plan = json.dumps({'q': '我 ED 交哪', 'keywords': ['MIT'], 'since': None, 'until': None})

        def run(args, stdin):
            self.calls.append(args)
            if 'stream-json' not in args:
                return {'result': plan}
            if '--allowedTools' in args:
                return {'result': '你决定 ED 交 MIT [[c:m1·1]]。', 'session_id': 's1', 'events': deltas('你决定 ED 交 MIT [[c:m1·1]]。')}
            return {'result': '你决定 ED 交 MIT [c1]。', 'events': deltas('你决定 ED 交 MIT [c1]。')}
        llm.use_claude(run)

    def tearDown(self):
        llm.use_claude(None)

    def test_choices_have_no_fable(self):
        ids = [m for m, _ in llm.CLAUDE_CHOICES]
        self.assertEqual(ids[0], llm.CLAUDE_MODEL)
        self.assertFalse([m for m in ids if 'fable' in m])
        self.assertEqual(llm.EFFORTS, ('low', 'medium', 'high', 'xhigh', 'max'))

    def test_quick_uses_picked_model_and_effort(self):
        qa.answer('我早申交的哪？', on_text=lambda x: None, model='claude-sonnet-5-5', effort='medium')
        plan, ans = self.calls[0], self.calls[-1]
        self.assertEqual(arg(plan, '--model'), 'claude-sonnet-5-5')
        self.assertEqual(arg(plan, '--effort'), 'low')                 # 拆问题那步不跟着调
        self.assertEqual(arg(ans, '--model'), 'claude-sonnet-5-5')
        self.assertEqual(arg(ans, '--effort'), 'medium')
        row = store.one("SELECT model, effort FROM claude_usage WHERE purpose='qa' ORDER BY rowid DESC LIMIT 1")
        self.assertEqual((row['model'], row['effort']), ('claude-sonnet-5-5', 'medium'))

    def test_deep_uses_picked_model_and_effort(self):
        qa.answer_deep('我早申交的哪？', on_text=lambda x: None, model='claude-haiku-5-5', effort='max')
        self.assertEqual(arg(self.calls[-1], '--model'), 'claude-haiku-5-5')
        self.assertEqual(arg(self.calls[-1], '--effort'), 'max')

    def test_default_unchanged(self):
        qa.answer('我早申交的哪？', on_text=lambda x: None)
        self.assertEqual(arg(self.calls[-1], '--model'), llm.CLAUDE_MODEL)
        self.assertEqual(arg(self.calls[-1], '--effort'), 'high')

    def test_pick_does_not_leak_to_other_threads(self):
        with llm.using('claude-haiku-5-5'):
            seen = []
            t = threading.Thread(target=lambda: seen.append(llm.current_model()))
            t.start()
            t.join()
            self.assertEqual(llm.current_model(), 'claude-haiku-5-5')
        self.assertEqual(seen, [llm.CLAUDE_MODEL])
        self.assertEqual(llm.current_model(), llm.CLAUDE_MODEL)

    def test_route_checks_and_passes_choice(self):
        c = appmod.app.test_client()
        h = {'Origin': 'http://127.0.0.1:5055'}
        bad = c.post('/api/qa/ask', json={'question': 'x', 'model': 'claude-fable-5-1'}, headers=h)
        self.assertEqual(bad.status_code, 400)
        bad = c.post('/api/qa/ask', json={'question': 'x', 'effort': 'ultra'}, headers=h)
        self.assertEqual(bad.status_code, 400)
        r = c.post('/api/qa/ask', json={'question': '我早申交的哪？', 'model': 'claude-opus-4-8', 'effort': 'xhigh'}, headers=h)
        self.assertEqual(r.status_code, 200)
        for _ in range(100):
            if not qa.live()['running']:
                break
            time.sleep(0.02)
        ans = [c for c in list(self.calls) if arg(c, '--effort') != 'low'][-1]     # 答完另开一条线起标题（effort low），不算
        self.assertEqual(arg(ans, '--model'), 'claude-opus-4-8')
        self.assertEqual(arg(ans, '--effort'), 'xhigh')
        self.assertEqual(qa.live()['model'], 'claude-opus-4-8')
        page = c.get('/chat').get_data(as_text=True)
        self.assertIn('Sonnet 5.5', page)                            # 菜单列表跟着后端的 CLAUDE_CHOICES
        self.assertNotIn('Fable', page)


if __name__ == '__main__':
    unittest.main()


class Strong(unittest.TestCase):
    """交接包（llm.ask_strong）先走本机 Claude Opus 5.5 [1m]（2026-10-14 用户点头；以前是 OpenRouter 上的 Opus 4.6，按量花钱），
    Claude 用不了才走 OpenRouter。"""
    def tearDown(self):
        llm.use_claude(None)

    def test_local_claude_first(self):
        seen = []
        llm.use_claude(lambda args, stdin: seen.append(args) or {'result': '=== 关于我 ===\n…'})
        text, cost = llm.ask_strong('材料')
        self.assertEqual(text, '=== 关于我 ===\n…')
        self.assertIsNone(cost)
        self.assertEqual(arg(seen[0], '--model'), llm.STRONG_CLAUDE)
        self.assertEqual(llm.strong_model(), llm.STRONG_CLAUDE)
        self.assertEqual(llm.current_model(), llm.CLAUDE_MODEL)       # 用完换回来

    def test_falls_back_when_claude_fails(self):
        llm.use_claude(lambda args, stdin: {'is_error': True, 'result': 'boom'})
        orig = llm._strong_openrouter
        llm._strong_openrouter = lambda prompt, max_tokens: ('OR 写的', 1.5)
        try:
            self.assertEqual(llm.ask_strong('材料'), ('OR 写的', 1.5))
        finally:
            llm._strong_openrouter = orig
