"""接不同模型（11-03，照 Khoj / LobeChat 的「接口 + 模型清单」和 Aider 的按角色分模型）：
填了 key 的几家都进写东西的路，指定了角色的排最前；「问」在没有 Claude 时走指定的 ASK；向量能换成开源的；不认识的模型不替人判断。
key、地址全是假的，不发真请求。"""
import _env  # noqa: F401
import os
import unittest
from unittest import mock

import llm
import providers

FAKE = {'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': 'k-ds', 'OPENROUTER_API_KEY': '', 'SILICONFLOW_API_KEY': 'k-sf',
        'MOONSHOT_API_KEY': '', 'ZHIPU_API_KEY': '', 'TOGETHER_API_KEY': '', 'EPHEMERIS_CUSTOM_API_KEY': '',
        'EPHEMERIS_CUSTOM_BASE_URL': '', 'EPHEMERIS_MODEL_TEXT': '', 'EPHEMERIS_MODEL_ASK': '', 'EPHEMERIS_MODEL_EMBED': ''}


def env(**over):
    """只用这里给的值，不读本机 .env（index.env 遇到空串会落回 .env，所以直接换掉 env 本身）。"""
    vals = {**FAKE, **over}
    real = providers.idx.env

    def fake(k, default=''):
        if k in vals:
            return vals[k]
        return real(k, default)
    return mock.patch.multiple(providers.idx, env=fake), mock.patch.object(llm, 'key', lambda name, alt=None: vals.get(name, ''))


class Providers(unittest.TestCase):
    def run_with(self, fn, **over):
        a, b = env(**over)
        with a, b:
            return fn()

    def test_configured_follow_keys(self):
        names = self.run_with(lambda: [n for n, _, _ in providers.configured()])
        self.assertEqual(names, ['deepseek', 'siliconflow'])

    def test_text_role_goes_first(self):
        r = self.run_with(providers.routes, EPHEMERIS_MODEL_TEXT='siliconflow:Qwen/Qwen3-235B-A22B')
        self.assertEqual([(x[0], x[3]) for x in r], [('siliconflow', 'Qwen/Qwen3-235B-A22B'), ('deepseek', 'deepseek-v4-flash')])
        self.assertEqual(r[0][1], 'https://api.siliconflow.cn/v1')

    def test_picked_text_always_first(self):
        """指定了写东西的模型：连着几次都先走它（以前几条路轮流先上）。"""
        tried = []

        def post(route, prompt, json_mode=False, purpose=''):
            tried.append(route[0])
            return '好'
        a, b = env(EPHEMERIS_MODEL_TEXT='siliconflow:Qwen/Qwen3-235B-A22B')
        with a, b, mock.patch.object(llm, '_cheap_post', post):
            for _ in range(3):
                llm._cheap('写一句')
        self.assertEqual(tried, ['siliconflow'] * 3)

    def test_bad_pick_ignored(self):
        for raw in ('nope:m', 'moonshot:kimi', 'siliconflow:', 'siliconflow'):    # 不认识的家 / 没 key / 没写模型
            with self.subTest(raw=raw):
                self.assertIsNone(self.run_with(lambda: providers.pick('TEXT'), EPHEMERIS_MODEL_TEXT=raw))

    def test_custom_needs_base(self):
        names = self.run_with(lambda: [n for n, _, _ in providers.configured()], EPHEMERIS_CUSTOM_API_KEY='k-c')
        self.assertNotIn('custom', names)
        r = self.run_with(providers.routes, EPHEMERIS_CUSTOM_API_KEY='k-c', EPHEMERIS_CUSTOM_BASE_URL='http://gpu.example:8000/v1/',
                          EPHEMERIS_MODEL_TEXT='custom:my-model')
        self.assertEqual((r[0][0], r[0][1], r[0][3]), ('custom', 'http://gpu.example:8000/v1', 'my-model'))   # 末尾的 / 去掉
        self.assertNotIn('custom', [x[0] for x in r[1:]])        # 自定义没有默认模型：指定了才用

    def test_ask_route(self):
        self.assertEqual(self.run_with(providers.ask_route, EPHEMERIS_MODEL_ASK='siliconflow:moonshotai/Kimi-K2')[3], 'moonshotai/Kimi-K2')
        self.assertEqual(self.run_with(providers.ask_route)[0], 'deepseek')    # 没指定：跟写东西同一条

    def test_info(self):
        self.assertTrue(providers.info('moonshotai/Kimi-K2-Instruct')['tools'])
        self.assertFalse(providers.info('deepseek-reasoner')['tools'])
        self.assertIsNone(providers.info('some-new-model-7b'))                  # 不认识：不猜

    def test_summary_has_no_keys(self):
        s = self.run_with(providers.summary)
        self.assertNotIn('k-ds', str(s))
        self.assertTrue(next(p for p in s['providers'] if p['name'] == 'siliconflow')['ready'])

    def test_agent_uses_ask_model(self):
        """没有 Claude 时「问」自己查：走指定的 ASK 那家、那个模型。"""
        sent = []

        class R:
            status_code = 200

            def json(self):
                return {'choices': [{'message': {'content': '答好了'}}], 'usage': {}}

        def post(url, **k):
            sent.append((url, k['json']['model'], k['headers']['Authorization']))
            return R()
        a, b = env(EPHEMERIS_MODEL_ASK='siliconflow:zai-org/GLM-4.6')
        with a, b, mock.patch('requests.post', post):
            out = llm.deepseek_agent('问', {'name': 'look', 'parameters': {}}, lambda x: '')
        self.assertEqual(out, '答好了')
        self.assertEqual(sent[0], ('https://api.siliconflow.cn/v1/chat/completions', 'zai-org/GLM-4.6', 'Bearer k-sf'))

    def test_embed_model_name_changes(self):
        """换了向量模型，记的名字跟着变：旧向量不跟新的比。"""
        name = self.run_with(llm.embed_model, EPHEMERIS_MODEL_EMBED='siliconflow:BAAI/bge-m3')
        self.assertEqual(name, 'siliconflow:BAAI/bge-m3')
        self.assertTrue(self.run_with(llm.embed_ready, EPHEMERIS_MODEL_EMBED='siliconflow:BAAI/bge-m3'))

    def test_env_names_for_settings(self):
        names = providers.env_names()
        for n in ('SILICONFLOW_API_KEY', 'EPHEMERIS_CUSTOM_BASE_URL', 'EPHEMERIS_MODEL_ASK', 'EPHEMERIS_MODEL_EMBED'):
            self.assertIn(n, names)


if __name__ == '__main__':
    unittest.main()
