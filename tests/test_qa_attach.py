"""「问」里附文件（11-01 用户：「我可以主动添加文件一起问」）：上传存进 qa_files/、读得出文字才收；问的时候提示词里列出路径，
让它先用 look.py file 读；问题那行记着附了什么（重新跑、改问题都还带着），messages 里跟 claude.ai 附件一样记「[附件：名字]」。"""
import _env  # noqa: F401
import io
import json
import os
import unittest
import zipfile

import app as appmod
import llm
import localfiles
import qa
import store
from test_cards_qa import deltas, seed

H = {'Origin': 'http://localhost:5055'}


def docx(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('word/document.xml', f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return b.getvalue()


class Upload(unittest.TestCase):
    def test_text_and_docx_are_kept(self):
        f = localfiles.save_upload('笔记.md', '第一行\n第二行\n'.encode())
        self.assertEqual((f['name'], f['lines']), ('笔记.md', 2))
        real = localfiles.resolve(f['path'])
        self.assertTrue(real.startswith(localfiles.uploads()))
        self.assertEqual(localfiles.attach_info(f['path'])['name'], '笔记.md')
        d = localfiles.save_upload('文书.docx', docx('Why Purdue'))
        self.assertEqual(d['lines'], 1)

    def test_rejected_files_are_not_kept(self):
        before = sum(len(fs) for _, _, fs in os.walk(localfiles.uploads()))
        for name, data in (('图.png', b'\x89PNG\r\n\x1a\n\0\0\0'), ('id_rsa', b'key'), ('空.txt', b'  \n'), ('../../.env', b'')):
            with self.assertRaises(localfiles.Denied):
                localfiles.save_upload(name, data)
        self.assertEqual(sum(len(fs) for _, _, fs in os.walk(localfiles.uploads())), before)

    def test_folder(self):
        items = [('论文/README.md', b'# hi\n'), ('论文/src/a.py', b'print(1)\n'), ('论文/图.png', b'\x89PNG\0'),
                 ('论文/.git/config', b'x'), ('论文/id_rsa', b'k'), ('论文/../../逃出去.txt', b'x')]
        f = localfiles.save_folder('论文', items)
        self.assertEqual((f['kind'], f['files'], f['skipped']), ('folder', 3, 3))        # 图片照存，挡着的跳过
        real = localfiles.resolve(f['path'])
        self.assertTrue(os.path.isfile(os.path.join(real, 'src', 'a.py')))
        info = qa.attachments([f['path']])[0]
        self.assertEqual((info['name'], info['files']), ('论文', 3))
        self.assertIn('文件夹 论文，3 个文件', qa._with_files('看看', [info], 'look'))
        with self.assertRaises(localfiles.Denied):
            localfiles.save_folder('空', [('空/.DS_Store', b'x')])

    def test_folder_route(self):
        store.run('DELETE FROM app_lock')
        c = appmod.app.test_client()
        r = c.post('/api/qa/upload', data={'folder': 'notes', 'file': [(io.BytesIO(b'a'), 'notes/a.txt'), (io.BytesIO(b'b'), 'notes/sub/b.txt')]},
                   headers=H, content_type='multipart/form-data').get_json()
        self.assertEqual((r['files'][0]['kind'], r['files'][0]['files']), ('folder', 2))

    def test_only_allowed_paths_can_be_attached(self):
        with self.assertRaises(localfiles.Denied):
            qa.attachments(['/etc/passwd'])

    def test_pick_gives_paths_without_copying(self):
        store.run('DELETE FROM app_lock')
        mine = localfiles.save_folder('选的', [('选的/a.md', b'x\n'), ('选的/b.md', b'y\n')])['path']
        before = sorted(os.listdir(localfiles.uploads()))
        saved = localfiles.pick
        localfiles.pick = lambda kind='file': [mine, '/etc']
        try:
            r = appmod.app.test_client().post('/api/qa/pick', json={'kind': 'folder'}, headers=H).get_json()
        finally:
            localfiles.pick = saved
        self.assertEqual((r['files'][0]['path'], r['files'][0]['files']), (mine, 2))
        self.assertIn('error', r['files'][1])                          # 白名单外面的不收
        self.assertEqual(sorted(os.listdir(localfiles.uploads())), before)

    def test_route(self):
        store.run('DELETE FROM app_lock')
        c = appmod.app.test_client()
        r = c.post('/api/qa/upload', data={'file': [(io.BytesIO('一句话'.encode()), 'a.txt'), (io.BytesIO(b'\0\1'), 'b.bin')]},
                   headers=H, content_type='multipart/form-data').get_json()
        self.assertEqual(r['files'][0]['name'], 'a.txt')
        self.assertIn('error', r['files'][1])


class Ask(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()
        store.run('DELETE FROM app_lock')
        self.calls = []

        def run(args, stdin):
            self.calls.append((args, stdin))
            return {'result': '答上了。', 'session_id': f's{len(self.calls)}', 'events': deltas('答上了。')}
        llm.use_claude(run)
        self.t = qa.new_thread()
        self.f = localfiles.save_upload('申请清单.txt', 'Purdue EA 11-01\n'.encode())

    def tearDown(self):
        llm.use_claude(None)

    def test_prompt_lists_files_and_question_remembers_them(self):
        qa.answer_deep('这份清单跟我聊的对得上吗', thread=self.t, files=qa.attachments([self.f['path']]))
        self.assertIn(self.f['path'], self.calls[0][1])
        self.assertIn('file <路径>', self.calls[0][1])
        q = store.one("SELECT trace FROM qa_chat WHERE thread_id=? AND role='user'", self.t)
        self.assertEqual(qa.files_of(q)[0]['name'], '申请清单.txt')
        m = store.one("SELECT text FROM messages WHERE source='ask' AND role='human' ORDER BY rowid DESC LIMIT 1")
        self.assertTrue(m['text'].endswith('[附件：申请清单.txt]'))
        qa.answer_deep('再说说', thread=self.t)                           # 追问不带新文件：提示词里不再列
        self.assertNotIn(self.f['path'], self.calls[1][1])

    def test_retry_and_edit_keep_files(self):
        c = appmod.app.test_client()
        seen = {}
        saved = qa.start_ask
        qa.start_ask = lambda *a, **kw: seen.update(kw) or True
        try:
            r = c.post('/api/qa/ask', json={'question': '看看', 'thread': self.t, 'files': [self.f['path']]}, headers=H)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(seen['files'][0]['path'], self.f['path'])
            r = c.post('/api/qa/ask', json={'question': '看看', 'thread': self.t, 'files': ['/etc/passwd']}, headers=H)
            self.assertEqual(r.status_code, 400)
        finally:
            qa.start_ask = saved
        qa.answer_deep('看看', thread=self.t, files=qa.attachments([self.f['path']]))
        qa.rewind(self.t)                                               # 重新跑：问题那行还在，附件从它的 trace 里拿
        qa.answer_deep('看看', thread=self.t, again=True, fresh=True)
        self.assertIn(self.f['path'], self.calls[-1][1])
        self.assertIn('申请清单.txt', json.dumps(qa.history(thread=self.t)[-2]['trace'], ensure_ascii=False))
        qa.start_ask = lambda *a, **kw: seen.update(kw) or True
        try:                                                            # 改问题：附件跟着原来那句
            c.post('/api/qa/ask', json={'question': '换个问法', 'thread': self.t, 'redo': 'edit'}, headers=H)
            self.assertEqual(seen['files'][0]['path'], self.f['path'])
        finally:
            qa.start_ask = saved

if __name__ == '__main__':
    unittest.main()
