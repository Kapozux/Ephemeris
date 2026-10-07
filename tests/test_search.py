"""搜索（2026-10-07）：中文子串搜得到；不管消息怎么进来（导入、同步、改写）索引都跟着；片段先转义再标记。"""
import _env  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import index as idx
import search
import store


class Search(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'diaries', 'journal', 'journal_fts'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-search-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, uuid, title, msgs):
        p = os.path.join(self.tmp, f'{uuid}.json')
        with open(p, 'w') as f:
            json.dump({'conversations': [{'uuid': uuid, 'name': title, 'messages': [
                {'created_at': f'2026-03-03T0{i}:00:00Z', 'role': r, 'text': t} for i, (r, t) in enumerate(msgs)]}]}, f)
        idx.ingest(store.connect(), p)

    def test_chinese_substring(self):
        self.ingest('u1', '读书', [('human', '今天读鲁迅的《伤逝》，涓生太自私了'), ('assistant', '涓生的忏悔是不可靠的')])
        hits = search.messages('伤逝')
        self.assertEqual(len(hits), 1)
        self.assertIn('<mark>伤逝</mark>', hits[0]['snip'])
        self.assertEqual(len(search.messages('涓生')), 1)                  # 两个字也行（整表扫）
        self.assertEqual(len(search.messages('涓生', mine=False)), 2)      # 连 AI 说的
        self.assertEqual(search.messages('不存在的词'), [])

    def test_case_insensitive_and_like_chars(self):
        self.ingest('u1', 't', [('human', 'Zipf 定律 100% 成立，a_b')])
        self.assertEqual(len(search.messages('zipf')), 1)
        self.assertEqual(len(search.messages('100%')), 1)
        self.assertEqual(len(search.messages('a_b')), 1)
        self.assertEqual(search.messages('0%成'), [])                       # % 不当通配符

    def test_snippet_is_escaped(self):
        self.ingest('u1', 't', [('human', '<script>alert(1)</script> 伤逝')])
        snip = search.messages('伤逝')[0]['snip']
        self.assertNotIn('<script>', snip)
        self.assertIn('&lt;/script&gt;', snip)

    def test_reimport_keeps_index_in_step(self):
        self.ingest('u1', 't', [('human', '第一次说伤逝')])
        self.ingest('u1', 't', [('human', '第一次说伤逝'), ('human', '第二次说伤逝')])   # 同步：同一个窗口又来一遍
        self.assertEqual(len(search.messages('伤逝')), 2)
        self.assertEqual(store.one("SELECT COUNT(*) n FROM msg_fts WHERE text LIKE '%伤逝%'")['n'], 2)
        store.run('DELETE FROM messages')
        self.assertEqual(search.messages('伤逝'), [])

    def test_conversations_by_title_or_text(self):
        self.ingest('u1', '鲁迅精读', [('human', '开始')])
        self.ingest('u2', '别的', [('human', '鲁迅一'), ('assistant', '鲁迅二')])
        hits = search.conversations('鲁迅')
        cid1 = store.one("SELECT id FROM conversations WHERE title='鲁迅精读'")['id']
        cid2 = store.one("SELECT id FROM conversations WHERE title='别的'")['id']
        self.assertEqual(hits[cid1]['hits'], 0)
        self.assertEqual(hits[cid2]['hits'], 2)
        self.assertIn('<mark>鲁迅</mark>', hits[cid2]['snip'])


if __name__ == '__main__':
    unittest.main()
