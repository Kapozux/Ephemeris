"""省钱路由（2026-10-12）：不涉政的走 DeepSeek，涉政的只走 Gemini；DeepSeek 失败回 Gemini；提示词模板里不能有时政词。"""
import _env  # noqa: F401
import importlib
import unittest

import llm

MODULES = ('diary', 'cards', 'qa', 'reflect_chat', 'conv_review', 'checkin', 'ledger', 'mood', 'daily', 'journal')


class Route(unittest.TestCase):
    def setUp(self):
        self.saved = (llm._cheap_routes, llm._gemini, llm._override)
        llm._override = None
        self.sent = []
        llm._gemini = lambda p, m=None: (self.sent.append('gemini'), 'G')[1]

    def tearDown(self):
        llm._cheap_routes, llm._gemini, llm._override = self.saved

    def fake_route(self, ok=True):
        import requests

        class R:
            status_code = 200 if ok else 500
            text = 'boom'

            def json(self):
                return {'choices': [{'message': {'content': 'D'}}], 'usage': {}}
        saved = requests.post
        requests.post = lambda *a, **k: (self.sent.append('deepseek'), R())[1]
        self.addCleanup(setattr, requests, 'post', saved)
        llm._cheap_routes = lambda all_routes=False: [('fake', 'http://x', 'k', 'm', {})]

    def test_ordinary_goes_to_deepseek(self):
        self.fake_route()
        self.assertEqual(llm.ask('帮我看看这道数学题'), 'D')
        self.assertEqual(self.sent, ['deepseek'])

    def test_political_stays_on_gemini(self):
        self.fake_route()
        self.assertEqual(llm.ask('聊聊共产党和文革那段历史'), 'G')
        self.assertEqual(self.sent, ['gemini'])

    def test_one_political_word_is_not_enough(self):
        self.fake_route()
        self.assertEqual(llm.ask('我在上海，今天聊了一下新疆的棉花'), 'D')

    def test_blogger_names_do_not_count(self):
        """立党、马督工是 Verbatim 分析的博主，聊开发时天天提，不算涉政。"""
        self.fake_route()
        self.assertEqual(llm.ask('Verbatim 跑了立党 520 期、马督工 300 期，立党那条线的卡'), 'D')

    def test_deepseek_failure_falls_back(self):
        self.fake_route(ok=False)
        self.assertEqual(llm.ask('帮我看看这道题'), 'G')
        self.assertEqual(self.sent, ['deepseek', 'gemini'])

    def test_no_fallback_means_failure_not_gemini(self):
        """证据卡大批量：DeepSeek 限流就算失败，不悄悄回 Gemini（贵 3～5 倍）。涉政的照样走 Gemini。"""
        self.fake_route(ok=False)
        self.assertIsNone(llm.ask('帮我看看这道题', fallback=False))
        self.assertEqual(self.sent, ['deepseek'])
        self.assertEqual(llm.ask('聊聊共产党和文革那段历史', fallback=False), 'G')

    def test_moderation_block_goes_to_gemini_even_without_fallback(self):
        import requests

        class R:
            status_code = 400
            text = '{"error":{"code":"data_inspection_failed"}}'
        saved = requests.post
        requests.post = lambda *a, **k: (self.sent.append('deepseek'), R())[1]
        self.addCleanup(setattr, requests, 'post', saved)
        llm._cheap_routes = lambda all_routes=False: [('fake', 'http://x', 'k', 'm', {})]
        self.assertEqual(llm.ask('词表里没有、审核却拦的话', fallback=False), 'G')
        self.assertEqual(self.sent, ['deepseek', 'gemini'])

    def test_all_routes_paused_is_failure_not_gemini(self):
        """10-12 的坑：两条路都被停了，以前当成「没有 DeepSeek」直接退到 Gemini。现在 fallback=False 就是失败。"""
        llm._cheap_routes = lambda all_routes=False: [('fake', 'http://x', 'k', 'm', {})] if all_routes else []
        self.assertIsNone(llm.ask('帮我看看这道题', fallback=False))
        self.assertEqual(self.sent, [])

    def test_network_glitch_does_not_pause(self):
        llm._paused.clear()
        llm._pause('x', "HTTPSConnectionPool: SSLError(SSLEOFError(8, 'EOF occurred'))")
        llm._pause('x', 'Expecting value: line 443 column 1')
        self.assertNotIn('x', llm._paused)
        llm._pause('x', '400: Access denied, please make sure your account is in good standing')
        self.assertIn('x', llm._paused)
        llm._paused.clear()

    def test_prompt_templates_have_no_political_words(self):
        """模板里要是有词表里的词，每次调用都会被判成涉政（Verbatim 吃过这个亏）。"""
        for name in MODULES:
            mod = importlib.import_module(name)
            for attr, v in vars(mod).items():
                if attr.isupper() and isinstance(v, str) and len(v) > 40:
                    self.assertEqual(llm.political(v), [], f'{name}.{attr}')


if __name__ == '__main__':
    unittest.main()
