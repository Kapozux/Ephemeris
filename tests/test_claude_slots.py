"""同时问好几个（10-30 用户：开了 5 个问，后两个一直不动）：问不排在做卡那几个位子后面；做卡还是最多 CLAUDE_PARALLEL 个。"""
import _env  # noqa: F401
import threading
import unittest

import llm


class Slots(unittest.TestCase):
    def tearDown(self):
        llm.use_claude(None)

    def together(self, n, purpose):
        """n 个同时调；全部同时进了 claude 才算放行（等不齐就是被限住了）。返回同时进去的最多几个。"""
        inside, most, lock = [0], [0], threading.Lock()
        gate = threading.Barrier(n, timeout=2)

        def run(args, stdin):
            with lock:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            with lock:
                inside[0] -= 1
            return {'result': 'ok', 'session_id': 's', 'events': []}
        llm.use_claude(run)
        ts = [threading.Thread(target=llm.claude_agent, args=('问', 'look'), kwargs={'purpose': purpose}) for _ in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return most[0]

    def test_five_questions_at_once(self):
        self.assertEqual(self.together(5, 'qa-deep'), 5)

    def test_batch_still_capped(self):
        self.assertEqual(self.together(llm.CLAUDE_PARALLEL + 2, 'cards'), llm.CLAUDE_PARALLEL)


if __name__ == '__main__':
    unittest.main()
