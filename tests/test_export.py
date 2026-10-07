"""导出（2026-10-07）：完整导出（原文、每周日记、主题）、单个窗口、交接包（大模型一次写三份，跳过的话题不给模型）。"""
import _env  # noqa: F401
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import days
import export
import llm
import store


ANSWER = '=====关于我=====\n## 我是谁\n高中生。\n=====过去一年=====\n## 2026-10\n- 2026-10 在做导出\n=====正在进行的事=====\n## 导出\n做到一半\n'


def day(n):
    return (datetime.fromisoformat(days.today()) - timedelta(days=n)).date().isoformat()


class Export(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ephemeris-exp-')
        with store.tx() as db:
            for t in ('messages', 'conversations', 'diaries', 'themes', 'theme_days'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('c1', 'claude', '改 IA: 第二稿/终稿')")
            db.execute("INSERT INTO conversations(id, source, title) VALUES ('g1', 'gemini', NULL)")
            rows = [('m1', 'c1', 'claude', '2026-03-03T10:00', '2026-03-03', '10:00', 'human', '帮我看看这段', 6),
                    ('m2', 'c1', 'claude', '2026-03-03T10:01', '2026-03-03', '10:01', 'assistant', '第二段太长', 5),
                    ('m3', 'g1', 'gemini', '2026-04-01T09:00', '2026-04-01', '09:00', 'human', '今天吃什么', 5)]
            db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', rows)
            for d, h in ((day(3), '改经济 IA 第二稿'), (day(2), '跟她吵架，感情很乱'), (day(1), '把 Ephemeris 的导出做完')):
                db.execute("INSERT INTO diaries(date, headline, narrative) VALUES (?, ?, '那天的事。')", (d, h))
            for name in ('经济 IA', '感情'):
                tid = db.execute('INSERT INTO themes(name) VALUES (?)', (name,)).lastrowid
                for n in (3, 1):
                    db.execute("INSERT INTO theme_days(theme_id, date, note) VALUES (?, ?, ?)", (tid, day(n), f'{name}进展{n}'))
        self.prompts = []
        llm.use(lambda p: (self.prompts.append(p), ANSWER)[1])

    def tearDown(self):
        llm.use(None)
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, *parts):
        with open(os.path.join(*parts), encoding='utf-8') as f:
            return f.read()

    def test_full_export(self):
        job = {}
        out = export.full(job, self.root)
        self.assertEqual(os.path.basename(out), '完整导出')
        f = self.read(out, '聊天原文', 'Claude', '2026-03', '2026-03-03_改 IA 第二稿 终稿_c1.md')
        self.assertIn('**我**　2026-03-03 10:00', f)
        self.assertIn('**Claude**　2026-03-03 10:01', f)
        self.assertLess(f.index('帮我看看这段'), f.index('第二段太长'))
        self.assertTrue(os.path.exists(os.path.join(out, '聊天原文', 'Gemini', '2026-04', '2026-04-01_无标题_g1.md')))
        toc = self.read(out, '聊天原文', '目录.md')
        self.assertIn('2 个对话', toc)
        self.assertIn('(<Claude/2026-03/2026-03-03_改 IA 第二稿 终稿_c1.md>)', toc)
        weeks = ''.join(self.read(out, '日记', n) for n in os.listdir(os.path.join(out, '日记')))
        self.assertIn('把 Ephemeris 的导出做完', weeks)
        themes = self.read(out, '主题.md')
        self.assertIn('## 经济 IA', themes)
        self.assertIn('经济 IA进展1', themes)
        self.assertIn('2 个对话', self.read(out, '说明.md'))
        self.assertEqual(job['path'], out)

    def test_second_export_same_day_does_not_overwrite(self):
        a = export.full({}, self.root)
        b = export.full({}, self.root)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isdir(a) and os.path.isdir(b))

    def test_one_conversation(self):
        name, text = export.one('c1')
        self.assertEqual(name, '2026-03-03_改 IA 第二稿 终稿_c1.md')
        self.assertTrue(text.startswith('# 改 IA: 第二稿/终稿'))
        self.assertIsNone(export.one('nope'))

    def test_handoff_three_sections_and_skip(self):
        out = export.handoff({}, self.root, ('感情',))
        self.assertEqual(set(os.listdir(out)), {'关于我.md', '过去一年.md', '正在进行的事.md', '怎么用.md'})
        self.assertEqual(self.read(out, '关于我.md'), '# 关于我\n\n## 我是谁\n高中生。\n')
        self.assertIn('2026-10 在做导出', self.read(out, '过去一年.md'))
        self.assertEqual(len(self.prompts), 1)
        p = self.prompts[0]
        self.assertIn('改经济 IA 第二稿', p)
        self.assertNotIn('跟她吵架', p)                                       # 标题带「感情」的日记不给模型
        self.assertNotIn('感情进展', p)                                       # 主题也不给
        self.assertIn('不要写任何跟这些有关的内容：感情', p)
        self.assertIn('跳过了：感情', self.read(out, '怎么用.md'))

    def test_skip_filters_body_too(self):
        store.run("INSERT INTO diaries(date, headline, narrative) VALUES (?, '普通的一天', '写完作业，晚上又想到感情的事。')", day(5))
        p = export.material(['感情'])
        self.assertNotIn('普通的一天', p)                                     # 标题没提，正文提了：也不发
        self.assertIn('改经济 IA 第二稿', p)
        n = export.status(['感情'])['sends']
        self.assertEqual((n['dropped_diaries'], n['dropped_themes']), (2, 1))
        self.assertEqual(export.status()['sends']['dropped_diaries'], 0)

    def test_handoff_bad_format_keeps_raw_text(self):
        llm.use(lambda p: '我没按格式写')
        with self.assertRaises(RuntimeError):
            export.handoff({}, self.root)
        out = os.path.join(self.root, '交接包')
        self.assertEqual(os.listdir(out), ['模型原文.md'])

    def test_split_tolerates_spacing(self):
        got = export.split('前言\n===== 关于我 =====\nA\n=====过去一年=====\nB\n=====正在进行的事=====\n\nC\n')
        self.assertEqual(got, {'关于我': 'A', '过去一年': 'B', '正在进行的事': 'C'})

    def test_estimate_is_about_a_dollar_scale(self):
        self.assertGreater(export.estimate(), 0.2)                          # 输出那一万 token 就 $0.25
        self.assertLess(export.estimate(), 2)


if __name__ == '__main__':
    unittest.main()
