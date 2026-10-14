"""Ephemeris 当备份用（2026-10-07）：什么情况下都不能因为上游「看起来是空的」就把以前的东西丢掉。

- 导入旧聊天：最近几天的日记作废重写，更早的日记不动、只记「后来补进了几条」
- Notion 手写：Notion 里没了的页本地留着；一下少了一大片当故障，什么都不标
- 备份：核心数据按行合并，本地少了的行在备份里留着，改掉的旧版本另存
- claude.ai 同步：原始数据压缩存档
"""
import _env  # noqa: F401
import gzip
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import backup
import claude_sync
import days
import index as idx
import journal
import store


def day(n):
    return (datetime.fromisoformat(days.today()) - timedelta(days=n)).date().isoformat()


def export(path, uuid, msgs):
    """一份 claude_json 导出：msgs = [(日期, 文字)]，都在当地中午。"""
    with open(path, 'w') as f:
        json.dump({'conversations': [{'uuid': uuid, 'name': 't', 'messages': [
            {'created_at': f'{d}T04:00:00Z', 'role': 'human', 'text': t} for d, t in msgs]}]}, f)


class ImportKeepsOldDiaries(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'diaries'):
                db.execute(f'DELETE FROM {t}')
        self.old, self.recent = day(30), day(1)
        for d in (self.old, self.recent):
            store.run("INSERT INTO diaries(date, headline) VALUES (?, '当时写的')", d)
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-imp-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_diary_kept_recent_invalidated(self):
        p = os.path.join(self.tmp, 'a.json')
        export(p, 'u1', [(self.old, '一个月前在 ChatGPT 聊的'), (self.old, '又一句'), (self.recent, '昨天晚到的一句')])
        r = idx.ingest(store.connect(), p)
        self.assertEqual(r['diaries_invalidated'], 1)
        self.assertEqual(store.one('SELECT headline, added_later FROM diaries WHERE date=?', self.old),
                         {'headline': '当时写的', 'added_later': 2})
        self.assertIsNone(store.one('SELECT 1 FROM diaries WHERE date=?', self.recent))

    def test_reimporting_same_file_changes_nothing(self):
        p = os.path.join(self.tmp, 'a.json')
        export(p, 'u1', [(self.old, '一句')])
        idx.ingest(store.connect(), p)
        idx.ingest(store.connect(), p)
        self.assertEqual(store.one('SELECT added_later FROM diaries WHERE date=?', self.old)['added_later'], 1)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages')['n'], 1)


class OfficialClaudeExport(unittest.TestCase):
    """claude.ai 设置里「导出数据」下载的 zip：conversations.json 里是 chat_messages + sender，正文有时只在 content 里。"""
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-zip-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zip_from_claude_settings(self):
        import zipfile
        convs = [{'uuid': 'u-1', 'name': '学做菜', 'chat_messages': [
            {'sender': 'human', 'text': '红烧肉怎么做', 'created_at': '2026-03-03T04:00:00Z'},
            {'sender': 'assistant', 'text': '', 'content': [{'type': 'text', 'text': '先焯水'}], 'created_at': '2026-03-03T04:01:00Z'}]}]
        p = os.path.join(self.tmp, 'data-2026-03-04.zip')
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('conversations.json', json.dumps(convs, ensure_ascii=False))
            z.writestr('users.json', '[]')
        self.assertEqual(idx.sniff(p), ('claude_json', 'claude'))
        r = idx.ingest(store.connect(), p)
        self.assertEqual((r['n_convs'], r['n_msgs']), (1, 2))
        rows = store.q('SELECT role, text FROM messages ORDER BY ts')
        self.assertEqual([(x['role'], x['text']) for x in rows], [('human', '红烧肉怎么做'), ('assistant', '先焯水')])

    def test_official_export_matches_daily_sync(self):
        """官方导出的 text 把思考过程、工具调用也拼进去了；只拿 content 里的 text 块、直接接上，跟每日同步那条是同一个 id，不会多一份。"""
        sync = os.path.join(self.tmp, 'sync.json')
        with open(sync, 'w') as f:
            json.dump({'conversations': [{'uuid': 'u-2', 'name': '读文件', 'messages': [
                {'role': 'human', 'text': '你读一下', 'created_at': '2026-05-06T08:18:35Z'},
                {'role': 'assistant', 'text': '文件很长。读完了。', 'created_at': '2026-05-06T08:19:15Z'}]}]}, f, ensure_ascii=False)
        idx.ingest(store.connect(), sync)
        export = os.path.join(self.tmp, 'conversations.json')
        with open(export, 'w') as f:
            json.dump([{'uuid': 'u-2', 'name': '读文件', 'chat_messages': [
                {'sender': 'human', 'text': '你读一下', 'content': [{'type': 'text', 'text': '你读一下'}], 'created_at': '2026-05-06T08:18:35Z'},
                {'sender': 'assistant', 'text': 'The user wants me to read it.\n```\nThis block is not supported\n```\n文件很长。读完了。',
                 'content': [{'type': 'thinking', 'thinking': 'The user wants me to read it.'}, {'type': 'tool_use'},
                             {'type': 'text', 'text': '文件很长。'}, {'type': 'tool_result'}, {'type': 'text', 'text': '读完了。'}],
                 'created_at': '2026-05-06T08:19:15Z'}]}], f, ensure_ascii=False)
        r = idx.ingest(store.connect(), export)
        self.assertEqual(r['n_msgs_new'], 0)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages')['n'], 2)


