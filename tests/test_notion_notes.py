"""Notion 笔记库（11-03）：拉回本地只给「问」查；密钥 / 密码存之前就打码（用户：什么都不能出现在 GitHub、公网上）；
Notion 里删了本地留着；look.py notes / note 查得到，屏蔽的天查不到；回答里能引 [[n:…]]。全是假数据。"""
import _env  # noqa: F401
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import hidden
import look
import qa
import store
from sources import notion_notes as nn

FAKE_KEY = 'sk-' + 'a1B2' * 10                                  # 拼出来的假 key，不是真的


def page(pid, title, edited='2026-11-01T00:00:00.000Z', date=None, tags=('课程',)):
    return {'id': pid, 'created_time': '2026-09-01T00:00:00.000Z', 'last_edited_time': edited, 'properties': {
        'Notes': {'type': 'title', 'title': [{'plain_text': title}]},
        '标签': {'type': 'multi_select', 'multi_select': [{'name': t} for t in tags]},
        '日期': {'type': 'date', 'date': {'start': date} if date else None}}}


def para(text):
    return {'type': 'paragraph', 'paragraph': {'rich_text': [{'plain_text': text, 'annotations': {}}]}, 'has_children': False}


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        look.main(list(argv))
    return buf.getvalue()


class Redact(unittest.TestCase):
    def test_masks(self):
        for raw in (FAKE_KEY, 'ntn_' + 'x9' * 15, 'ghp_' + 'Z8' * 12, 'AIza' + 'q7' * 18, 'AKIA' + 'ABCDEFGH12345678',
                    '0f' * 20, 'https://me:hunter2pass@example.com/x'):
            with self.subTest(raw=raw[:8]):
                out, n = nn.redact(f'前面 {raw} 后面')
                self.assertGreater(n, 0)
                self.assertNotIn(raw.split('@')[0][-10:], out)
        out, n = nn.redact('wifi 密码：abc12345\nAPI key = qwerty\nPassword: Tr0ub4dor')
        self.assertEqual(n, 3)
        for s in ('abc12345', 'qwerty', 'Tr0ub4dor'):
            self.assertNotIn(s, out)
        pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIx\n-----END RSA PRIVATE KEY-----'
        self.assertNotIn('MIIx', nn.redact(pem)[0])

    def test_leaves_normal_text(self):
        text = '物理 IA 的数据：12.5 cm，第 3 组。链接 https://example.com/some-long-article-title-here-2026 看一下'
        self.assertEqual(nn.redact(text), (text, 0))


class Sync(unittest.TestCase):
    def setUp(self):
        store.run('DELETE FROM notes')
        store.run('DELETE FROM qa_hidden')
        self.pages = [page('p1', '物理 IA 计划', date='2026-10-02'), page('p2', '各种 key')]
        self.blocks = {'p1': [para('测量单摆周期，做五组')], 'p2': [para(f'OpenAI {FAKE_KEY}'), para('邮箱密码：hunter22')]}

    def fake(self, method, url, body=None, **k):
        if '/query' in url:
            return {'results': self.pages, 'has_more': False}
        pid = url.split('/blocks/')[1].split('/')[0]
        return {'results': self.blocks[pid], 'has_more': False}

    def sync(self, **k):
        with mock.patch.object(nn.tn, '_req', self.fake), mock.patch.object(nn.time, 'sleep', lambda s: None), \
                mock.patch.dict(os.environ, {'NOTION_NOTES_DS': 'ds-test'}):
            return nn.sync(say=lambda m: None, **k)

    def test_sync_stores_masked(self):
        r = self.sync()
        self.assertEqual((r['pages'], r['fetched'], r['masked']), (2, 2, 2))
        p2 = store.one("SELECT text, masked FROM notes WHERE page_id='p2'")
        self.assertNotIn(FAKE_KEY, p2['text'])
        self.assertNotIn('hunter22', p2['text'])
        self.assertEqual(p2['masked'], 2)
        everything = json.dumps(store.q('SELECT * FROM notes'), ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, everything)                    # 哪一列都没有原文
        p1 = store.one("SELECT date, tags FROM notes WHERE page_id='p1'")
        self.assertEqual(p1['date'], '2026-10-02')
        self.assertEqual(json.loads(p1['tags']), {'标签': ['课程']})

    def test_incremental_and_gone_kept(self):
        self.sync()
        self.blocks['p1'] = [para('改过了')]
        self.assertEqual(self.sync()['fetched'], 0)               # 没改的不重拉
        self.pages = [page('p1', '物理 IA 计划', edited='2026-11-02T00:00:00.000Z', date='2026-10-02')]
        r = self.sync()
        self.assertEqual((r['fetched'], r['gone']), (1, 1))
        gone = store.one("SELECT text, gone_at FROM notes WHERE page_id='p2'")
        self.assertTrue(gone['gone_at'])                          # Notion 里删了：本地留着、只标记
        self.assertIn('〔已隐藏〕', gone['text'])

    def test_look_and_hidden(self):
        self.sync()
        out = run('notes', '单摆')
        self.assertIn('[n:p1]', out)
        self.assertIn('物理 IA 计划', out)
        self.assertIn('测量单摆', run('note', 'n:p1'))
        self.assertIn('遮了 2 处', run('note', 'n:p2'))
        self.assertNotIn(FAKE_KEY, run('sql', 'SELECT text FROM notes'))
        hidden.set_('day', '2026-10-02')
        self.assertNotIn('[n:p1]', run('notes', '单摆'))
        self.assertIn('没有这页', run('note', 'n:p1'))

    def test_cite(self):
        self.sync()
        body, cites = qa._deep_cites('IA 是做单摆 [[n:p1]]，编的 [[n:nope]]。')
        self.assertEqual([c['id'] for c in cites], ['n:p1'])
        self.assertEqual(cites[0]['who'], 'note')
        self.assertNotIn('nope', body)
        self.assertEqual(qa._seen_ids('  [n:p1] 2026-10-02《物理 IA 计划》'), {'n:p1'})


if __name__ == '__main__':
    unittest.main()
