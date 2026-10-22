"""「问」写到一半可以停（2026-10-13，聊天页的停止按钮，学 claude.ai）：claude 进程当场关掉，已经写出来的那段留在对话里
（跟 claude.ai 一样，标「已停止」），不会因为「Claude 失败了」再换别的模型从头写一遍。"""
import _env  # noqa: F401
import json
import threading
import time
import unittest

import cards
import llm
import qa
from test_cards_qa import deltas, out, seed


class Stop(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        llm.use(lambda p: out([{'m': 1, 'k': '决定', 'p': '大学申请', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'}]))
        cards.build_conv('c1')
        llm.use(None)
        self.cheap = []
        self._orig = llm._cheap, llm._gemini
        llm._cheap = lambda p, why=None, **kw: self.cheap.append(p) or '别的模型写的'
        llm._gemini = lambda p, model=None: self.cheap.append(p) or '别的模型写的'
        plan = json.dumps({'q': '我 ED 交哪', 'keywords': ['MIT'], 'since': None, 'until': None})

        def run(args, stdin):
            if 'stream-json' not in args:
                return {'result': plan}
            if '--allowedTools' in args:
                return {'result': '你决定 ED 交 MIT [[c:m1·1]]。后面还有', 'session_id': 's1',
                        'events': deltas('你决定 ED 交 MIT [[c:m1·1]]。', '后面还有很多')}
            return {'result': '你决定 ED 交 MIT [c1]。后面', 'events': deltas('你决定 ED 交 MIT [c1]。', '后面还有很多')}
        llm.use_claude(run)

    def tearDown(self):
        llm.use_claude(None)
        llm._cheap, llm._gemini = self._orig

    def stop_after_first(self):
        n = [0]

        def on_text(x):
            if x is None:                                    # 「一段新回复开始」不算字
                return
            n[0] += 1
            if n[0] == 1:
                llm.cancel(threading.get_ident())          # 写出第一段之后用户点了停止
        return on_text

    def test_quick_stop_keeps_partial(self):
        r = qa.answer('我早申交的哪？', on_text=self.stop_after_first())
        self.assertTrue(r['stopped'])
        self.assertEqual(r['answer'], '你决定 ED 交 MIT [[m1·1]]。')
        self.assertEqual(qa.history()[-1]['text'], '你决定 ED 交 MIT [[m1·1]]。')
        self.assertEqual(self.cheap, [])                      # 没换别的模型重写

    def test_deep_stop_keeps_partial(self):
        r = qa.answer_deep('我早申交的哪？', on_text=self.stop_after_first())
        self.assertTrue(r['stopped'])
        self.assertIn('ED 交 MIT', r['answer'])
        self.assertEqual(len(r['cites']), 1)
        self.assertEqual(self.cheap, [])

    def test_stop_endpoint_cancels_running_job(self):
        started = threading.Event()
        release = threading.Event()

        def run(args, stdin):
            if 'stream-json' not in args:
                return {'result': json.dumps({'q': 'x', 'keywords': ['MIT']})}
            started.set()
            release.wait(2)
            return {'result': '写了一半', 'events': deltas('写了一半')}
        llm.use_claude(run)
        qa.start_ask('我早申交的哪？')
        started.wait(2)
        self.assertTrue(qa.stop())
        release.set()
        for _ in range(50):
            if not qa.live()['running']:
                break
            time.sleep(0.05)
        live = qa.live()
        self.assertFalse(live['running'])
        self.assertTrue(live['result']['stopped'])
        self.assertFalse(qa.stop())                           # 没在跑：没什么可停

    def test_two_threads_answer_at_once(self):
        """10-22——不同对话各答各的，停一段不碰另一段。"""
        started, release = threading.Semaphore(0), threading.Event()

        def run(args, stdin):
            if 'stream-json' not in args:
                return {'result': json.dumps({'q': 'x', 'keywords': ['MIT']})}
            started.release()
            release.wait(2)
            return {'result': '写好了', 'events': deltas('写好了')}
        llm.use_claude(run)
        a, b = qa.new_thread(), qa.new_thread()
        self.assertTrue(qa.start_ask('我早申交的哪？', thread=a))
        self.assertTrue(qa.start_ask('我 ED 交的哪？', thread=b))
        self.assertFalse(qa.start_ask('再问一句', thread=a))    # 同一段还是一次一个
        self.assertTrue(started.acquire(timeout=2) and started.acquire(timeout=2))
        self.assertEqual(qa.busy(), sorted([a, b]))
        self.assertTrue(qa.stop(a))
        release.set()
        for _ in range(50):
            if not qa.busy():
                break
            time.sleep(0.05)
        self.assertEqual(qa.busy(), [])
        self.assertTrue(qa.live(a)['result']['stopped'])
        self.assertFalse(qa.live(b)['result'].get('stopped'))
        self.assertEqual((qa.live(a)['thread'], qa.live(b)['thread']), (a, b))


if __name__ == '__main__':
    unittest.main()
