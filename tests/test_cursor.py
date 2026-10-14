"""Cursor 的对话也进 Ephemeris（2026-10-14 用户：「Cursor 的你能看到吗」）：来源 cursor，一段对话一个窗口；
他说的和 AI 回的文字进来（没有文字的工具调用不进）；写代码的记录（Claude Code + Cursor）加起来只占日记材料的 10%
（用户：「10% 权重是所有 Coding 加起来」）；做卡只看他说的。"""
import _env  # noqa: F401
import json
import os
import sqlite3
import unittest

import cards
import cursor
import slice as sl
import store

DAY = '2026-04-21'


def make_db(path, convs):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)')
    for cid, name, bubbles in convs:
        heads = [{'bubbleId': b, 'type': t} for b, t, _, _ in bubbles]
        db.execute('INSERT INTO cursorDiskKV VALUES (?,?)', (f'composerData:{cid}', json.dumps(
            {'composerId': cid, 'name': name, 'createdAt': 1776752880000, 'fullConversationHeadersOnly': heads})))
        for b, t, text, ts in bubbles:
            db.execute('INSERT INTO cursorDiskKV VALUES (?,?)', (f'bubbleId:{cid}:{b}', json.dumps(
                {'bubbleId': b, 'type': t, 'text': text, **({'createdAt': ts} if ts else {})})))
    db.execute("INSERT INTO cursorDiskKV VALUES ('composerData:empty', ?)",
               (json.dumps({'composerId': 'empty', 'createdAt': 1, 'fullConversationHeadersOnly': []}),))
    db.commit()
    db.close()


class Sync(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'cc_files', 'cards', 'card_convs'):
                db.execute(f'DELETE FROM {t}')
        make_db(cursor.DB, [('c1', 'get_Audio转文本功能检查', [
            ('u1', 1, '目前的程序只能支持前半个小时，你有什么解决思路吗', f'{DAY}T06:28:00.000Z'),
            ('a1', 2, '', f'{DAY}T06:28:05.000Z'),                                  # 只有工具调用
            ('a2', 2, '可以先切片再合并。', f'{DAY}T06:28:30.000Z'),
            ('u2', 1, '先做切片最后合并吧', None),                                   # 没时间：用对话的开始时间
        ])])

    def test_words_come_in_as_one_window(self):
        r = cursor.sync(say=None)
        rows = store.q('SELECT role, text, source, conv_id, date FROM messages ORDER BY ts, id')
        self.assertEqual(sorted((x['role'], x['text']) for x in rows), sorted([
            ('human', '目前的程序只能支持前半个小时，你有什么解决思路吗'), ('assistant', '可以先切片再合并。'), ('human', '先做切片最后合并吧')]))
        self.assertEqual({x['source'] for x in rows}, {'cursor'})
        self.assertEqual({x['conv_id'] for x in rows}, {'cursor-c1'})
        self.assertEqual(store.one("SELECT title FROM conversations WHERE id='cursor-c1'")['title'], 'Cursor·get_Audio转文本功能检查')
        self.assertEqual(r['new'], 3)

    def test_unchanged_db_is_not_reread_and_new_messages_append(self):
        cursor.sync(say=None)
        self.assertEqual(cursor.sync(say=None)['new'], 0)
        make_db(cursor.DB, [('c1', 'x', [('u1', 1, '目前的程序只能支持前半个小时，你有什么解决思路吗', f'{DAY}T06:28:00.000Z'),
                                          ('u3', 1, '再加一个视频转文本', f'{DAY}T07:00:00.000Z')])])
        os.utime(cursor.DB, (1, 2))
        self.assertEqual(cursor.sync(say=None)['new'], 1)
        self.assertEqual(store.one("SELECT COUNT(*) n FROM messages WHERE source='cursor'")['n'], 4)    # 只加不删

    def test_no_cursor_installed(self):
        os.remove(cursor.DB)
        self.assertEqual(cursor.sync(say=None)['new'], 0)

    def test_cards_skip_ai_replies(self):
        cursor.sync(say=None)
        _, segs = cards.segments('cursor-c1')
        self.assertEqual({r['role'] for s in segs for r in s}, {'human'})


class Weights(unittest.TestCase):
    def test_all_coding_shares_one_tenth(self):
        th = lambda src, n, title: {'conv_id': title, 'source': src, 'conversation': title, 'first': '10:00', 'last': '11:00',
                                     'n_msgs': 50, 'prior': None,
                                     'messages': [{'time': '10:00', 'text': f'{title}的话。' * (n // 50 // 5)} for _ in range(50)]}
        threads = [th('claude', 200000, '聊天'), th('cc', 100000, 'CC写代码'), th('cursor', 100000, 'Cursor写代码')]
        day = {'date': DAY, 'n_threads': 3, 'n_user_msgs': 150, 'user_chars': 400000, 'threads': threads}
        text = sl.render_day(day, char_budget=60000)
        coding = text.split(sl.SOURCE_HEAD['cc'])[1]
        self.assertIn('CC写代码', coding)
        self.assertIn('Cursor写代码', coding)
        self.assertLess(len(coding), 60000 * 0.1 + 4000)                   # 两家加起来一份 10%
        self.assertEqual(sl._kind('cursor'), 'cc')


if __name__ == '__main__':
    unittest.main()
