"""三个应用共用 Claude 额度的分配器（claude_gate，2026-10-13）：Ephemeris 每次起 claude 前占位子（问 = live、做卡 = batch、其余 normal），
用完记一行共用的账；别的应用撞了上限这边也停；这边撞了写进共用的 pause。"""
import _env  # noqa: F401
import os
import sqlite3
import tempfile
import time
import unittest

import claude_gate
import llm


class Gate(unittest.TestCase):
    def setUp(self):
        os.environ['CLAUDE_GATE'] = os.path.join(tempfile.mkdtemp(prefix='gate-'), 'gate.db')
        self.held = []
        self._cheap, self._cheap_on = llm._cheap, llm.cheap_on
        llm._cheap = lambda prompt, why=None, **kw: 'deepseek 回的'
        llm.cheap_on = lambda: True                   # 别看这台电脑旁边有没有 Verbatim 的 key

    def tearDown(self):
        os.environ['CLAUDE_GATE'] = ''
        llm.use_claude(None)
        llm._cheap, llm.cheap_on = self._cheap, self._cheap_on
        llm._paused.pop('claude', None)

    def fake(self, reply):
        def run(args, stdin):
            self.held.append(claude_gate.status()['slots'])
            return reply
        llm.use_claude(run)

    def test_slot_prio_and_ledger(self):
        self.fake({'result': '好', 'total_cost_usd': 0.02, 'usage': {'input_tokens': 9, 'output_tokens': 1}})
        llm.claude('问一句', purpose='qa')
        llm.claude('做卡', purpose='cards')
        llm.claude('写日记', purpose='diary')
        self.assertEqual([h[0][:3] for h in self.held],
                         [('ephemeris', 'qa', 'live'), ('ephemeris', 'cards', 'batch'), ('ephemeris', 'diary', 'normal')])
        s = claude_gate.status()
        self.assertEqual(s['slots'], [])
        self.assertEqual(s['5h'][0][:3], ('ephemeris', llm.CLAUDE_MODEL, 3))

    def test_shared_pause_both_ways(self):
        self.fake({'result': '好'})
        claude_gate.pause(time.time() + 600, 'usage limit reached', 'verbatim')
        self.assertFalse(llm.claude_on())                      # 别人撞了：这边也停
        sqlite3.connect(claude_gate.path(), isolation_level=None).execute('DELETE FROM pause')
        self.assertTrue(llm.claude_on())
        self.fake({'is_error': True, 'result': 'Claude AI usage limit reached|%d' % (time.time() + 900)})
        self.assertEqual(llm.ask('写日记'), 'deepseek 回的')
        self.assertEqual(claude_gate.status()['pause'][2], 'ephemeris')   # 这边撞了：写进共用的


if __name__ == '__main__':
    unittest.main()
