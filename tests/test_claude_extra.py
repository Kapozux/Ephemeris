"""列表里看不到的 claude.ai 窗口（10-29 用户：无痕窗口，「能在 Ephemeris 某个地方添加 claude.ai/chat/xxxx，每日下载时也去下」）：
贴链接记下 id；同步时按 id 拉，标题和时间用对话自己带的；拉不到记在它那行、不算同步失败；不拉了也不删已经拉进来的。"""
import _env  # noqa: F401
import json
import shutil
import tempfile
import unittest

import claude_sync
import index as idx
import store

LISTED = '11111111-1111-1111-1111-111111111111'
GHOST = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
GONE = '99999999-9999-9999-9999-999999999999'


def conv(uuid, name, text):
    return {'uuid': uuid, 'name': name, 'model': None, 'created_at': '2026-10-20T04:00:00Z', 'updated_at': '2026-10-20T05:00:00Z',
            'messages': [{'role': 'human', 'created_at': '2026-10-20T04:00:00Z', 'text': text, 'attachments': []}]}


class FakeChrome:
    """页面里的 fetch 换成假的：列表只有 LISTED；按 id 拉，GHOST 拉得到（无痕窗口），GONE 404。"""
    asked = []

    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def goto(self, *a, **kw):
        pass

    def set_cookie(self, *a):
        pass

    def evaluate(self, js, timeout=0):
        if 'chat_conversations?limit' in js:
            return {'conversations': [{'uuid': LISTED, 'name': '列表里的', 'created_at': '2026-10-20T04:00:00Z',
                                       'updated_at': '2026-10-20T05:00:00Z'}]}
        ids = json.loads(js.split(', ids = ', 1)[1].split(', out = []', 1)[0])
        FakeChrome.asked = ids
        have = {LISTED: conv(LISTED, '列表里的', '平常的窗口'), GHOST: conv(GHOST, '无痕的', '无痕窗口里说的话')}
        return {'conversations': [have[i['uuid']] for i in ids if i['uuid'] in have],
                'failed': [{'uuid': i['uuid'], 'error': 'Error: HTTP 404'} for i in ids if i['uuid'] not in have]}


class Extra(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'claude_sync', 'claude_extra', 'sync_runs'):
                db.execute(f'DELETE FROM {t}')
        self.saved = claude_sync.Chrome, claude_sync.env, claude_sync.wait_network, idx.IMPORT_DIR
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-cx-')
        claude_sync.Chrome = FakeChrome
        claude_sync.env = lambda k: {'CLAUDE_SESSION_KEY': 'sk', 'CLAUDE_ORG_ID': 'org'}.get(k)
        claude_sync.wait_network = lambda *a, **kw: True
        idx.IMPORT_DIR = self.tmp

    def tearDown(self):
        claude_sync.Chrome, claude_sync.env, claude_sync.wait_network, idx.IMPORT_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_paste_links(self):
        got = claude_sync.add_extra(f'https://claude.ai/chat/{GHOST.upper()}\nhttps://claude.ai/chat/{GONE}?x=1 {GHOST}')
        self.assertEqual(got, [GHOST, GONE])
        self.assertEqual(len(claude_sync.extras()), 2)
        with self.assertRaises(ValueError):
            claude_sync.add_extra('https://claude.ai/new')

    def test_sync_fetches_extras_and_keeps_going_when_one_is_gone(self):
        claude_sync.add_extra(f'claude.ai/chat/{GHOST} claude.ai/chat/{GONE} claude.ai/chat/{LISTED}')
        r = claude_sync.sync()
        self.assertEqual({i['uuid'] for i in FakeChrome.asked}, {LISTED, GHOST, GONE})   # 列表里有的不重复拉
        self.assertEqual(r['fetched'], 2)
        self.assertEqual(store.one("SELECT text FROM messages WHERE conv_id=?", GHOST)['text'], '无痕窗口里说的话')
        self.assertEqual(store.one('SELECT title FROM conversations WHERE id=?', GHOST)['title'], '无痕的')
        rows = {x['uuid']: x for x in claude_sync.extras()}
        self.assertEqual(rows[GHOST]['title'], '无痕的')
        self.assertIsNone(rows[GHOST]['last_error'])
        self.assertIn('404', rows[GONE]['last_error'])
        self.assertTrue(claude_sync.last_run()['ok'])

        r = claude_sync.sync()                     # 第二天：列表里的没变不拉，手动加的照样按 id 拉
        self.assertEqual({i['uuid'] for i in FakeChrome.asked}, {GHOST, GONE})
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages WHERE conv_id=?', GHOST)['n'], 1)

        claude_sync.remove_extra(GHOST)            # 不拉了：拉进来的照样留着
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages WHERE conv_id=?', GHOST)['n'], 1)

    def test_only_a_gone_extra_is_not_a_failed_sync(self):
        claude_sync.add_extra(GONE)
        claude_sync._remember([(LISTED, '2026-10-20T05:00:00Z')])
        r = claude_sync.sync()
        self.assertEqual(r['fetched'], 0)
        self.assertTrue(claude_sync.last_run()['ok'])


if __name__ == '__main__':
    unittest.main()
