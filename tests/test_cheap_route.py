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
        llm._cheap_routes = lambda: [('fake', 'http://x', 'k', 'm', {})]

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

    def test_prompt_templates_have_no_political_words(self):
        """模板里要是有词表里的词，每次调用都会被判成涉政（Verbatim 吃过这个亏）。"""
        for name in MODULES:
            mod = importlib.import_module(name)
            for attr, v in vars(mod).items():
                if attr.isupper() and isinstance(v, str) and len(v) > 40:
                    self.assertEqual(llm.political(v), [], f'{name}.{attr}')


if __name__ == '__main__':
    unittest.main()
