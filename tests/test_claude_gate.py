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
        sqlite3.connect(claude_gate.path(), isolation_level=None).execute('DELETE FROM pauses')
        self.assertTrue(llm.claude_on())
        self.fake({'is_error': True, 'result': 'Claude AI usage limit reached|%d' % (time.time() + 900)})
        self.assertEqual(llm.ask('写日记'), 'deepseek 回的')
        self.assertEqual(claude_gate.status()['pause']['claude'][2], 'ephemeris')   # 这边撞了：写进共用的

    def test_old_pause_table_still_counts(self):
        """没升级的应用还写老的 pause 表（只有 Claude 一行）：照样停 Claude，不停 GPT。"""
        c = claude_gate._db()
        c.execute('INSERT INTO pause (k, until, reason, app) VALUES (1, ?, ?, ?)', (time.time() + 600, 'limit', 'moye'))
        c.close()
        self.assertTrue(claude_gate.paused_until('claude'))
        self.assertFalse(claude_gate.paused_until('codex'))


class Codex(unittest.TestCase):
    """GPT（11-03）：codex exec 走同一个闸，provider 分开算；Claude 用不了才顶上，大批量（route='cheap'）不走它；要 EPHEMERIS_CODEX=on。"""

    def setUp(self):
        os.environ['CLAUDE_GATE'] = os.path.join(tempfile.mkdtemp(prefix='gate-'), 'gate.db')
        os.environ['EPHEMERIS_CODEX'] = 'on'
        self._cheap, self._cheap_on = llm._cheap, llm.cheap_on
        llm._cheap = lambda prompt, why=None, **kw: 'deepseek 回的'
        llm.cheap_on = lambda: True
        self.calls, self.held = [], []

    def tearDown(self):
        os.environ['CLAUDE_GATE'] = ''
        os.environ['EPHEMERIS_CODEX'] = 'off'
        llm.use_claude(None)
        llm.use_codex(None)
        llm._cheap, llm.cheap_on = self._cheap, self._cheap_on
        for k in ('claude', 'codex'):
            llm._paused.pop(k, None)

    def gpt(self, events, err=''):
        def run(args, stdin):
            self.calls.append((args, stdin))
            self.held.append(claude_gate.status()['slots'])
            return events, err
        llm.use_codex(run)

    OK = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'GPT 回的'}},
          {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 5}}]

    def test_after_claude_and_before_deepseek(self):
        self.gpt(self.OK)
        llm.use_claude(lambda a, s: {'is_error': True, 'result': '断了'})
        self.assertEqual(llm.ask('写日记', purpose='diary'), 'GPT 回的')
        args = self.calls[0][0]
        self.assertIn('--ephemeral', args)                     # 不留会话：不然会被当成他自己的 Codex 对话收进日记
        self.assertIn('read-only', args)
        self.assertEqual(self.held[0][0][4], 'codex')
        s = claude_gate.status()
        self.assertEqual({r[5] for r in s['5h']}, {'claude', 'codex'})
        self.assertEqual(llm.ask('做卡', route='cheap'), 'deepseek 回的')   # 大批量不动 GPT 额度
        self.assertEqual(len(self.calls), 1)

    def test_off_by_default(self):
        os.environ['EPHEMERIS_CODEX'] = 'off'
        self.gpt(self.OK)
        self.assertFalse(llm.codex_on())
        self.assertEqual(llm.ask('写日记'), 'deepseek 回的')

    def test_limit_pauses_only_gpt(self):
        self.gpt([{'type': 'error', 'message': "You've hit your usage limit. Try again later."}])
        self.assertEqual(llm.ask('写日记'), 'deepseek 回的')
        self.assertTrue(claude_gate.paused_until('codex'))
        self.assertFalse(claude_gate.paused_until('claude'))
        self.assertFalse(llm.codex_on())

    def test_caps_are_per_provider(self):
        c = claude_gate._db()
        for _ in range(4):                                     # Claude 那边挤满了也不影响 GPT 的位子
            c.execute("INSERT INTO slots (app, purpose, prio, pid, started, provider) VALUES ('verbatim', 'x', 'batch', ?, ?, 'claude')",
                      (os.getpid(), time.time()))
        c.close()
        os.environ['CLAUDE_GATE_CAP'] = '4'
        try:
            self.gpt(self.OK)
            self.assertEqual(llm.codex('写', purpose='diary'), 'GPT 回的')
        finally:
            os.environ['CLAUDE_GATE_CAP'] = ''

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
