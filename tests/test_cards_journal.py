"""Notion 手写日记也做证据卡（2026-10-14 用户：「那肯定要啊」）：一页一个窗口、长页按段切；原话照样逐字核对；
改了那页整页重做；「问」里卡的出处叫「手写日记」；早上自动做卡也挑最近写 / 改的页。"""
import _env  # noqa: F401
import unittest

import cards
import llm
import qa
import store
from test_cards_qa import out, seed

PAGE = 'p1'
CID = cards.JOURNAL + PAGE


def write(text, date='2026-09-20', fetched='2026-09-21T08:00:00'):
    store.run('INSERT OR REPLACE INTO journal (page_id, date, title, text, chars, fetched_at) VALUES (?,?,?,?,?,?)',
              PAGE, date, date, text, len(text), fetched)


class Journal(unittest.TestCase):
    def setUp(self):
        seed()
        store.run('DELETE FROM journal')
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def fake(self, o):
        llm.use(lambda p: (self.prompts.append(p), out(o))[1])

    def test_page_becomes_cards(self):
        write('今天**终于**把 CMU 的文书写完了，心里松了一口气。\n\n晚上和妈吵了一架，因为她又问我分数。')
        self.assertIn((CID, 'full'), cards.due())
        self.fake([{'m': 1, 'k': '经历', 'p': '美本申请', 't': '我写完了 CMU 文书', 'q': '终于把 CMU 的文书写完了'},
                   {'m': 1, 'k': '经历', 'p': '家里', 't': '我和妈吵架', 'q': '晚上和妈吵了一架'},
                   {'m': 1, 'k': '感受', 't': '编的', 'q': '我其实很开心呢'}])
        r = cards.build_conv(CID)
        self.assertEqual((r['n'], r['dropped']), (2, 1))
        self.assertIn('自己在 Notion 里写的一页日记', self.prompts[0])
        self.assertIn('[1] 今天终于把 CMU', self.prompts[0])          # 喂的是纯文字（粗体记号去掉了）
        rows = store.q('SELECT id, who, msg_id, date FROM cards WHERE conv_id=? ORDER BY id', CID)
        self.assertEqual([x['id'] for x in rows], [f'{CID}/1·1', f'{CID}/1·2'])
        self.assertEqual({(x['who'], x['date']) for x in rows}, {('me', '2026-09-20')})
        self.assertNotIn(CID, [c for c, _ in cards.due()])          # 做过了

        c = qa.card(rows[0]['id'])
        self.assertEqual(c['title'], '手写日记')
        self.assertIn('《手写日记》', qa._card_line(1, c))

        write('今天终于把 CMU 的文书写完了。后来又改了一句。')       # 改了：整页重做
        self.assertIn((CID, 'full'), cards.due())

    def test_long_page_split(self):
        para = '我' + '写' * 3000
        text = '\n'.join([para] * 6)
        write(text)
        rows = cards._journal_rows()[CID]
        self.assertGreater(len(rows), 2)
        self.assertTrue(all(r['chars'] <= cards.PIECE_CHARS for r in rows))
        self.assertEqual('\n'.join(r['text'] for r in rows), text)      # 一个字没丢

    def test_morning_picks_recent_pages(self):
        write('昨天的日记', date='2026-09-20', fetched='2026-09-21T08:00:00')
        self.assertIn(CID, [c for c, _ in cards.due_since('2026-09-21')])     # 刚拉下来（改过）的也算
        self.assertNotIn(CID, [c for c, _ in cards.due_since('2026-09-25')])


if __name__ == '__main__':
    unittest.main()
