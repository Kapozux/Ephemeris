"""第一次用（2026-10-10）：「开始用」页什么时候出现、填的 key 怎么写进 .env。"""
import _env  # noqa: F401
import os
import shutil
import stat
import tempfile
import unittest
import unittest.mock

import index as idx
import llm
import setup
import store


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-setup-')
        self.saved = setup.ENV, llm.check
        setup.ENV = os.path.join(self.tmp, '.env')
        with store.tx() as db:
            db.execute('DELETE FROM messages')

    def tearDown(self):
        setup.ENV, llm.check = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shown_until_key_and_first_chats(self):
        llm.check = lambda: '没有 Gemini key'
        self.assertTrue(setup.status()['need'])
        llm.check = lambda: None
        self.assertTrue(setup.status()['need'])                         # 有 key 了，还没聊天
        store.run("INSERT INTO messages VALUES ('m', 'c', 'claude', '2026-03-03T10:00', '2026-03-03', '10:00', 'human', 'x', 1)")
        self.assertFalse(setup.status()['need'])

    def test_bad_gemini_key_is_not_saved(self):
        saved = setup.verify_gemini
        setup.verify_gemini = lambda k=None: '这个 key 不对'
        try:
            r = setup.save({'GEMINI_API_KEY': 'bad'})
        finally:
            setup.verify_gemini = saved
        self.assertEqual(r['gemini_error'], '这个 key 不对')
        self.assertFalse(os.path.exists(setup.ENV))

    def test_save_keeps_other_lines_and_only_known_fields(self):
        setup.verify_gemini, saved = (lambda k=None: None), setup.verify_gemini
        self.addCleanup(setattr, setup, 'verify_gemini', saved)
        with open(setup.ENV, 'w') as f:
            f.write('# 注释\nNOTION_TOKEN=abc\nGEMINI_API_KEY=old\n')
        setup.save({'GEMINI_API_KEY': ' new\n', 'DIARY_NAME': '小明', 'EVIL': 'x'})
        with open(setup.ENV) as f:
            text = f.read()
        self.assertEqual(text, '# 注释\nNOTION_TOKEN=abc\nGEMINI_API_KEY=new\nDIARY_NAME=小明\n')
        self.assertEqual(stat.S_IMODE(os.stat(setup.ENV).st_mode), 0o600)

    def test_language_only_known_and_takes_effect_now(self):
        """11-04 英文界面：只认 zh / en；Flask 启动时把 .env 读进了环境变量，存的时候一起改，不用重启。"""
        r = setup.save({'EPHEMERIS_LANG': 'fr'})
        self.assertEqual(r['key_error'], '不认识这个语言')
        self.assertFalse(os.path.exists(setup.ENV))
        with unittest.mock.patch.dict(os.environ, {'EPHEMERIS_LANG': 'zh'}):
            self.assertEqual(setup.save({'EPHEMERIS_LANG': 'en'})['lang'], 'en')
            self.assertEqual(idx.lang(), 'en')
        with unittest.mock.patch.dict(os.environ, {'EPHEMERIS_LANG': 'xx'}):
            self.assertEqual(idx.lang(), 'zh')                         # 不认识的当中文

    def test_model_writes_in_the_chosen_language(self):
        """中文界面提示词原样；英文界面末尾加一句写英文（心情词这些固定值照旧）。"""
        with unittest.mock.patch.dict(os.environ, {'EPHEMERIS_LANG': 'zh'}):
            self.assertEqual(idx.write_in(), '')
            self.assertEqual(idx.write_in(ask=True), '')
        with unittest.mock.patch.dict(os.environ, {'EPHEMERIS_LANG': 'en'}):
            self.assertIn('English', idx.write_in())
            self.assertIn('mood', idx.write_in())
            self.assertIn('question itself', idx.write_in(ask=True))

    def test_status_never_returns_the_values(self):
        setup.save({'CLAUDE_SESSION_KEY': 'sk-ant-secret'})
        self.assertNotIn('sk-ant-secret', repr(setup.status()))


