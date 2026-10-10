"""第一次用（2026-10-10）：「开始用」页什么时候出现、填的 key 怎么写进 .env。"""
import _env  # noqa: F401
import os
import shutil
import stat
import tempfile
import unittest

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
        setup.save({'GEMINI_API_KEY': ' new\n', 'DIARY_NAME': '妈妈', 'EVIL': 'x'})
        with open(setup.ENV) as f:
            text = f.read()
        self.assertEqual(text, '# 注释\nNOTION_TOKEN=abc\nGEMINI_API_KEY=new\nDIARY_NAME=妈妈\n')
        self.assertEqual(stat.S_IMODE(os.stat(setup.ENV).st_mode), 0o600)

    def test_status_never_returns_the_values(self):
        setup.save({'CLAUDE_SESSION_KEY': 'sk-ant-secret'})
        self.assertNotIn('sk-ant-secret', repr(setup.status()))


if __name__ == '__main__':
    unittest.main()
