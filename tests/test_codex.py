"""Codex（OpenAI）的对话也进 Ephemeris（2026-10-14）：来源 codex，一个会话一个窗口；
他说的（user_message 事件，去掉前面附的文件清单和图片标签）和 Codex 回的文字进来；工具调用、系统塞的环境说明不进；
跟 Claude Code / Cursor 合用「写代码」那 10%；做卡只看他说的。"""
import _env  # noqa: F401
import json
import os
import shutil
import unittest

import cards
import codex
import slice as sl
import store

DAY = '2026-08-15'


def ln(ts, typ, payload):
    return json.dumps({'timestamp': f'{DAY}T{ts}.000Z', 'type': typ, 'payload': payload}, ensure_ascii=False)


class Sync(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(codex.ROOT, ignore_errors=True)
        with store.tx() as db:
            for t in ('messages', 'conversations', 'cc_files', 'cards', 'card_convs'):
                db.execute(f'DELETE FROM {t}')
        d = os.path.join(codex.ROOT, 'sessions', '2026', '08', '15')
        os.makedirs(d)
        self.path = os.path.join(d, 'rollout-2026-08-15T06-57-08-s1.jsonl')
        with open(self.path, 'w') as f:
            f.write('\n'.join([
                ln('06:57:08', 'session_meta', {'id': 's1', 'cwd': '/Users/me/Documents/Playground'}),
                ln('06:57:23', 'response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context>x</environment_context>'}]}),
                ln('06:57:23', 'event_msg', {'type': 'user_message', 'message': '\n# Files mentioned by the user:\n\n## a.pdf: /x/a.pdf\n\n## My request for Codex:\n把这些 pdf 转成 md\n\n<image name=[Image #1] path="/tmp/x.png">\n'}),
                ln('06:57:27', 'event_msg', {'type': 'agent_message', 'message': '我会先读文件。'}),
                ln('06:57:27', 'response_item', {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '我会先读文件。'}]}),
                ln('06:57:30', 'response_item', {'type': 'function_call', 'name': 'shell', 'arguments': '{}'}),
                ln('07:01:08', 'event_msg', {'type': 'user_message', 'message': '把转好的 md 合并成一份。\n'}),
                ln('07:01:09', 'event_msg', {'type': 'user_message', 'message': '## My request:\n?\n'}),
            ]) + '\n')

    def test_words_come_in(self):
        r = codex.sync(say=None)
        rows = store.q('SELECT role, text, source, conv_id FROM messages ORDER BY ts, id')
        self.assertEqual([(x['role'], x['text']) for x in rows], [
            ('human', '把这些 pdf 转成 md'), ('assistant', '我会先读文件。'), ('human', '把转好的 md 合并成一份。')])
        self.assertEqual({x['source'] for x in rows}, {'codex'})
        self.assertEqual(store.one('SELECT title FROM conversations')['title'], 'Codex·Playground：把这些 pdf 转成 md')
        self.assertEqual(r['new'], 3)

    def test_reviewer_and_voice_sessions_are_skipped(self):
        d = os.path.dirname(self.path)
        for name, meta in (('g', {'id': 'g1', 'source': {'subagent': {'other': 'guardian'}}, 'thread_source': 'subagent'}),
                           ('v', {'id': 'v1', 'source': 'vscode', 'thread_source': 'realtime_voice'})):
            with open(os.path.join(d, f'rollout-{name}.jsonl'), 'w') as f:
                f.write(ln('08:00:00', 'session_meta', meta) + '\n' + ln('08:00:01', 'event_msg', {
                    'type': 'user_message', 'message': 'The following is the Codex agent history … [104] user: 继续'}) + '\n')
        codex.sync(say=None)
        self.assertEqual({r['conv_id'] for r in store.q('SELECT conv_id FROM messages')}, {'codex-s1'})

    def test_unchanged_files_are_not_reread(self):
        codex.sync(say=None)
        self.assertEqual(codex.sync(say=None), {'files': 0, 'new': 0, 'days': []})

    def test_cards_skip_ai_and_coding_weight(self):
        codex.sync(say=None)
        _, segs = cards.segments('codex-s1')
        self.assertEqual({r['role'] for s in segs for r in s}, {'human'})
        self.assertEqual(sl._kind('codex'), 'cc')


if __name__ == '__main__':
    unittest.main()
