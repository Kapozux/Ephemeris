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

class Week(unittest.TestCase):
    """周额度（10-31）：设了 CLAUDE_GATE_WEEK_KEEP 就看 Anthropic 那边的「7 天用了几成」，批量 / 普通先让，交互留到最后；十分钟只探一次。"""

    def setUp(self):
        os.environ['CLAUDE_GATE'] = os.path.join(tempfile.mkdtemp(prefix='gate-'), 'gate.db')
        os.environ['CLAUDE_GATE_WEEK_KEEP'] = '0.3'
        self.saved, self.probes, self.util = claude_gate._probe, [], 0.5
        claude_gate._probe = lambda: self.probes.append(1) or (self.util, time.time() + 3600)

    def tearDown(self):
        os.environ['CLAUDE_GATE'] = ''
        os.environ.pop('CLAUDE_GATE_WEEK_KEEP', None)
        claude_gate._probe = self.saved

    def take(self, prio):
        with claude_gate.slot('t', 'x', prio, wait=0):
            return True

    def test_batch_yields_first_live_last(self):
        self.assertTrue(self.take('batch'))
        self.assertTrue(self.take('normal'))
        self.assertEqual(len(self.probes), 1)                      # 十分钟内用存着的
        sqlite3.connect(claude_gate.path(), isolation_level=None).execute("DELETE FROM meta WHERE k = 'week_ts'")
        self.util = 0.75
        with self.assertRaises(claude_gate.Paused):
            self.take('batch')
        self.assertTrue(self.take('live'))
        sqlite3.connect(claude_gate.path(), isolation_level=None).execute("DELETE FROM meta WHERE k = 'week_ts'")
        self.util = 0.95
        with self.assertRaises(claude_gate.Paused):
            self.take('live')

    def test_unknown_or_off_lets_through(self):
        claude_gate._probe = lambda: None
        self.assertTrue(self.take('batch'))
        os.environ.pop('CLAUDE_GATE_WEEK_KEEP')
        self.util = 0.99
        claude_gate._probe = lambda: (0.99, time.time() + 3600)
        self.assertTrue(self.take('batch'))


if __name__ == '__main__':
    unittest.main()
