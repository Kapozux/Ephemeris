"""证据卡 + 问（2026-10-11）：原话对不上的卡不收；粘贴的长文不做；问答只用给的卡、出处认回真 id、编的删掉。"""
import _env  # noqa: F401
import json
import unittest

import cards
import llm
import qa
import store

DAY = '2026-09-12'


def seed():
    with store.tx() as db:
        for t in ('messages', 'conversations', 'cards', 'card_days', 'qa_chat'):
            db.execute(f'DELETE FROM {t}')
        db.execute("INSERT INTO conversations(id, source, title) VALUES ('c1', 'claude', '申请季')")
        rows = [('m1', 'human', '我决定 ED 交 CMU，UIUC 放 EA。'),
                ('m2', 'assistant', '可以，但建议你先把主文书第二段的论点收窄一点。'),
                ('m3', 'human', '贴一篇文章：' + '很长的文章' * 500)]
        for i, (mid, role, text) in enumerate(rows):
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       (mid, 'c1', 'claude', f'{DAY}T0{i}:00', DAY, f'0{i}:00', role, text, len(text)))


class Cards(unittest.TestCase):
    def setUp(self):
        seed()
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def fake(self, out):
        llm.use(lambda p: (self.prompts.append(p), json.dumps(out, ensure_ascii=False))[1])

    def test_quotes_must_be_verbatim(self):
        self.fake([
            {'m': 1, 'k': '决定', 't': '我决定 ED 交 CMU', 'q': 'ED 交 CMU，UIUC 放 EA'},
            {'m': 2, 'k': '建议', 't': 'AI 建议收窄主文书论点', 'q': '先把主文书第二段的论点收窄'},
            {'m': 1, 'k': '决定', 't': '编的', 'q': '我决定不申请了'},           # 原话里没有 → 丢
            {'m': 9, 'k': '事实', 't': '编号不对', 'q': 'CMU'},                # 没有第 9 条 → 丢
            {'m': 1, 'k': '建议', 't': 'AI 说先改文书', 'q': '主文书第二段'}])      # 编号写错，原话只在第 2 条 → 改指第 2 条
        r = cards.build_day(DAY)
        self.assertEqual((r['n'], r['dropped']), (3, 2))
        rows = store.q('SELECT id, who, kind, msg_id FROM cards ORDER BY id')
        self.assertEqual([(x['id'], x['who'], x['msg_id']) for x in rows],
                         [(f'{DAY}·1', 'me', 'm1'), (f'{DAY}·2', 'ai', 'm2'), (f'{DAY}·3', 'ai', 'm2')])
        self.assertNotIn('很长的文章', self.prompts[0])                      # 粘贴的长文不送
        self.assertEqual(cards.due([DAY]), [])

    def test_new_message_makes_day_due_again(self):
        self.fake([])
        cards.build_day(DAY)
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'm4', 'c1', 'claude', f'{DAY}T09:00', DAY, '09:00', 'human', '又来一句', 4)
        self.assertEqual(cards.due([DAY]), [DAY])

    def test_garbage_items_are_dropped_not_fatal(self):
        self.fake([3, 'x', {'m': 1, 'k': '决定', 't': '我决定 ED 交 CMU', 'q': 'ED 交 CMU'}])
        r = cards.build_day(DAY)
        self.assertEqual((r['n'], r['dropped']), (1, 2))

    def test_failed_block_leaves_day_due(self):
        llm.use(lambda p: 'not json at all')                                 # 两次都解析不出 → 这一块算没拿到
        r = cards.build_day(DAY)
        self.assertEqual(r['failed'], 1)
        self.assertEqual(cards.due([DAY]), [DAY])                            # 下次还会重做

    def test_search_index_follows_cards(self):
        self.fake([{'m': 1, 'k': '决定', 't': '我决定 ED 交 CMU', 'q': 'ED 交 CMU'}])
        cards.build_day(DAY)
        self.assertEqual(store.one("SELECT COUNT(*) n FROM cards_fts WHERE text LIKE '%ED 交%'")['n'], 1)
        cards.build_day(DAY)                                                   # 重做：旧的删掉，索引跟着
        self.assertEqual(store.one("SELECT COUNT(*) n FROM cards_fts WHERE text LIKE '%ED 交%'")['n'], 1)


class Ask(unittest.TestCase):
    def setUp(self):
        seed()
        llm.use(lambda p: json.dumps([{'m': 1, 'k': '决定', 't': '我决定 ED 交 CMU', 'q': 'ED 交 CMU'},
                                      {'m': 2, 'k': '建议', 't': 'AI 建议收窄主文书论点', 'q': '论点收窄'}], ensure_ascii=False))
        cards.build_day(DAY)
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def answer_with(self, text):
        def fake(p):
            self.prompts.append(p)
            if p.startswith('你在帮一个人查'):
                return json.dumps({'q': '我 ED 交哪', 'keywords': ['CMU', 'ED', '文书'], 'since': None, 'until': None})
            return text
        llm.use(fake)

    def test_cites_map_back_and_fakes_are_dropped(self):
        self.answer_with('你决定 ED 交 CMU [c1]，AI 当时建议先改文书 [c2, c9]。')
        r = qa.answer('我早申交的哪？')
        self.assertEqual(r['answer'], f'你决定 ED 交 CMU [[{DAY}·1]]，AI 当时建议先改文书 [[{DAY}·2]]。')
        self.assertEqual([c['id'] for c in r['cites']], [f'{DAY}·1', f'{DAY}·2'])
        self.assertEqual(r['cites'][0]['msg_id'], 'm1')
        self.assertIn('[c1] 2026-09-12 我 决定：我决定 ED 交 CMU「ED 交 CMU」', self.prompts[1])
        h = qa.history()
        self.assertEqual([x['role'] for x in h], ['user', 'assistant'])
        self.assertEqual(h[1]['cites'][0]['quote'], 'ED 交 CMU')                # 存了快照

    def test_nothing_found(self):
        self.answer_with('不会被调用')
        llm.use(lambda p: json.dumps({'q': 'x', 'keywords': ['火星'], 'since': None, 'until': None}))
        r = qa.answer('我去过火星吗')
        self.assertEqual(r['cites'], [])
        self.assertIn('没找到', r['answer'])

    def test_follow_up_carries_previous_cards(self):
        self.answer_with('你决定 ED 交 CMU [c1]。')
        qa.answer('我早申交的哪？')
        self.prompts.clear()
        llm.use(lambda p: (self.prompts.append(p), json.dumps({'q': '后来呢', 'keywords': ['不相干'], 'since': None, 'until': None})
                           if p.startswith('你在帮一个人查') else '还是 CMU [c1]')[1])
        r = qa.answer('那后来呢')
        self.assertEqual([c['id'] for c in r['cites']], [f'{DAY}·1'])                # 关键词没命中，靠上一轮的卡接上


if __name__ == '__main__':
    unittest.main()
