"""同一个窗口存了两份（2026-10-13）：手动导出（没有编号，指纹 id）+ 自动同步（uuid）。
多出来的那份挪去存档（不丢）；再导一次不会回来；两份都是同步来的不动；能挪回来。"""
import _env  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import dedup
import index as idx
import store

UUID = '6aa081f6-8b3b-484e-92f0-46781e138915'
UUID2 = '7bb081f6-8b3b-484e-92f0-46781e138915'
MSGS = [('2026-07-07', '他刚入职海底捞就准备摆烂'), ('2026-07-07', '冯家科为了奔现跑去海底捞'), ('2026-07-07', '后来呢')]


def export(path, msgs, uuid=None, name='为了网恋女友入职海底捞'):
    c = {'name': name, 'messages': [{'created_at': f'{d}T04:0{i}:00Z', 'role': 'human', 'text': t}
                                    for i, (d, t) in enumerate(msgs)]}
    if uuid:
        c['uuid'] = uuid
    with open(path, 'w') as f:
        json.dump({'conversations': [c]}, f, ensure_ascii=False)


class Dedup(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'conversations_dup', 'messages_dup', 'cards', 'card_convs', 'diaries'):
                db.execute(f'DELETE FROM {t}')
        self.tmp = tempfile.mkdtemp(prefix='ephemeris-dup-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, name, msgs, uuid=None, title='为了网恋女友入职海底捞'):
        p = os.path.join(self.tmp, name)
        export(p, msgs, uuid, title)
        return idx.ingest(store.connect(), p)

    def convs(self):
        return sorted(r['id'] for r in store.q('SELECT id FROM conversations'))

    def test_sync_copy_replaces_the_exported_copy(self):
        self.ingest('export.json', MSGS)                                   # 手动导出：指纹 id
        fp = self.convs()[0]
        store.run("INSERT INTO cards (id, date, conv_id, msg_id, who, kind, text, quote) VALUES ('x·1','2026-07-07',?,'m','me','事实','t','q')", fp)
        self.ingest('sync.json', MSGS + [('2026-07-08', '第二天接着聊')], UUID)   # 同步来的：uuid，还多了一条
        self.assertEqual(self.convs(), [UUID])                              # 窗口页只剩一个
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages')['n'], 4)
        self.assertEqual(store.one('SELECT dup_of FROM conversations_dup WHERE id=?', fp)['dup_of'], UUID)
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages_dup')['n'], 3)     # 挪走的消息还在库里
        self.assertEqual(store.one('SELECT COUNT(*) n FROM cards')['n'], 0)           # 派生的卡删了
        self.assertEqual(store.one("SELECT COUNT(*) n FROM msg_fts WHERE text LIKE '%海底捞%'")['n'], 2)   # 搜索不成对

    def test_reimporting_the_export_does_not_bring_it_back(self):
        self.ingest('export.json', MSGS)
        self.ingest('sync.json', MSGS, UUID)
        self.ingest('export.json', MSGS)
        self.assertEqual(self.convs(), [UUID])

    def test_export_after_sync_is_dropped_right_away(self):
        self.ingest('sync.json', MSGS, UUID)
        self.ingest('export.json', MSGS)
        self.assertEqual(self.convs(), [UUID])

    def test_two_synced_windows_are_left_alone(self):
        self.ingest('a.json', MSGS, UUID)
        self.ingest('b.json', MSGS, UUID2)
        self.assertEqual(self.convs(), sorted([UUID, UUID2]))
        sure, unsure = dedup.find(store.connect())
        self.assertEqual((sure, len(unsure)), ([], 1))                       # 列出来给人看，不动

    def test_same_opening_different_conversation_is_not_a_duplicate(self):
        self.ingest('export.json', [MSGS[0], ('2026-07-07', '完全不同的后续'), ('2026-07-07', '再聊别的')])
        self.ingest('sync.json', MSGS, UUID)
        self.assertEqual(len(self.convs()), 2)

    def test_restore_puts_it_back(self):
        self.ingest('export.json', MSGS)
        fp = self.convs()[0]
        self.ingest('sync.json', MSGS, UUID)
        with store.tx() as db:
            self.assertEqual(dedup.restore(db, fp), 3)
        self.assertEqual(self.convs(), sorted([fp, UUID]))
        self.assertEqual(store.one('SELECT COUNT(*) n FROM messages_dup')['n'], 0)


if __name__ == '__main__':
    unittest.main()