class OtherMarkdownExports(unittest.TestCase):
    """2026-10-14 从下载里补导：Google AI Studio 的 markdown、同一个插件导出的 ChatGPT transcript。"""
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-md-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, 'w') as f:
            f.write(text)
        return p

    def test_aistudio(self):
        p = self.write('Adim1-conversation-2026-03-04-18-10-38.md', '''# AI Studio Conversation

**User:**

User  docs  progress_activity 40,304 tokens

---

**Model:**

Model 10:40 PM

![Thinking](x)

 Thoughts **Considering**
 Expand to view model thoughts 
 chevron_right

---

**User:**

User 10:40 PM你认为这个频道中文聊还是英文

**Model:**

结论：混合模式。
 arrow_split See branched conversations

---

**User:**

User 8:15 AM这是去年底的对话

**Model:**

Model 8:15 AMThinking Process:
1. 想一想
''')
        self.assertEqual(idx.sniff(p), ('aistudio_md', 'gemini'))
        r = idx.ingest(store.connect(), p)
        self.assertEqual(r['n_msgs'], 3)
        rows = store.q('SELECT date, hhmm, role, text FROM messages ORDER BY ts')
        self.assertEqual([(x['date'], x['role'], x['text']) for x in rows],
                         [('2026-03-03', 'human', '你认为这个频道中文聊还是英文'),        # 22:40 比导出时间 18:10 晚：前一天
                          ('2026-03-03', 'assistant', '结论：混合模式。'),
                          ('2026-03-04', 'human', '这是去年底的对话')])               # 思考过程那段不要

    def test_chatgpt_transcript(self):
        p = self.write('密码强度_transcript.md', '''# Conversation with ChatGPT

**密码强度测量解析**

---

## Human (Aug 1, 2026, 3:42 PM):

这题为什么选 C

---

## ChatGPT (Aug 1, 2026, 3:42 PM):

因为两条结论要分开对应。
''')
        self.assertEqual(idx.sniff(p), ('claude_md', 'chatgpt'))
        idx.ingest(store.connect(), p)
        rows = store.q('SELECT source, role, text FROM messages ORDER BY ts, role DESC')
        self.assertEqual([(x['source'], x['role']) for x in rows], [('chatgpt', 'human'), ('chatgpt', 'assistant')])
        self.assertEqual(rows[0]['text'], '这题为什么选 C')


