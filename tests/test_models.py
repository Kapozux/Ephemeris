"""哪条回复是哪个模型写的（10-29）：来源记了就进 msg_models，没记的不猜；补模型不会改消息本身。"""
import _env  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import codex
import index as idx
import store


class Models(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'msg_models', 'conversations_dup', 'messages_dup'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-models-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def models(self):
        return {r['role']: r['model'] for r in store.q(
            'SELECT m.role, x.model FROM messages m LEFT JOIN msg_models x ON x.msg_id = m.id')}

    def test_claude_sync_window_model_goes_to_replies_only(self):
        p = os.path.join(self.tmp, 'sync.json')
        json.dump({'conversations': [{'uuid': 'u1', 'name': 't', 'model': 'claude-opus-5-5', 'messages': [
            {'role': 'human', 'created_at': '2026-10-01T04:00:00Z', 'text': '问'},
            {'role': 'assistant', 'created_at': '2026-10-01T04:01:00Z', 'text': '答'}]}]}, open(p, 'w'))
        idx.ingest(store.connect(), p)
        self.assertEqual(self.models(), {'human': None, 'assistant': 'claude-opus-5-5'})

    def test_export_without_model_stays_unknown(self):
        p = os.path.join(self.tmp, 'old.json')
        json.dump({'conversations': [{'uuid': 'u2', 'name': 't', 'messages': [
            {'role': 'assistant', 'created_at': '2026-10-01T04:01:00Z', 'text': '答'}]}]}, open(p, 'w'))
        idx.ingest(store.connect(), p)
        self.assertEqual(self.models(), {'assistant': None})

    def test_codex_takes_model_from_turn_context(self):
        p = os.path.join(self.tmp, 'rollout-x.jsonl')
        lines = [{'type': 'session_meta', 'payload': {'id': 's', 'cwd': '/a/proj'}},
                 {'type': 'turn_context', 'payload': {'model': 'gpt-5.6-sol'}},
                 {'type': 'event_msg', 'timestamp': '2026-10-01T04:00:00Z', 'payload': {'type': 'user_message', 'message': 'hi'}},
                 {'type': 'response_item', 'timestamp': '2026-10-01T04:01:00Z',
                  'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'yo'}]}}]
        with open(p, 'w') as f:
            f.write('\n'.join(json.dumps(x) for x in lines))
        _, _, msgs = codex._parse(p)
        self.assertEqual([(m[2], m[4]) for m in msgs], [('human', None), ('assistant', 'gpt-5.6-sol')])

    def test_synthetic_is_not_a_model(self):
        idx.note_models(store.connect(), [('a', '<synthetic>'), ('b', None), ('c', 'x')])
        self.assertEqual([r['msg_id'] for r in store.q('SELECT msg_id FROM msg_models')], ['c'])


if __name__ == '__main__':
    unittest.main()
