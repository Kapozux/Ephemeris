"""ChatGPT 的聊天也能进 Ephemeris（2026-10-14 用户：「有没有类似 Claude 的抓的」）：认两种文件——
export_chatgpt.js 下载的 chatgpt_export_*.json，和 ChatGPT 官方「导出数据」的 zip / conversations.json（消息是一棵树，只取最后停在的那一支）。
来源 chatgpt，算 AI 聊天（不是写代码）；再导一次不重复。"""
import _env  # noqa: F401
import json
import os
import tempfile
import unittest
import zipfile

import index as idx
import slice as sl
import store

T0 = 1776500000          # 2026-04-18 某时


def official():
    def node(i, parent, role, text, t, ctype='text', **kw):
        return {'id': i, 'parent': parent, 'children': [], 'message': None if role is None else {
            'id': i, 'author': {'role': role}, 'create_time': t, 'recipient': kw.get('recipient', 'all'),
            'metadata': kw.get('metadata', {}), 'content': {'content_type': ctype, 'parts': [text]}}}
    mapping = {n['id']: n for n in [
        node('root', None, None, '', None),
        node('sys', 'root', 'system', '', T0, metadata={'is_visually_hidden_from_conversation': True}),
        node('u1', 'sys', 'user', '帮我改一下文书第一段', T0 + 1),
        node('a_old', 'u1', 'assistant', '旧的回答（被重新生成掉了）', T0 + 2),
        node('a1', 'u1', 'assistant', '可以，第一段先讲你做的项目。', T0 + 3),
        node('tool', 'a1', 'assistant', 'search("x")', T0 + 4, ctype='code', recipient='web'),
        node('u2', 'tool', 'user', '好', T0 + 5),
    ]}
    return [{'id': 'g1', 'title': '文书', 'create_time': T0, 'update_time': T0 + 5, 'current_node': 'u2', 'mapping': mapping}]


class Import(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations'):
                db.execute(f'DELETE FROM {t}')
        self.dir = tempfile.mkdtemp()

    def rows(self):
        return [(r['role'], r['text'], r['source']) for r in store.q('SELECT role, text, source FROM messages ORDER BY ts')]

    def test_script_export(self):
        p = os.path.join(self.dir, 'chatgpt_export_2026-10-14.json')
        json.dump({'exporter': 'ephemeris-chatgpt', 'conversations': [{'uuid': 'chatgpt-g1', 'name': '文书', 'messages': [
            {'role': 'human', 'created_at': '2026-04-18T08:00:00.000Z', 'text': '帮我改一下文书第一段'},
            {'role': 'assistant', 'created_at': '2026-04-18T08:00:05.000Z', 'text': '可以。'}]}]}, open(p, 'w'), ensure_ascii=False)
        self.assertEqual(idx.sniff(p), ('claude_json', 'chatgpt'))
        r = idx.import_file(store.connect(), p, keep_copy=False)
        self.assertEqual(self.rows(), [('human', '帮我改一下文书第一段', 'chatgpt'), ('assistant', '可以。', 'chatgpt')])
        self.assertEqual(store.one('SELECT id, source FROM conversations')['source'], 'chatgpt')
        idx.import_file(store.connect(), p, keep_copy=False)
        self.assertEqual(len(self.rows()), 2)                                  # 再导一次不重复

    def test_official_export_takes_the_last_branch(self):
        for name in ('conversations.json', 'export.zip'):
            with self.subTest(name):
                self.setUp()
                p = os.path.join(self.dir, name)
                if name.endswith('.zip'):
                    with zipfile.ZipFile(p, 'w') as z:
                        z.writestr('conversations.json', json.dumps(official(), ensure_ascii=False))
                else:
                    json.dump(official(), open(p, 'w'), ensure_ascii=False)
                self.assertEqual(idx.sniff(p), ('chatgpt_export', 'chatgpt'))
                idx.import_file(store.connect(), p, keep_copy=False)
                self.assertEqual(self.rows(), [('human', '帮我改一下文书第一段', 'chatgpt'),
                                               ('assistant', '可以，第一段先讲你做的项目。', 'chatgpt'), ('human', '好', 'chatgpt')])
                self.assertEqual(store.one('SELECT id FROM conversations')['id'], 'chatgpt-g1')     # 跟脚本导的是同一个窗口

    def test_claude_zip_still_claude(self):
        p = os.path.join(self.dir, 'claude.zip')
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('conversations.json', json.dumps([{'uuid': 'x', 'name': 'a', 'chat_messages': []}]))
        self.assertEqual(idx.sniff(p), ('claude_json', 'claude'))

    def test_counts_as_chat_not_coding(self):
        self.assertEqual(sl._kind('chatgpt'), 'chat')


if __name__ == '__main__':
    unittest.main()
