"""Claude 流式调用的看门（2026-10-17：「问」查一个大问题，查了 10 分钟还在干活，被总时长掐掉）：
一直有输出就不掐；超过 idle 秒一行都没有才算卡住；总时长另有上限。用一个假进程，不碰真的 claude。"""
import _env  # noqa: F401
import subprocess
import sys
import time
import unittest

import llm


def fake(script):
    return subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


BUSY = """
import json, time, sys
for i in range(6):
    print(json.dumps({'type': 'assistant', 'message': {'content': []}})); sys.stdout.flush(); time.sleep(0.5)
print(json.dumps({'type': 'result', 'result': '写完了', 'is_error': False}))
"""


class Watch(unittest.TestCase):
    def test_busy_run_longer_than_idle_is_kept(self):
        d = llm._claude_read(fake(BUSY), lambda ev: None, time.time(), timeout=60, idle=2)    # 跑 3 秒，但一直有动静
        self.assertEqual(d['result'], '写完了')

    def test_silent_run_is_killed(self):
        t = time.time()
        d = llm._claude_read(fake('import time; time.sleep(30)'), lambda ev: None, t, timeout=60, idle=2)
        self.assertTrue(d['is_error'])
        self.assertIn('没有动静', d['result'])
        self.assertLess(time.time() - t, 10)

    def test_total_cap_still_applies(self):
        d = llm._claude_read(fake(BUSY), lambda ev: None, time.time(), timeout=1, idle=30)
        self.assertIn('还没写完', d['result'])


if __name__ == '__main__':
    unittest.main()
