"""「问」只读看电脑上的文件（2026-10-21）：只在白名单目录里；隐藏文件、密钥、数据库、链到外面的都看不了；回答里 [[f:路径:行号]] 认得回那几行。"""
import _env  # noqa: F401
import os
import tempfile
import unittest

import localfiles
import qa
from test_look_sql import run


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = os.path.realpath(self.tmp.name)
        self.root, self.out = os.path.join(base, 'docs'), os.path.join(base, 'elsewhere')
        os.makedirs(os.path.join(self.root, 'run v4', 'astropy'))
        os.makedirs(os.path.join(self.root, '.ssh'))
        os.makedirs(self.out)
        self.log = os.path.join(self.root, 'run v4', 'astropy', 'agent_stdout.log')
        with open(self.log, 'w') as f:
            f.write('\n'.join(f'第 {i} 行' for i in range(1, 500)) + '\n大小写两种形式都正常工作，无回归。\n任务完成。\n')
        for name, text in (('.env', 'KEY=1'), ('chats.db', 'x'), ('server.pem', 'x')):
            with open(os.path.join(self.root, name), 'w') as f:
                f.write(text)
        with open(os.path.join(self.root, '.ssh', 'config'), 'w') as f:
            f.write('Host x')
        with open(os.path.join(self.out, 'private.txt'), 'w') as f:
            f.write('外面的')
        os.symlink(self.out, os.path.join(self.root, 'link'))
        with open(os.path.join(self.root, 'pic.png'), 'wb') as f:
            f.write(b'\x89PNG\0\0')
        os.environ['EPHEMERIS_FILE_ROOTS'] = self.root

    def tearDown(self):
        os.environ.pop('EPHEMERIS_FILE_ROOTS', None)
        self.tmp.cleanup()

    def test_list_and_find(self):
        out = run('files', self.root)
        self.assertIn('run v4/', out)
        self.assertNotIn('.env', out)
        self.assertNotIn('chats.db', out)
        self.assertNotIn('.ssh', out)
        self.assertIn('agent_stdout.log', run('files', self.root, '--glob', '*.log'))

    def test_grep_and_read(self):
        out = run('grep', '无回归', '--in', self.root)
        self.assertIn(':500]]', out)
        self.assertIn('1 处', out)
        out = run('file', self.log, '--at', '任务完成', '--lines', '30')
        self.assertIn('   501| 任务完成。', out)
        self.assertIn('（501 行', out)

    def test_refused(self):
        for p in (os.path.join(self.root, '.env'), os.path.join(self.root, 'chats.db'), os.path.join(self.root, '.ssh', 'config'),
                  os.path.join(self.root, 'server.pem'), os.path.join(self.out, 'private.txt'),
                  os.path.join(self.root, 'link', 'private.txt'), 'relative.txt'):
            with self.assertRaises(localfiles.Denied, msg=p):
                localfiles.lines(p)
            self.assertNotIn('KEY=1', run('file', p))
            self.assertNotIn('外面的', run('file', p))
        self.assertIn('不是文字文件', run('file', os.path.join(self.root, 'pic.png')))
        self.assertEqual(localfiles.grep(['KEY'], self.root)[0], [])

    def test_cite_file_lines(self):
        body, cites = qa._deep_cites(f'原文是中文[[f:{self.log}:500-501]]，编的不算[[f:{self.root}/.env:1]]。')
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0]['who'], 'file')
        self.assertEqual(cites[0]['quote'], '大小写两种形式都正常工作，无回归。\n任务完成。')
        self.assertIn('[[f:', body)
        self.assertNotIn('.env', body)

    def test_steps_and_prompt(self):
        self.assertEqual(qa._step('tool', f"python look.py grep 无回归 --in '{self.root}'")['t'], '在文件里搜')
        self.assertIn('file', qa.LOOK_CMDS)
        self.assertIn(localfiles.show(self.root), qa._roots())


if __name__ == '__main__':
    unittest.main()