class NotionNeverDeletes(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            db.execute('DELETE FROM journal')
        for i in range(10):
            journal._store({'page_id': f'p{i}', 'date': f'2026-01-{i + 1:02d}', 'title': 't', 'last_edited': 'e'}, f'第 {i} 篇', 0)
        self.saved = journal.list_pages, journal.page_text
        journal.page_text = lambda pid: ('又出现了', 0)

    def tearDown(self):
        journal.list_pages, journal.page_text = self.saved

    def listed(self, ids):
        journal.list_pages = lambda: [{'page_id': f'p{i}', 'date': f'2026-01-{i + 1:02d}', 'title': 't', 'last_edited': 'e'}
                                      for i in ids]

    def test_page_gone_in_notion_stays_here(self):
        self.listed(range(1, 10))                                        # p0 在 Notion 里删了
        r = journal.sync(say=lambda m: None)
        self.assertEqual((r['gone'], r['warning']), (1, None))
        row = store.one("SELECT text, gone_at FROM journal WHERE page_id='p0'")
        self.assertEqual(row['text'], '第 0 篇')
        self.assertTrue(row['gone_at'])
        self.assertEqual(journal.for_date('2026-01-01')[0]['text'], '第 0 篇')

    def test_mass_disappearance_is_treated_as_a_fault(self):
        self.listed([])                                                  # 权限没了 / 连不上：一页都没列出来
        r = journal.sync(say=lambda m: None)
        self.assertIn('少了 10 页', r['warning'])
        self.assertEqual(store.one('SELECT COUNT(*) n FROM journal WHERE gone_at IS NOT NULL')['n'], 0)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM journal')['n'], 10)

    def test_page_that_comes_back_is_unmarked(self):
        self.listed(range(1, 10))
        journal.sync(say=lambda m: None)
        self.listed(range(10))
        store.run("UPDATE journal SET last_edited='old' WHERE page_id='p0'")   # 回来了，而且改过
        journal.sync(say=lambda m: None)
        self.assertIsNone(store.one("SELECT gone_at FROM journal WHERE page_id='p0'")['gone_at'])


class BackupNeverShrinks(unittest.TestCase):
    REL = '数据/checkins.jsonl'

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ephemeris-bak-')
        with store.tx() as db:
            db.execute('DELETE FROM backup_keys')
            db.execute('DELETE FROM backup_files')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rows):
        w = backup._Writer(self.root)
        w.put_rows(self.REL, rows, 'id', 'checkins')
        w.commit()
        return w

    def backed_up(self):
        with open(os.path.join(self.root, self.REL), encoding='utf-8') as f:
            return {json.loads(line)['id']: json.loads(line) for line in f if line.strip()}

    def test_rows_lost_locally_stay_in_backup(self):
        self.write([{'id': 1, 'word': '开心'}, {'id': 2, 'word': '低落'}])
        w = self.write([{'id': 2, 'word': '低落'}, {'id': 3, 'word': '平稳'}])   # 本地 1 没了
        self.assertEqual(sorted(self.backed_up()), [1, 2, 3])
        self.assertEqual(w.kept, 1)
        w = self.write([])                                                    # 本地整张表空了
        self.assertEqual(sorted(self.backed_up()), [1, 2, 3])

    def test_changed_row_keeps_old_version(self):
        self.write([{'id': 1, 'word': '开心'}])
        w = self.write([{'id': 1, 'word': '崩溃'}])
        self.assertEqual(self.backed_up()[1]['word'], '崩溃')
        with open(os.path.join(self.root, '旧版本', 'checkins.jsonl'), encoding='utf-8') as f:
            old = [json.loads(line) for line in f]
        self.assertEqual(old[0]['row'], {'id': 1, 'word': '开心'})
        self.assertEqual(w.versions, 1)

    def test_conversation_stats_changing_is_not_an_old_version(self):
        w = backup._Writer(self.root)
        w.put_rows('数据/conversations.jsonl', [{'id': 'c', 'n_msgs': 3}], 'id', 'conversations')
        w.commit()
        w = backup._Writer(self.root)
        w.put_rows('数据/conversations.jsonl', [{'id': 'c', 'n_msgs': 5}], 'id', 'conversations')
        w.commit()
        self.assertEqual(w.versions, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, '旧版本', 'conversations.jsonl')))

    def test_unreadable_backup_is_not_overwritten(self):
        self.write([{'id': 1, 'word': '开心'}, {'id': 2, 'word': '低落'}])
        with open(os.path.join(self.root, self.REL), 'w', encoding='utf-8') as f:
            f.write('坏掉的文件\n')
        w = self.write([{'id': 2, 'word': '低落'}])
        self.assertTrue(w.warnings)
        with open(os.path.join(self.root, self.REL), encoding='utf-8') as f:
            self.assertEqual(f.read(), '坏掉的文件\n')                      # 宁可这次不备，也不拿更少的盖掉

    def test_restore_reads_merged_files(self):
        """合并后的文件还能原样还原（格式没变）。"""
        self.write([{'id': 1, 'word': '开心'}])
        self.write([])
        self.assertEqual(list(self.backed_up()), [1])


class ClaudeRawArchive(unittest.TestCase):
    def test_sync_payload_is_archived(self):
        saved = idx.IMPORT_DIR
        idx.IMPORT_DIR = tempfile.mkdtemp(prefix='ephemeris-arc-')
        try:
            src = os.path.join(idx.IMPORT_DIR, 'x.json')
            with open(src, 'w') as f:
                json.dump({'conversations': [{'uuid': 'u'}]}, f)
            claude_sync._archive(src)
            files = os.listdir(os.path.join(idx.IMPORT_DIR, 'claude-sync'))
            self.assertEqual(len(files), 1)
            with gzip.open(os.path.join(idx.IMPORT_DIR, 'claude-sync', files[0]), 'rt') as f:
                self.assertEqual(json.load(f)['conversations'][0]['uuid'], 'u')
        finally:
            shutil.rmtree(idx.IMPORT_DIR, ignore_errors=True)
            idx.IMPORT_DIR = saved


class NoRebuildFromScratch(unittest.TestCase):
    def test_wipe_and_rebuild_is_gone(self):
        self.assertFalse(hasattr(idx, 'reindex_all'))
        self.assertFalse(hasattr(idx, 'SOURCES'))


if __name__ == '__main__':
    unittest.main()