class Providers(unittest.TestCase):
    """能挑一家模型（2026-10-14：同学的 Windows 只有 DeepSeek）：每家的 key 存之前先验；选的那家记进 EPHEMERIS_PROVIDER，
    选了别家就不碰本机的 Claude Code。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-setup-')
        self.saved = setup.ENV, setup.verify
        setup.ENV = os.path.join(self.tmp, '.env')
        self.asked = []

    def tearDown(self):
        setup.ENV, setup.verify = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop('EPHEMERIS_PROVIDER', None)

    def env_text(self):
        with open(setup.ENV, encoding='utf-8') as f:
            return f.read()

    def test_deepseek_key_verified_then_saved(self):
        setup.verify = lambda p, k: self.asked.append((p, k))
        setup.save({'DEEPSEEK_API_KEY': 'sk-good', 'EPHEMERIS_PROVIDER': 'deepseek'})
        self.assertEqual(self.asked, [('deepseek', 'sk-good')])
        self.assertIn('DEEPSEEK_API_KEY=sk-good\nEPHEMERIS_PROVIDER=deepseek\n', self.env_text())

    def test_bad_key_not_saved(self):
        setup.verify = lambda p, k: 'DeepSeek 说余额不足'
        r = setup.save({'DEEPSEEK_API_KEY': 'sk-broke'})
        self.assertEqual(r['key_error'], 'DeepSeek 说余额不足')
        self.assertFalse(os.path.exists(setup.ENV))

    def test_other_presets_verified_against_models(self):
        """11-03「模型」那栏：硅基流动这类的 key 存之前读一次它的 /models；自定义的用页面上一起填的地址。"""
        saved = setup.verify_preset
        try:
            setup.verify_preset = lambda name, k, base=None: self.asked.append((name, k, base)) or (None if k == 'good' else '无效')
            self.assertEqual(setup.save({'SILICONFLOW_API_KEY': 'bad'})['key_error'], '无效')
            self.assertFalse(os.path.exists(setup.ENV))
            setup.save({'EPHEMERIS_CUSTOM_API_KEY': 'good', 'EPHEMERIS_CUSTOM_BASE_URL': 'http://gpu.example/v1'})
            self.assertIn(('custom', 'good', 'http://gpu.example/v1'), self.asked)
            self.assertIn('EPHEMERIS_CUSTOM_API_KEY=good', self.env_text())
        finally:
            setup.verify_preset = saved

    def test_role_must_be_provider_colon_model(self):
        self.assertIn('key_error', setup.save({'EPHEMERIS_MODEL_ASK': 'kimi-k2'}))
        self.assertFalse(os.path.exists(setup.ENV))
        setup.save({'EPHEMERIS_MODEL_ASK': 'siliconflow:moonshotai/Kimi-K2'})
        self.assertIn('EPHEMERIS_MODEL_ASK=siliconflow:moonshotai/Kimi-K2', self.env_text())
        self.assertNotIn('key_error', setup.save({'EPHEMERIS_MODEL_ASK': ''}))        # 清空 = 不指定

    def test_unknown_provider_rejected(self):
        self.assertIn('key_error', setup.save({'EPHEMERIS_PROVIDER': 'skynet'}))
        self.assertFalse(os.path.exists(setup.ENV))

    def test_verify_maps_http_codes(self):
        import requests
        setup.verify = self.saved[1]

        class R:
            def __init__(self, code):
                self.status_code = code
        for code, want in ((200, None), (401, '无效'), (402, '余额不足')):
            with unittest.mock.patch.object(requests, 'get', lambda url, **k: R(code)):
                got = setup.verify('deepseek', 'sk-x')
            self.assertTrue(got is None if want is None else want in got, (code, got))

    def test_other_provider_turns_claude_off(self):
        llm.use_claude(lambda args, stdin: {'result': 'x'})
        self.addCleanup(llm.use_claude, None)
        self.assertTrue(llm.claude_on())
        os.environ['EPHEMERIS_PROVIDER'] = 'deepseek'
        self.assertFalse(llm.claude_on())
        os.environ['EPHEMERIS_PROVIDER'] = 'claude'
        self.assertTrue(llm.claude_on())

    def test_status_lists_providers(self):
        s = setup.status()
        self.assertEqual(set(s['providers']), set(setup.PROVIDERS))
        self.assertIn(s['provider'], setup.PROVIDERS + ('',))


if __name__ == '__main__':
    unittest.main()
