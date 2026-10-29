"""研究版（research/）：只许引用 research.CORE 里的 Ephemeris 模块；共用的 speech 判断对；指标算得对。"""
import _env  # noqa: F401
import ast
import os
import unittest

import research
import speech
from datetime import datetime
from zoneinfo import ZoneInfo
from research.measures import chat_first_person_ratio as fp, chat_mean_daily_overnight_ratio as night
from research.participant import Msg, Participant, Period, sessions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _imports(path):
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split('.')[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module.split('.')[0], node.lineno


class Boundary(unittest.TestCase):
    def test_only_core_modules(self):
        ephemeris = {f[:-3] for f in os.listdir(ROOT) if f.endswith('.py')}
        bad = []
        for d, _, files in os.walk(os.path.join(ROOT, 'research')):
            for f in files:
                if f.endswith('.py'):
                    path = os.path.join(d, f)
                    for mod, line in _imports(path):
                        if mod in ephemeris and mod not in research.CORE:
                            bad.append(f'{os.path.relpath(path, ROOT)}:{line} import {mod}')
        self.assertEqual(bad, [], '研究版只许引用 research.CORE；要用就先加进 CORE')


class Speech(unittest.TestCase):
    def test_typed(self):
        self.assertTrue(speech.typed('明天几点出发'))
        self.assertFalse(speech.typed('字' * (speech.MAXLEN + 1)))
        self.assertFalse(speech.typed('看这段\n```\nprint(1)\n```'))
        self.assertFalse(speech.typed('\n'.join('第一行 第二行 第三行 第四行 第五行 第六行'.split())))

    def test_window_spontaneous_and_echo(self):
        w = speech.Window()
        self.assertEqual(w.say('assistant', {'兜底', '方案'}), ({'兜底', '方案'}, set()))
        spont, echo = w.say('human', {'兜底', '晚饭'})
        self.assertEqual((spont, echo), ({'晚饭'}, {'兜底'}))

    def test_pasted_counts_as_context(self):
        w = speech.Window()
        w.show('human', {'颗粒度'})                    # 你贴进来的文章里有
        self.assertEqual(w.say('assistant', {'颗粒度'}), (set(), {'颗粒度'}))   # AI 跟着说不算主动


TZ = ZoneInfo('America/Los_Angeles')


def _msg(i, conv, day, hour, text='ok', role='human'):
    return Msg(f'm{i}', conv, 'claude', datetime(2026, 5, day, hour, 0, tzinfo=TZ), role, text, speech.typed(text))


def _person(msgs):
    p = Period(1, datetime(2026, 5, 1, tzinfo=TZ), datetime(2026, 5, 15, tzinfo=TZ), 20.0, msgs)
    return Participant('T', 'America/Los_Angeles', [p], {})


class Measures(unittest.TestCase):
    def test_first_person_counts(self):
        self.assertEqual(fp.counts("I'm tired and my code broke"), (2, 6))
        self.assertEqual(fp.counts('我们觉得我想睡'), (1, 7))          # 「我们」不算
        self.assertEqual(fp.counts('Mine'), (1, 1))

    def test_overnight_is_mean_of_daily_ratios(self):
        msgs = [_msg(1, 'a', 3, 1), _msg(2, 'a', 3, 14),               # 5/3：一半在 0–6 点
                _msg(3, 'b', 4, 10), _msg(4, 'b', 4, 11), _msg(5, 'b', 4, 12), _msg(6, 'b', 4, 13),   # 5/4：没有
                _msg(7, 'c', 5, 2, role='assistant'),                  # AI 说的不算
                _msg(8, 'd', 6, 3, text='x' * 500)]                    # 贴进来的不算
        value, ev = night.compute(_person(msgs), {'overnight_hours': [0, 6]})[1]
        self.assertAlmostEqual(value, (0.5 + 0.0) / 2)
        self.assertEqual([e['msg'] for e in ev], ['m1'])

    def test_overnight_evidence_one_per_window(self):
        msgs = [_msg(1, 'a', 3, 1), _msg(2, 'a', 3, 2), _msg(3, 'b', 4, 3)]
        _, ev = night.compute(_person(msgs), {})[1]
        self.assertEqual([e['msg'] for e in ev], ['m1', 'm3'])


    def test_sessions_split_on_30_minute_gap(self):
        msgs = [_msg(1, 'a', 3, 10), _msg(2, 'b', 3, 10), _msg(3, 'a', 3, 11), _msg(4, 'a', 3, 13)]
        msgs[1].at = msgs[0].at.replace(minute=25)                    # 隔 25 分钟、换了窗口：同一段
        msgs[2].at = msgs[0].at.replace(hour=11, minute=0)            # 隔 35 分钟：新的一段
        self.assertEqual([[m.id for m in s] for s in sessions(msgs)], [['m1', 'm2'], ['m3'], ['m4']])


if __name__ == '__main__':
    unittest.main()


class Route(unittest.TestCase):
    """研究版只用研究方案写明的厂商和模型（10-29）：名单外的不碰、不自动换，每次尝试都记账。"""

    def setUp(self):
        import tempfile
        import llm
        from research import route
        self.route, self.llm = route, llm
        self.old_flow, self.old_ask = route.FLOW, llm.ask_only
        route.FLOW = os.path.join(tempfile.mkdtemp(prefix='ephemeris-flow-'), 'flow.jsonl')
        self.calls = []

    def tearDown(self):
        self.route.FLOW, self.llm.ask_only = self.old_flow, self.old_ask

    def fake(self, fail=()):
        def ask_only(provider, model, prompt, **kw):
            self.calls.append((provider, model))
            if provider in fail:
                raise RuntimeError('down')
            return '{"ok": 1}'
        self.llm.ask_only = ask_only

    def test_no_models_no_call(self):
        self.fake()
        with self.assertRaises(ValueError):
            self.route.call({'study': 's'}, 'p', 'x')
        with self.assertRaises(ValueError):
            self.route.call({'study': 's', 'models': ['somewhere:m']}, 'p', 'x')
        self.assertEqual(self.calls, [])

    def test_only_listed_in_order_and_logged(self):
        self.fake(fail=('deepseek',))
        study = {'study': 's', 'models': ['deepseek:a', 'claude:b']}
        self.assertEqual(self.route.call_json(study, 'hello', 'x', pid='P1'), {'ok': 1})
        self.assertEqual(self.calls, [('deepseek', 'a'), ('claude', 'b')])
        flow = self.route.flow('s')
        self.assertEqual(flow['deepseek']['a']['ok'], 0)
        self.assertEqual(flow['claude']['b'], {'calls': 1, 'ok': 1, 'chars': 5, 'participants': 1})

    def test_all_listed_fail_raises_without_other_vendors(self):
        self.fake(fail=('deepseek',))
        with self.assertRaises(RuntimeError):
            self.route.call({'study': 's', 'models': ['deepseek:a']}, 'p', 'x')
        self.assertEqual(self.calls, [('deepseek', 'a')])

    def test_research_never_calls_auto_routing(self):
        """llm.ask / ask_json / claude / _call 会自动换厂商：研究版代码里只许 route.py 调 llm.ask_only。"""
        bad = []
        for dirpath, _, files in os.walk(os.path.join(ROOT, 'research')):
            for f in files:
                if f.endswith('.py'):
                    src = open(os.path.join(dirpath, f), encoding='utf-8').read()
                    for name in ('ask', 'ask_json', 'claude', 'claude_stream', 'ask_stream', '_call', '_cheap', '_gemini'):
                        if f'llm.{name}(' in src:
                            bad.append(f'{f}: llm.{name}')
        self.assertEqual(bad, [])
