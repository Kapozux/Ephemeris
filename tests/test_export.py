"""导出（2026-10-07）：原文归档每个对话一个文件、带目录；交接包四个文件，跳过的话题哪儿都不出现。"""
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
        llm.use(lambda p: (self.prompts.append(p), '## 我是谁\n高中生。')[1])

    def tearDown(self):
        llm.use(None)
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, *parts):
        with open(os.path.join(*parts), encoding='utf-8') as f:
            return f.read()

    def test_archive_one_file_per_conversation(self):
        job = {}
        out = export.archive(job, self.root)
        self.assertEqual((job['done'], job['total']), (2, 2))
        f = self.read(out, 'Claude', '2026-03', '2026-03-03_改 IA 第二稿 终稿_c1.md')
        self.assertIn('**我**　2026-03-03 10:00', f)
        self.assertIn('**Claude**　2026-03-03 10:01', f)
        self.assertLess(f.index('帮我看看这段'), f.index('第二段太长'))
        self.assertTrue(os.path.exists(os.path.join(out, 'Gemini', '2026-04', '2026-04-01_无标题_g1.md')))
        toc = self.read(out, '目录.md')
        self.assertIn('2 个对话', toc)
        self.assertIn('(<Claude/2026-03/2026-03-03_改 IA 第二稿 终稿_c1.md>)', toc)

    def test_second_export_same_day_does_not_overwrite(self):
        a = export.archive({}, self.root)
        b = export.archive({}, self.root)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isdir(a) and os.path.isdir(b))

    def test_handoff_files_and_skip(self):
        out = export.handoff({}, self.root, ('感情',))
        self.assertEqual(set(os.listdir(out)), {'关于我.md', '正在进行的事.md', '怎么用.md', '过去一年.md'})
        timeline, threads = self.read(out, '过去一年.md'), self.read(out, '正在进行的事.md')
        self.assertIn('改经济 IA 第二稿', timeline)
        self.assertNotIn('感情', timeline)
        self.assertIn('经济 IA进展1', threads)                     # 做到哪 = 最后一次的进展
        self.assertNotIn('感情', threads)
        self.assertEqual(len(self.prompts), 1)
        self.assertNotIn('跟她吵架', self.prompts[0])
        self.assertIn('不要写任何跟这些有关的内容：感情', self.prompts[0])
        self.assertIn('高中生', self.read(out, '关于我.md'))

    def test_handoff_model_failure_keeps_other_files(self):
        llm.use(lambda p: None)
        job = {}
        with self.assertRaises(RuntimeError):
            export.handoff(job, self.root)
        out = os.path.join(self.root, '交接包')
        self.assertTrue(os.path.exists(os.path.join(out, '过去一年.md')))
        self.assertFalse(os.path.exists(os.path.join(out, '关于我.md')))


if __name__ == '__main__':
    unittest.main()
