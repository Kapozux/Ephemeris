"""手写日记：Notion 的格式和图片带回来，但喂模型 / 建索引的纯文字跟以前一模一样；
只有正文真的变了才重新记进主题账本；打开页面时悄悄拉最近改过的几页。"""
import _env  # noqa: F401
import io
import os
import shutil
import sqlite3
import tempfile
import unittest

import journal
import store
import to_notion as tn


def rt(text, bold=False, strike=False, code=False, href=None):
    return {'plain_text': text, 'href': href,
            'annotations': {'bold': bold, 'strikethrough': strike, 'code': code}}


def blk(t, *rich, **body):
    return {'type': t, 'has_children': False, t: {'rich_text': list(rich), **body}}


class Format(unittest.TestCase):
    def test_inline_marks(self):
        b = blk('paragraph', rt('今天'), rt('很累 ', bold=True), rt('但'), rt('写完了', strike=True),
                rt('文书', href='https://example.com/a'), rt('ls', code=True))
        self.assertEqual(journal._line(b), '今天**很累** 但~~写完了~~[文书](https://example.com/a)`ls`')

    def test_numbered_list_counts_and_resets(self):
        lines = journal._render([blk('numbered_list_item', rt('一')), blk('numbered_list_item', rt('二')),
                                 blk('paragraph', rt('中间')), blk('numbered_list_item', rt('重来'))])
        self.assertEqual(lines, ['1. 一', '2. 二', '中间', '1. 重来'])

    def test_image_saved_or_placeholder(self):
        img = {'type': 'image', 'has_children': False, 'image': {'caption': [rt('晚饭')], 'type': 'file', 'file': {'url': 'x'}}}
        self.assertEqual(journal._line(img, image=lambda b: '/journal-files/p/abc.jpg'), '![晚饭](/journal-files/p/abc.jpg)')
        self.assertEqual(journal._line(img, image=lambda b: None), '[图片：晚饭]')     # 下载不了：退回占位

    def test_plain_is_exactly_the_old_text(self):
        new = ('# 今天\n1. 改**文书**\n- [链接](https://a.com)里的字\n![晚饭](/journal-files/p/a.jpg)\n'
               '![](/journal-files/p/b.png)\n~~划掉~~和`代码`')
        old = '# 今天\n- 改文书\n- 链接里的字\n[图片：晚饭]\n[图片]\n划掉和代码'
        self.assertEqual(journal.plain(new), old)
        self.assertEqual(journal.digest(new), journal.digest(old))       # 只是格式变了 = 没变


class Digested(unittest.TestCase):
    PAGE = {'page_id': 'p-1', 'date': '2026-03-03', 'title': '2026-3-3', 'last_edited': '2026-03-03T10:00:00.000Z'}

    def setUp(self):
        with store.tx() as db:
            db.execute('DELETE FROM journal')
            db.execute('DELETE FROM journal_fts')

    def test_new_page_is_dirty_until_digested(self):
        journal._store(self.PAGE, '今天很累', 0)
        self.assertEqual(journal.dirty_dates(), ['2026-03-03'])
        journal.mark_digested(['2026-03-03'])
        self.assertEqual(journal.dirty_dates(), [])

    def test_touched_but_same_text_is_not_dirty(self):
        journal._store(self.PAGE, '今天很累', 0)
        journal.mark_digested(['2026-03-03'])
        # Ephemeris 往这页末尾推 AI 日记 → Notion 的修改时间变了，手写正文没变
        journal._store({**self.PAGE, 'last_edited': '2026-03-04T08:31:00.000Z'}, '今天**很累**', 1)
        self.assertEqual(journal.dirty_dates(), [])

    def test_edited_text_is_dirty(self):
        journal._store(self.PAGE, '今天很累', 0)
        journal.mark_digested(['2026-03-03'])
        journal._store({**self.PAGE, 'last_edited': '2026-03-04T21:00:00.000Z'}, '今天很累，晚上又写了一段', 0)
        self.assertEqual(journal.dirty_dates(), ['2026-03-03'])

    def test_model_and_search_see_plain_text(self):
        journal._store(self.PAGE, '改 **essay** 草稿\n![晚饭](/journal-files/p/a.jpg)', 0)
        self.assertEqual(journal.text_for('2026-03-03'), '改 essay 草稿\n[图片：晚饭]')
        self.assertEqual(journal.for_date('2026-03-03')[0]['chars'], len('改 essay 草稿\n[图片：晚饭]'))
        snip = journal.search('essay')[0]['snip']
        self.assertNotIn('**', snip)                                        # 索引里是纯文字
        self.assertNotIn('journal-files', snip)


