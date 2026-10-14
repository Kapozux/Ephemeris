"""ChatGPT 每天自动同步（2026-10-14）：只拉更新时间变了的对话、边拉边入库、记下拉到哪；手动导入的文件能先记成「已经有了」。
Chrome 是假的：evaluate 按脚本是「列」还是「拉」返回固定数据。"""
import _env  # noqa: F401
import json
import os
import tempfile
import unittest
from unittest import mock

import chatgpt_sync as gs
import store


class FakeChrome:
    calls = []

    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def goto(self, *a, **k):
        pass

    def set_cookie(self, name, value, domain=None):
        FakeChrome.calls.append(('cookie', name, domain))

    def evaluate(self, js, timeout=None):
        if '/backend-api/conversations?' in js:
            return {'conversations': [{'id': 'a', 'title': '旧的', 'update_time': '100'},
                                      {'id': 'b', 'title': '新的', 'update_time': '200'}]}
        ids = json.loads(js[js.index('const ids = ') + 12:js.index(', out = []')])
        FakeChrome.calls.append(('fetch', [x['id'] for x in ids]))
        return {'conversations': [{'uuid': 'chatgpt-' + x['id'], 'id': x['id'], 'name': x['title'], 'updated_at': x['update_time'],
                                   'messages': [{'role': 'human', 'created_at': '2026-10-14T01:00:00.000Z', 'text': '你好' + x['id']}]}
                                  for x in ids], 'failed': []}


class Sync(unittest.TestCase):
    def setUp(self):
        FakeChrome.calls = []
        with store.tx() as db:
            for t in ('messages', 'conversations', 'chatgpt_sync'):
                db.execute(f'DELETE FROM {t}')

    def run_sync(self):
        with mock.patch.object(gs, 'Chrome', FakeChrome), mock.patch.object(gs.claude_sync, 'wait_network', lambda *a: True), \
             mock.patch.object(gs, 'env', lambda k: {'CHATGPT_SESSION_TOKEN': 'tok'}.get(k, '')), \
             mock.patch.object(gs.claude_sync, '_archive', lambda p: None):
            return gs.sync()

    def test_only_changed_are_fetched_and_imported_as_chatgpt(self):
        gs.remember([('a', '100')])
        r = self.run_sync()
        self.assertEqual([c for c in FakeChrome.calls if c[0] == 'fetch'], [('fetch', ['b'])])
        self.assertIn(('cookie', '__Secure-next-auth.session-token', '.chatgpt.com'), FakeChrome.calls)
        self.assertEqual((r['fetched'], r['new_msgs']), (1, 1))
        self.assertEqual(store.one("SELECT source, conv_id FROM messages")['source'], 'chatgpt')
        self.assertEqual(gs.known(), {'a': '100', 'b': '200'})
        self.assertEqual(self.run_sync()['fetched'], 0)                    # 第二次：都没变

    def test_seed_from_manual_export(self):
        p = os.path.join(tempfile.mkdtemp(), 'chatgpt_export.json')
        json.dump({'exporter': 'ephemeris-chatgpt', 'conversations': [{'uuid': 'chatgpt-a', 'updated_at': '100'},
                                                                      {'uuid': 'chatgpt-b', 'updated_at': '200'}]}, open(p, 'w'))
        self.assertEqual(gs.seed_from_export(p), 2)
        self.run_sync()
        self.assertEqual([c for c in FakeChrome.calls if c[0] == 'fetch'], [])

    def test_rate_limited_keeps_what_it_got_and_resumes(self):
        orig = FakeChrome.evaluate

        def limited(self, js, timeout=None):
            r = orig(self, js, timeout)
            if 'const ids = ' in js:
                r = {**r, 'conversations': r['conversations'][:1], 'limited': True}
            return r
        with mock.patch.object(FakeChrome, 'evaluate', limited):
            r = self.run_sync()
        self.assertTrue(r['limited'])
        self.assertEqual(r['fetched'], 1)
        self.assertEqual(set(gs.known()), {'a'})                           # 拉到的记下了
        self.assertEqual([c for c in FakeChrome.calls if c[0] == 'fetch'][-1], ('fetch', ['a', 'b']))
        FakeChrome.calls = []
        self.run_sync()
        self.assertEqual([c for c in FakeChrome.calls if c[0] == 'fetch'], [('fetch', ['b'])])   # 下次只拉剩下的

    def test_time_formats_compare_equal(self):
        gs.remember([('a', '2026-10-14T01:00:00.000Z'), ('b', 1789000000.73)])
        self.assertEqual(gs.known()['a'], gs.norm(1791939600.0))
        self.assertEqual(gs.known()['b'], '1789000000')

    def test_not_configured_is_skipped(self):
        with mock.patch.object(gs, 'env', lambda k: ''):
            self.assertFalse(gs.configured())


if __name__ == '__main__':
    unittest.main()
