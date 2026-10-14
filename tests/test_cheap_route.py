"""省钱路由（2026-10-12）：不涉政的走 DeepSeek，涉政的只走 Gemini；DeepSeek 失败回 Gemini；提示词模板里不能有时政词。"""
import _env  # noqa: F401
import importlib
import unittest

import llm

MODULES = ('diary', 'cards', 'qa', 'reflect_chat', 'conv_review', 'checkin', 'ledger', 'mood', 'daily', 'journal')


class Route(unittest.TestCase):
    def setUp(self):
        self.saved = (llm._cheap_routes, llm._gemini, llm._override, llm.gemini_ready)
        llm._override = None
        llm.gemini_ready = lambda: True               # 这一组测的是「有 Gemini 可退」的电脑
        self.sent = []
        llm._gemini = lambda p, m=None: (self.sent.append('gemini'), 'G')[1]

    def tearDown(self):
        llm._cheap_routes, llm._gemini, llm._override, llm.gemini_ready = self.saved

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


class DeepSeekOnly(unittest.TestCase):
    """只有 DeepSeek key 的电脑（2026-10-14，用户同学的 Windows）：没有 Gemini 可退，涉政的也给 DeepSeek；
    限流 / 断线多重试几次；要 JSON 对象时开 json_object；固定的系统提示放最前（上下文缓存按前缀命中）；每次记进 api_usage。"""
    def setUp(self):
        import store
        store.run('DELETE FROM api_usage')
        self.saved = (llm._cheap_routes, llm._gemini, llm._override, llm.gemini_ready, llm.RETRY_WAIT)
        llm._override = None
        llm.gemini_ready = lambda: False
        llm.RETRY_WAIT = 0
        llm._gemini = lambda p, m=None: self.fail('没有 Gemini，不该走到它')
        llm._cheap_routes = lambda all_routes=False: [('deepseek', 'http://x', 'k', 'deepseek-v4-flash', {})]
        self.bodies = []

    def tearDown(self):
        llm._cheap_routes, llm._gemini, llm._override, llm.gemini_ready, llm.RETRY_WAIT = self.saved
        llm._paused.pop('deepseek', None)

    def route(self, *codes, text='D', usage=None):
        import requests
        codes = list(codes)

        class R:
            def __init__(self, code):
                self.status_code, self.text = code, 'busy' if code != 200 else ''

            def json(self):
                return {'choices': [{'message': {'content': text}}],
                        'usage': usage or {'prompt_tokens': 1000, 'completion_tokens': 100, 'prompt_cache_hit_tokens': 800}}
        saved = requests.post
        requests.post = lambda url, **k: (self.bodies.append(k['json']), R(codes.pop(0) if codes else 200))[1]
        self.addCleanup(setattr, requests, 'post', saved)

    def test_political_goes_to_deepseek(self):
        self.route(200)
        self.assertEqual(llm.ask('聊聊共产党和文革那段历史'), 'D')
        self.assertEqual(len(self.bodies), 1)

    def test_retries_rate_limit(self):
        self.route(429, 503, 200)
        self.assertEqual(llm.ask('帮我看看这道题', fallback=False), 'D')
        self.assertEqual(len(self.bodies), 3)

    def test_gives_up_with_reason(self):
        self.route(*([429] * 10))
        self.assertIsNone(llm.ask('帮我看看这道题'))
        self.assertEqual(len(self.bodies), llm.CHEAP_RETRIES)
        self.assertIn('DeepSeek 这次没拿到', llm.status()['last_error'])

    def test_json_mode_and_cache_prefix(self):
        self.route(200, text='{"a": 1}')
        self.assertEqual(llm.ask_json('只输出 JSON 对象：{"a": 数字}'), {'a': 1})
        b = self.bodies[0]
        self.assertEqual(b['response_format'], {'type': 'json_object'})
        self.assertEqual(b['messages'][0], {'role': 'system', 'content': llm.CHEAP_SYSTEM})

    def test_usage_recorded_with_cache_discount(self):
        import store
        self.route(200)
        llm.ask('帮我看看这道题', purpose='cards')
        r = store.one('SELECT route, model, purpose, input, cache_hit, usd FROM api_usage')
        self.assertEqual((r['route'], r['purpose'], r['input'], r['cache_hit']), ('deepseek', 'cards', 1000, 800))
        self.assertAlmostEqual(r['usd'], (200 * 0.14 + 800 * 0.014 + 100 * 0.28) / 1e6)
        self.assertEqual(llm.api_week()['calls'], 1)

    def test_picked_model_used(self):
        self.route(200)
        with llm.using('deepseek-reasoner'):
            llm.ask_json('只输出 JSON 对象', want=dict)
        self.assertEqual(self.bodies[0]['model'], 'deepseek-reasoner')
        self.assertNotIn('response_format', self.bodies[0])           # reasoner 不开 json_object

    def test_check_and_menu_without_claude_or_gemini(self):
        saved = llm.claude_on
        llm.claude_on = lambda: False
        self.addCleanup(setattr, llm, 'claude_on', saved)
        self.assertIsNone(llm.check())                    # 有 DeepSeek 就算能用，不报「没有 Gemini key」
        self.assertEqual(llm.chat_backend(), 'deepseek')
        self.assertEqual(llm.choices()[0][0], llm.deepseek_model())
        self.assertFalse([m for m, _ in llm.choices() if m.startswith('claude')])