class Refresh(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            db.execute('DELETE FROM journal')
        self.saved = tn._req, tn.token, tn.DATA_SOURCE_ID, journal.page_text
        journal._recent['at'] = 0.0
        tn.token, tn.DATA_SOURCE_ID = (lambda: 'secret'), 'ds'
        self.fetched = []
        journal.page_text = lambda pid: (self.fetched.append(pid), ('晚上写的', 0))[1]
        page = lambda pid, title, edited, icon=None: {  # noqa: E731
            'id': pid, 'last_edited_time': edited, 'icon': {'emoji': icon} if icon else None,
            'properties': {tn.TITLE_PROP: {'title': [{'plain_text': title}]}}}
        tn._req = lambda method, url, body=None: {'results': [
            page('p-new', '2026-3-5', 'e1'), page('p-ai', '2026-3-5', 'e2', icon=tn.ICON), page('p-x', '不是日期', 'e3')]}

    def tearDown(self):
        tn._req, tn.token, tn.DATA_SOURCE_ID, journal.page_text = self.saved

    def test_pulls_only_changed_date_pages_and_throttles(self):
        self.assertEqual(journal.refresh_recent(), {'changed': ['2026-03-05']})
        self.assertEqual(self.fetched, ['p-new'])                          # Ephemeris 建的、不是日期页的都不拉
        self.assertEqual(journal.refresh_recent(), {'skipped': True})      # 两分钟内不再查
        journal._recent['at'] = 0.0
        self.assertEqual(journal.refresh_recent(), {'changed': []})        # 没变的不再拉正文
        self.assertEqual(self.fetched, ['p-new'])

    def test_disabled_without_token(self):
        tn.token = lambda: ''
        self.assertEqual(journal.refresh_recent(), {'disabled': True})


class Images(unittest.TestCase):
    def setUp(self):
        self.saved = journal.FILES_DIR, journal.urllib.request.urlopen
        journal.FILES_DIR = tempfile.mkdtemp(prefix='ephemeris-img-')
        self.downloads = 0

        class Resp(io.BytesIO):
            headers = {'Content-Type': 'image/png'}
            def __enter__(s):
                return s
            def __exit__(s, *a):
                return False

        def fake_open(req, timeout=None):
            self.downloads += 1
            return Resp(b'\x89PNG fake')
        journal.urllib.request.urlopen = fake_open

    def tearDown(self):
        shutil.rmtree(journal.FILES_DIR, ignore_errors=True)
        journal.FILES_DIR, journal.urllib.request.urlopen = self.saved

    def img(self, url):
        return {'type': 'image', 'image': {'type': 'file', 'file': {'url': url}}}

    def test_download_once_per_image(self):
        a = journal._save_image('page-1', self.img('https://s3.example.com/ws/file-1/IMG.png?X-Amz-Signature=1'))
        b = journal._save_image('page-1', self.img('https://s3.example.com/ws/file-1/IMG.png?X-Amz-Signature=2'))
        self.assertTrue(a.startswith('/journal-files/page-1/') and a.endswith('.png'))
        self.assertEqual((a, self.downloads), (b, 1))                      # 签名变了还是同一张图：不重下
        self.assertTrue(os.path.exists(os.path.join(journal.FILES_DIR, 'page-1', os.path.basename(a))))

    def test_route_serves_file_but_not_outside(self):
        import app as ephemeris
        url = journal._save_image('page-2', self.img('https://s3.example.com/ws/file-2/x.png'))
        c = ephemeris.app.test_client()
        with c.get(url) as r:
            self.assertEqual(r.data, b'\x89PNG fake')
        with c.get('/journal-files/../store.py') as r:
            self.assertEqual(r.status_code, 404)


class Migration(unittest.TestCase):
    def test_old_pages_count_as_already_digested(self):
        saved = store.DB_PATH, store._conn
        store.DB_PATH, store._conn = os.path.join(tempfile.mkdtemp(prefix='ephemeris-old-'), 'old.db'), None
        try:
            raw = sqlite3.connect(store.DB_PATH)
            raw.executescript("""CREATE TABLE journal (page_id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT, text TEXT,
                                   chars INTEGER, stripped INTEGER DEFAULT 0, last_edited TEXT, fetched_at TEXT);
                                 INSERT INTO journal VALUES ('p', '2025-01-01', 't', '去年写的 - 列表', 9, 0, 'e', 'f');""")
            raw.close()
            store.connect()
            self.assertEqual(journal.dirty_dates(), [])                    # 升级不会把老的手写全部重抽一遍
        finally:
            store._conn.close()
            store.DB_PATH, store._conn = saved


if __name__ == '__main__':
    unittest.main()
