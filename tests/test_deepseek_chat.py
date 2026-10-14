"""只有 DeepSeek 的电脑上的聊天页（2026-10-14，用户同学的 Windows）：菜单列 DeepSeek 的模型、没有 Effort；
深挖由 DeepSeek 自己调 look 工具查（llm.deepseek_agent）；look 命令按列表传参，写上「python look.py」前缀也认。"""
import _env  # noqa: F401
import json
import unittest

import app as appmod
import llm
import qa
from test_cards_qa import seed

H = {'Origin': 'http://127.0.0.1:5055'}


class DeepSeekChat(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        self.saved = (llm.claude_on, llm.cheap_on, llm.gemini_ready, llm._cheap_routes, llm.RETRY_WAIT, qa.run_look)
        llm.claude_on = lambda: False
        llm.cheap_on = lambda: True
        llm.gemini_ready = lambda: False
        llm.RETRY_WAIT = 0
        llm._cheap_routes = lambda all_routes=False: [('deepseek', 'http://x', 'k', 'deepseek-v4-flash', {})]
        self.bodies, self.ran = [], []

    def tearDown(self):
        (llm.claude_on, llm.cheap_on, llm.gemini_ready, llm._cheap_routes, llm.RETRY_WAIT, qa.run_look) = self.saved

    def replies(self, *msgs):
        import requests
        msgs = list(msgs)

        class R:
            status_code = 200
            text = ''

            def __init__(self, m):
                self.m = m

            def json(self):
                return {'choices': [{'message': self.m}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        saved = requests.post
        requests.post = lambda url, **k: (self.bodies.append(json.loads(json.dumps(k['json']))), R(msgs.pop(0)))[1]
        self.addCleanup(setattr, requests, 'post', saved)

    def test_menu_is_deepseek(self):
        page = appmod.app.test_client().get('/chat').get_data(as_text=True)
        self.assertIn('DeepSeek V4 Flash', page)
        self.assertNotIn('Opus 5.5', page)
        self.assertNotIn('<span class="ch-mi-t">Effort</span>', page)
        c = appmod.app.test_client()
        self.assertEqual(c.post('/api/qa/ask', json={'question': 'x', 'model': 'claude-opus-5-5'}, headers=H).status_code, 400)

    def test_deep_mode_uses_tools(self):
        tool_call = {'role': 'assistant', 'content': '', 'reasoning_content': '想一想',
                     'tool_calls': [{'id': 't1', 'type': 'function', 'function': {'name': 'look', 'arguments': json.dumps({'command': 'cards ED'})}}]}
        self.replies(tool_call, {'role': 'assistant', 'content': '你 ED 交的是 MIT [[m:m1]]。'})
        qa.run_look = lambda args: (self.ran.append(args['command']), '[m:m1] 我决定 ED 交 MIT')[1]
        steps = []
        r = qa.answer_deep('我 ED 交的哪？', progress=steps.append, on_text=lambda x: None)
        self.assertEqual(self.ran, ['cards ED'])
        self.assertIn('MIT', r['answer'])
        self.assertEqual([c['id'] for c in r['cites']], ['m:m1'])
        first, second = self.bodies
        self.assertEqual(first['tools'][0]['function']['name'], 'look')
        self.assertIn('调用工具 look', first['messages'][1]['content'])
        self.assertNotIn('Bash', first['messages'][1]['content'])
        self.assertEqual(second['messages'][-1], {'role': 'tool', 'tool_call_id': 't1', 'content': '[m:m1] 我决定 ED 交 MIT'})
        self.assertNotIn('reasoning_content', second['messages'][-2])          # 思考过程不回传

    def test_run_look_strips_prefix(self):
        qa.run_look = self.saved[-1]
        out = qa.run_look({'command': 'python look.py stats'})
        self.assertIn('消息', out)
        self.assertIn('没认出命令', qa.run_look({'command': 'rm -rf /'}))


if __name__ == '__main__':
    unittest.main()
