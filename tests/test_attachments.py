"""claude.ai 消息带的附件（10-28）：只在正文后记一行名字；补上之前存过的消息不变成两条；只有附件没正文的也留下。"""
import _env  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import index as idx
import store

UUID = '5cc081f6-8b3b-484e-92f0-46781e138915'


def write(path, msgs, official=False):
    if official:     # claude.ai 官方导出：chat_messages + sender + content 块 + attachments / files
        c = {'uuid': UUID, 'name': '看看这个', 'chat_messages': [
            {'sender': r, 'created_at': ts, 'content': [{'type': 'text', 'text': t}], 'text': t,
             'attachments': [{'file_name': n, 'extracted_content': '很长'} for n in att],
             'files': [{'file_name': n} for n in files]} for ts, r, t, att, files in msgs]}
        data = [c]
    else:            # 每日同步：messages + role + attachments（名字）
        data = {'conversations': [{'uuid': UUID, 'name': '看看这个', 'messages': [
            {'role': r, 'created_at': ts, 'text': t, **({'attachments': att + files} if att or files else {})}
            for ts, r, t, att, files in msgs]}]}
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False)


class Attachments(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'conversations_dup', 'messages_dup', 'diaries'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-att-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, name, msgs, official=False):
        p = os.path.join(self.tmp, name)
        write(p, msgs, official)
        return idx.ingest(store.connect(), p)

    def texts(self):
        return [r['text'] for r in store.q('SELECT text FROM messages ORDER BY ts')]

    def test_old_message_gets_the_note_without_a_second_copy(self):
        old = [('2026-10-01T04:00:00Z', 'human', '你帮我看看', [], []),
               ('2026-10-01T04:01:00Z', 'assistant', '这里面说了三件事', [], [])]
        self.ingest('old.json', old)                                     # 10-28 之前：附件丢了
        self.ingest('new.json', [('2026-10-01T04:00:00Z', 'human', '你帮我看看', [''], ['简历.pdf']),
                                 old[1]], official=True)
        self.assertEqual(self.texts(), ['你帮我看看\n[附件：粘贴的长文、简历.pdf]', '这里面说了三件事'])
        self.assertEqual(store.one("SELECT COUNT(*) n FROM msg_fts WHERE text LIKE '%简历.pdf%'")['n'], 1)

    def test_attachment_only_message_is_kept_and_sync_matches_export(self):
        msgs = [('2026-10-02T04:00:00Z', 'human', '', [], ['截图.png']),
                ('2026-10-02T04:01:00Z', 'assistant', '图里是一张表', [], [])]
        self.ingest('sync.json', msgs)
        self.ingest('export.json', msgs, official=True)                   # 两条路进来是同一条
        self.assertEqual(self.texts(), ['[附件：截图.png]', '图里是一张表'])

    def test_note_lists_each_name_once(self):
        self.assertEqual(idx._claude_files({'attachments': [{'file_name': 'a.txt'}], 'files': [{'file_name': 'a.txt'}]}),
                         ['a.txt'])
        self.assertEqual(idx.attach_note([]), '')


if __name__ == '__main__':
    unittest.main()
