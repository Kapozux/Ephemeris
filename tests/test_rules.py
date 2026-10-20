import _env  # noqa: F401
import unittest

import claude_sync
import reflect_chat
import slice as sl


class Pasted(unittest.TestCase):
    def test_short_messages_are_typed(self):
        self.assertFalse(sl.looks_pasted('今天好累啊'))

    def test_long_prose_without_markers_is_typed(self):
        self.assertFalse(sl.looks_pasted('我今天想了很久' * 150))      # 1050 字，没换行没引号

    def test_subtitles_code_and_quotes_are_pasted(self):
        self.assertTrue(sl.looks_pasted('[00:00:02] 你好\n' * 80))
        self.assertTrue(sl.looks_pasted('def f():\n    return 1\n' + 'x' * 900))
        self.assertTrue(sl.looks_pasted('“既然你要那个味' + '啊' * 900))

    def test_compact_keeps_head_and_tail(self):
        t = '你看看这个：' + '材料' * 3000 + '你觉得呢？'
        out = sl.compact(t)
        self.assertTrue(out.startswith('你看看这个'))
        self.assertTrue(out.endswith('你觉得呢？'))
        self.assertIn('已省略', out)


class Quotes(unittest.TestCase):
    VOICE = '- 怎么都不对\n- 你就给我逻辑/中文 说A咋修改吧'

    def test_real_quote_passes(self):
        self.assertEqual(reflect_chat._bad_quotes('你会说「怎么都不对」。', self.VOICE), [])

    def test_invented_quote_is_caught(self):
        # 2026-09-30：模型把 prompt 里的示例句当成他的原话引用
        self.assertEqual(reflect_chat._bad_quotes('你会「改之前先确认方向对不对」。', self.VOICE),
                         ['改之前先确认方向对不对'])

    def test_drop_only_the_bad_sentence(self):
        text = '主线是申请。你会说「怎么都不对」。你也会「编的话」。心情很差。'
        self.assertEqual(reflect_chat._drop_sentences(text, ['编的话']), '主线是申请。你会说「怎么都不对」。心情很差。')


class SyncErrors(unittest.TestCase):
    def test_expired_login(self):
        msg = claude_sync._explain({'status': 403, 'body': '{"error":{"details":{"error_code":"account_session_invalid"}}}'})
        self.assertIn('sessionKey', msg)

    def test_cloudflare(self):
        self.assertIn('Cloudflare', claude_sync._explain({'status': 403, 'body': '<html><title>Just a moment...</title>'}))


if __name__ == '__main__':
    unittest.main()
