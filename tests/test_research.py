"""研究版（research/）：只许引用 research.CORE 里的 Ephemeris 模块；共用的 speech 判断对；指标算得对。"""
import _env  # noqa: F401
import ast
import json
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
        ephemeris = {f[:-3] for f in os.listdir(os.path.join(ROOT, 'src')) if f.endswith('.py')}
        self.assertIn('store', ephemeris)
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


class Consent(unittest.TestCase):
    """参与者先看再交（10-29）：拿掉的窗口不进任何指标；删掉的证据不交；退出了整个人不导出；给研究者的只有计数。"""

    def setUp(self):
        import sqlite3
        import tempfile
        from research import consent, participant
        self.consent, self.participant = consent, participant
        self.dir = tempfile.mkdtemp(prefix='ephemeris-consent-')
        self.old = consent.DATA, participant.DATA, participant.db_path
        consent.DATA = participant.DATA = self.dir
        participant.db_path = lambda pid: os.path.join(self.dir, f'{pid}.db')
        db = sqlite3.connect(os.path.join(self.dir, 'P9.db'))
        db.execute('CREATE TABLE messages (id, conv_id, source, ts, role, text)')
        db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?)', [
            ('m1', 'keep', 'claude', '2026-03-03T10:00:00+00:00', 'human', 'hi'),
            ('m2', 'drop', 'claude', '2026-03-04T10:00:00+00:00', 'human', 'secret')])
        db.commit()
        db.close()
        with open(os.path.join(self.dir, 'cesd.csv'), 'w') as f:
            f.write('pid,survey_number,survey_date,cesd_score\nP9,0,2026-03-01,10\nP9,1,2026-03-15,12\n')

    def tearDown(self):
        self.consent.DATA, self.participant.DATA, self.participant.db_path = self.old

    def test_excluded_window_leaves_every_measure(self):
        self.consent.exclude('P9', 'drop')
        person = self.participant.load('P9', 'UTC')
        self.assertEqual(list(person.convs), ['keep'])
        self.assertEqual([m.id for m in person.periods[0].msgs], ['m1'])
        self.consent.exclude('P9', 'drop', on=False)
        self.assertEqual(len(self.participant.load('P9', 'UTC').periods[0].msgs), 2)

    def test_changing_a_decision_needs_confirming_again(self):
        self.consent.submitted('P9', {'made_at': 'x'})
        self.consent.hide('P9', 'm1')
        self.assertIsNone(self.consent.get('P9')['submitted'])

    def test_researchers_get_counts_only(self):
        self.consent.exclude('P9', 'drop')
        self.consent.hide('P9', 'm1')
        self.consent.withdraw('P9')
        c = self.consent.counts('P9')
        self.assertEqual(c, {'excluded_windows': 1, 'hidden_evidence': 1, 'withdrawn': True, 'other_sources_reported': []})
        self.assertNotIn('drop', json.dumps(c))


class Coverage(unittest.TestCase):
    """覆盖报告（10-29）：平台占比、有数据的天、最长空白、知道模型的占比；不知道的照实报。"""

    def test_period_summary(self):
        from research import coverage
        z = ZoneInfo('UTC')
        p = Period(1, datetime(2026, 3, 1, tzinfo=z), datetime(2026, 3, 15, tzinfo=z), 10)
        mk = lambda i, day, src, role, model=None: Msg(str(i), 'c', src, datetime(2026, 3, day, 9, tzinfo=z), role, 'x', True, model)  # noqa: E731
        p.msgs = [mk(1, 2, 'claude', 'human'), mk(2, 2, 'claude', 'assistant', 'claude-opus-5-5'),
                  mk(3, 3, 'gemini', 'human'), mk(4, 3, 'gemini', 'assistant'), mk(5, 12, 'claude', 'human')]
        s = coverage.period(p)
        self.assertEqual(s['chat_cov_days_with_data'], 3)
        self.assertEqual(s['chat_cov_max_gap_days'], 8)            # 3 号到 12 号之间
        self.assertEqual((s['chat_cov_main_platform'], s['chat_cov_main_platform_share']), ('claude', 0.6))
        self.assertEqual(s['chat_cov_model_known_share'], 0.5)      # Gemini 那条回复不知道模型
        rows = {r['platform']: r for r in coverage.long_rows('P1', p)}
        self.assertEqual(rows['claude']['models'], 'claude-opus-5-5:1')
        self.assertEqual(rows['gemini']['replies_model_known'], 0)


