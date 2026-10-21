"""「问」只读看电脑上的文件（2026-10-21）：只在白名单目录里；隐藏文件、密钥、数据库、链到外面的都看不了；回答里 [[f:路径:行号]] 认得回那几行。"""
import _env  # noqa: F401
import os
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

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


class Documents(unittest.TestCase):
    """10-21：.docx / .pptx / 有文字层的 PDF 当场取字；扫描件不现转，指去墨页已经转好的（moye:<id>）。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        os.environ['EPHEMERIS_FILE_ROOTS'] = self.root
        self.docx = os.path.join(self.root, '主文书 v5.docx')
        with zipfile.ZipFile(self.docx, 'w') as z:
            z.writestr('word/document.xml', '<w:document><w:body><w:p><w:pPr/><w:r><w:t>我做了 </w:t></w:r><w:r><w:t xml:space="preserve">Verbatim &amp; 墨页</w:t></w:r></w:p>'
                                            '<w:p/><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>')
        self.pptx = os.path.join(self.root, '课件.pptx')
        with zipfile.ZipFile(self.pptx, 'w') as z:
            for i, t in ((2, '第二页的字'), (10, '第十页的字'), (1, '封面')):
                z.writestr(f'ppt/slides/slide{i}.xml', f'<p:sld><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:sld>')
        with open(os.path.join(self.root, '~$主文书.docx'), 'wb') as f:
            f.write(b'\x05Kerwin\0\0')

    def tearDown(self):
        os.environ.pop('EPHEMERIS_FILE_ROOTS', None)
        self.tmp.cleanup()

    def test_docx_pptx(self):
        out = run('file', self.docx)
        self.assertIn('     1| 我做了 Verbatim & 墨页', out)
        self.assertIn('     3| 第二段', out)
        self.assertEqual([t for _, t in localfiles.lines(self.pptx)[0] if '字' in t or '封面' in t], ['封面', '第二页的字', '第十页的字'])
        self.assertIn(':3]]', run('grep', '第二段', '--in', self.root))
        self.assertIn('临时文件', run('file', os.path.join(self.root, '~$主文书.docx')))

    def test_pdf(self):
        pdf = os.path.join(self.root, 'a.pdf')
        with open(pdf, 'wb') as f:
            f.write(b'%PDF-1.4\0')
        text = b'Second Law of Thermodynamics, entropy never decreases\n\x0cpage two has plenty of words on it too\n'
        with mock.patch('localfiles.shutil.which', return_value='/x/pdftotext'), \
                mock.patch('localfiles.subprocess.run', return_value=subprocess.CompletedProcess([], 0, text, b'')):
            out = run('file', pdf)
            self.assertIn('—— 第 2 页 ——', out)
            self.assertIn('entropy never decreases', out)
        with mock.patch('localfiles.shutil.which', return_value='/x/pdftotext'), \
                mock.patch('localfiles.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'\x0c \x0c', b'')):
            self.assertIn('扫描件', run('file', pdf))

    def test_moye(self):
        def fake(path):
            if path.startswith('/api/search'):
                return {'results': [{'id': 'j1', 'count': 2, 'hits': [{'page': 3, 'before': '…the ', 'match': 'Carnot', 'after': ' cycle…'}]}]}
            if path == '/api/library':
                return {'items': [{'id': 'j1', 'filename': '热学.pdf', 'ai_title': '卡诺循环', 'created_at': '2026-10-20T08:39:18.555Z'}]}
            return {'job': {'id': 'j1', 'filename': '热学.pdf', 'finished_at': '2026-10-20T08:42:28Z'},
                    'result': {'markdown': '# 热学\n\nCarnot 效率 = 1 - Tc/Th'}}
        with mock.patch('localfiles._moye', side_effect=fake):
            out = run('moye', 'Carnot')
            self.assertIn('moye:j1', out)
            self.assertIn('第 3 页', out)
            self.assertIn('     3| Carnot 效率', run('file', 'moye:j1'))
            body, cites = qa._deep_cites('效率公式[[f:moye:j1:3]]')
            self.assertEqual(cites[0]['quote'], 'Carnot 效率 = 1 - Tc/Th')
            self.assertIn('墨页 · 热学.pdf', cites[0]['title'])
        self.assertIn('不是墨页的 id', run('file', 'moye:../../etc'))
        with mock.patch.dict(os.environ, {'MOYE_URL': 'http://127.0.0.1:1'}):
            self.assertIn('墨页没在跑', run('moye', 'Carnot'))


if __name__ == '__main__':
    unittest.main()