class Label(unittest.TestCase):
    """多人盲标（10-29）：标注页拿不到模型的判断和别人的答案；分歧要大家标完才开；先报人和人，再报模型和人。"""

    def setUp(self):
        import sqlite3
        import tempfile
        from research import consent, label, participant
        self.label, self.consent = label, consent
        self.dir = tempfile.mkdtemp(prefix='ephemeris-label-')
        self.old = research.DATA, consent.DATA, participant.DATA, participant.db_path
        research.DATA = consent.DATA = participant.DATA = self.dir
        participant.db_path = lambda pid: os.path.join(self.dir, f'{pid}.db')
        db = sqlite3.connect(os.path.join(self.dir, 'P01.db'))
        db.execute('CREATE TABLE messages (id, conv_id, source, ts, role, text)')
        rows = []
        for c in range(6):
            rows += [(f'c{c}h', f'c{c}', 'claude', f'2026-03-0{c + 2}T10:00:00+00:00', 'human', f'question {c}'),
                     (f'c{c}a', f'c{c}', 'claude', f'2026-03-0{c + 2}T10:01:00+00:00', 'assistant', f'answer {c}')]
        db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?)', rows)
        db.commit()
        db.close()
        with open(os.path.join(self.dir, 'cesd.csv'), 'w') as f:
            f.write('pid,survey_number,survey_date,cesd_score\nP01,0,2026-03-01,10\nP01,1,2026-03-15,12\n')
        with open(os.path.join(self.dir, 'P01.judge.json'), 'w') as f:
            json.dump({f'c{c}': {'version': 'v1', 'idea_from': 'ai', 'verified': True} for c in range(6)}, f)
        study = {'study': 's', 'privacy': 'A', 'participants': ['P01'], 'models': ['deepseek:x'], 'measures': []}
        json.dump(study, open(os.path.join(self.dir, 'study.json'), 'w'))
        self.t = {'task': 't', 'study': os.path.join(self.dir, 'study.json'), 'unit': 'window', 'seed': 1,
                  'rounds': [2, 2], 'annotators': ['A1', 'A2'],
                  'codebook': {'version': 'v1', 'questions': [
                      {'key': 'idea', 'text': 'who?', 'options': [['user', 'P'], ['ai', 'AI'], ['none', 'No'], ['unsure', '?']],
                       'abstain': ['unsure'], 'model': 'chat_ai_first_idea_ratio:idea_from'}]}}
        self.path = os.path.join(self.dir, 'task.json')
        json.dump(self.t, open(self.path, 'w'))

    def tearDown(self):
        from research import consent, participant
        research.DATA, consent.DATA, participant.DATA, participant.db_path = self.old

    def test_blind_and_human_human_first(self):
        L = self.label
        self.consent.hide('P01', 'c0h')                         # 参与者删掉的原话不进题目
        items = L.make(self.path)
        self.assertEqual(len(items), 4)
        self.assertNotIn('c0', {it['conv'] for it in items})
        st = L.state(self.t, items, 'A1')
        page = json.dumps(st)
        self.assertNotIn('model', json.dumps(st['codebook']))
        for it in items:                                        # 页面上没有参与者编号、窗口 id、模型的判断
            self.assertNotIn(it['conv'], page)
            self.assertNotIn('P01', page)
        self.assertNotIn('"ai"', json.dumps(st['next']))
        r1 = L.round_items(self.t, items, 1)
        for n, it in enumerate(r1):
            L.save_label(self.t, items, 'A1', it['id'], {'idea': 'ai'}, 1000)
        self.assertIsNone(L.disagreements(self.t, items, 1))     # A2 还没标完：不开讨论
        with self.assertRaises(ValueError):
            L.save_label(self.t, items, 'A2', L.round_items(self.t, items, 2)[0]['id'], {'idea': 'ai'}, 1)  # 不在这一轮
        with self.assertRaises(ValueError):
            L.save_label(self.t, items, 'A2', r1[0]['id'], {'idea': 'maybe'}, 1)
        L.save_label(self.t, items, 'A2', r1[0]['id'], {'idea': 'ai'}, 3000)
        L.save_label(self.t, items, 'A2', r1[1]['id'], {'idea': 'user'}, 5000)
        dis = L.disagreements(self.t, items, 1)
        self.assertEqual([d['answers'] for d in dis], [{'A1': 'ai', 'A2': 'user'}])
        self.assertNotIn('P01', json.dumps(dis))
        L.agree_on(self.t, r1[1]['id'], 'idea', 'ai')
        s = L.score(self.path)['rounds'][0]
        q = s['questions']['idea']
        self.assertEqual(list(q)[:2], ['human_human', 'model_vs_human'])
        self.assertEqual(q['human_human']['pairs']['A1~A2']['agree'], 0.5)
        self.assertEqual(q['model_vs_human']['vs_gold']['n'], 2)       # 1 题两人对上 + 1 题讨论定的
        self.assertEqual(q['model_vs_human']['gold_from'], {'discussed': 1, 'unanimous': 1})
        self.assertEqual(s['time']['A2']['median_s'], 4.0)

    def test_page_api_is_blind(self):
        items = self.label.make(self.path)
        c = self.label.create_app(self.path).test_client()
        self.assertEqual(c.get('/api/state?a=nobody').status_code, 400)
        st = c.get('/api/state?a=A1').get_json()
        self.assertNotIn('chat_ai_first_idea_ratio', json.dumps(st))
        self.assertEqual(c.get('/api/discuss?a=A1').status_code, 409)
        bad = c.post('/api/label', json={'a': 'A1', 'id': st['next']['id'], 'answers': {}})
        self.assertEqual(bad.status_code, 400)
        for it in self.label.round_items(self.t, items, 1):
            for a in ('A1', 'A2'):
                c.post('/api/label', json={'a': a, 'id': it['id'], 'answers': {'idea': 'none'}, 'ms': 10})
        self.assertEqual(c.get('/api/discuss?a=A1').get_json()['items'], [])

    def test_privacy_b_cannot_be_labelled(self):
        study = json.load(open(self.t['study']))
        json.dump(dict(study, privacy='B'), open(self.t['study'], 'w'))
        with self.assertRaises(SystemExit):
            self.label.make(self.path)
