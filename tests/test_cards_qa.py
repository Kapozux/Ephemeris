"""证据卡 + 问（2026-10-11；10-13 改成按窗口做）：原话对不上的卡不收；我发的长文标出来让模型分；
窗口后来又聊了只做新的、带前文；做过的那段变了整窗重做；问答两路找、出处认回真 id、编的删掉。"""
import _env  # noqa: F401
import json
import unittest

import cards
import llm
import qa
import store

DAY, DAY2 = '2026-09-12', '2026-09-13'


def add(mid, conv, day, hh, role, text):
    store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
              mid, conv, 'claude', f'{day}T{hh}:00', day, f'{hh}', role, text, len(text))


def seed():
    with store.tx() as db:
        for t in ('messages', 'conversations', 'cards', 'card_convs', 'card_vecs', 'qa_chat'):
            db.execute(f'DELETE FROM {t}')
        db.execute("INSERT INTO conversations(id, source, title) VALUES ('c1', 'claude', '申请季')")
    add('m1', 'c1', DAY, '01:00', 'human', '我决定 ED 交 MIT，Purdue 放 EA。')
    add('m2', 'c1', DAY, '02:00', 'assistant', '可以，但建议你先把主文书第二段的论点收窄一点。')
    add('m3', 'c1', DAY, '03:00', 'human', '贴一篇文章：' + '很长的文章' * 500)


def out(cards_, summary='在定早申学校'):
    return json.dumps({'summary': summary, 'cards': cards_}, ensure_ascii=False)


class Cards(unittest.TestCase):
    def setUp(self):
        seed()
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def fake(self, o):
        llm.use(lambda p: (self.prompts.append(p), out(o) if isinstance(o, list) else o)[1])

    def test_quotes_must_be_verbatim(self):
        self.fake([
            {'m': 1, 'k': '决定', 'p': '大学申请', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT，Purdue 放 EA'},
            {'m': 2, 'k': '建议', 'p': '大学申请', 't': 'AI 建议收窄主文书论点', 'q': '先把主文书第二段的论点收窄'},
            {'m': 1, 'k': '决定', 't': '编的', 'q': '我决定不申请了'},           # 原话里没有 → 丢
            {'m': 9, 'k': '事实', 't': '编号不对', 'q': 'MIT'},                # 没有第 9 条、原话也太短 → 丢
            {'m': 1, 'k': '建议', 't': 'AI 说先改文书', 'q': '主文书第二段'}])      # 编号写错，原话只在第 2 条 → 改指第 2 条
        r = cards.build_conv('c1')
        self.assertEqual((r['n'], r['dropped']), (3, 2))
        rows = store.q('SELECT id, who, msg_id, topic, date FROM cards ORDER BY id')
        self.assertEqual([(x['id'], x['who'], x['msg_id']) for x in rows],
                         [('m1·1', 'me', 'm1'), ('m2·1', 'ai', 'm2'), ('m2·2', 'ai', 'm2')])
        self.assertEqual((rows[0]['topic'], rows[0]['date']), ('大学申请', DAY))
        self.assertIn('「申请季」', self.prompts[0])
        self.assertEqual(cards.due(['c1']), [])
        self.assertIn('在定早申学校', store.one('SELECT summary FROM card_convs')['summary'])

    def test_long_messages_are_marked_not_skipped(self):
        """我发的长文也编号（可能是我自己写的文书）；标成「长文」让模型分。"""
        self.fake([{'m': 3, 'k': '事实', 't': '我贴了一篇文章', 'q': '贴一篇文章：很长的文章'}])
        self.assertEqual(cards.build_conv('c1')['n'], 1)
        self.assertIn('[3] 我 2026-09-12 03:00（长文 2506 字）', self.prompts[0])

    def test_later_messages_only_do_the_new_ones_with_context(self):
        self.fake([{'m': 1, 'k': '决定', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT，Purdue'}])
        cards.build_conv('c1')
        add('m4', 'c1', DAY2, '09:00', 'human', '就按刚才那个方案，MIT 文书今天开写。')
        self.assertEqual(cards.due(['c1']), [('c1', 'more')])
        self.prompts.clear()
        self.fake([{'m': 1, 'k': '计划', 't': '我今天开写 MIT 文书', 'q': 'MIT 文书今天开写'}])
        cards.build_conv('c1')
        p = self.prompts[0]
        self.assertIn('[前文] 我', p)                                               # 上一天的话带着当前文
        self.assertIn('[1] 我 2026-09-13', p)                                      # 只编号新来的
        self.assertIn('这个窗口前面聊过的', p)
        self.assertEqual(sorted(r['id'] for r in store.q('SELECT id FROM cards')), ['m1·1', 'm4·1'])   # 旧卡留着
        self.assertEqual(store.one("SELECT date FROM cards WHERE id='m4·1'")['date'], DAY2)
        self.assertEqual(cards.due(['c1']), [])

    def test_changed_old_message_redoes_whole_window(self):
        self.fake([{'m': 1, 'k': '决定', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT，Purdue'}])
        cards.build_conv('c1')
        store.run("UPDATE messages SET text='我决定 ED 交 MIT，Purdue 放 EA，再加 UCSD。', chars=30 WHERE id='m1'")
        self.assertEqual(cards.due(['c1']), [('c1', 'full')])

    def test_failed_segment_leaves_window_due(self):
        llm.use(lambda p: 'not json at all')                                 # 两次都解析不出 → 这一段算没拿到
        r = cards.build_conv('c1')
        self.assertEqual(r['failed'], 1)
        self.assertEqual(cards.due(['c1']), [('c1', 'full')])                # 下次还会重做

    def test_only_first_good_segments_advance(self):
        """外面做好的（Claude workflow）也走 apply：第二段没拿到，做到哪只推进到第一段。"""
        mode, segs = cards.segments('c1', size=60)
        self.assertEqual((mode, len(segs)), ('full', 3))       # 每条一段
        r = cards.apply('c1', mode, segs, [{'cards': [{'m': 1, 'k': '决定', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'}]},
                                            None, {'cards': []}], src='claude')
        self.assertEqual((r['n'], r['failed']), (1, 1))
        self.assertEqual(store.one('SELECT upto_id, src FROM card_convs'), {'upto_id': 'm1', 'src': 'claude'})
        self.assertEqual(cards.due(['c1']), [('c1', 'more')])

    def test_garbage_items_are_dropped_not_fatal(self):
        self.fake([3, 'x', {'m': 1, 'k': '决定', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'}])
        r = cards.build_conv('c1')
        self.assertEqual((r['n'], r['dropped']), (1, 2))

    def test_search_index_follows_cards(self):
        self.fake([{'m': 1, 'k': '决定', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'}])
        cards.build_conv('c1')
        self.assertEqual(store.one("SELECT COUNT(*) n FROM cards_fts WHERE text LIKE '%ED 交%'")['n'], 1)
        cards.build_conv('c1', 'full')                                         # 重做：旧的删掉，索引跟着
        self.assertEqual(store.one("SELECT COUNT(*) n FROM cards_fts WHERE text LIKE '%ED 交%'")['n'], 1)


def vec(text):
    """假向量：讲申请的一个方向，别的一个方向。"""
    return [1.0, 0.0] + [0.0] * 766 if any(w in text for w in ('MIT', '文书', '早申')) else [0.0, 1.0] + [0.0] * 766


class Ask(unittest.TestCase):
    def setUp(self):
        seed()
        llm.use(lambda p: out([{'m': 1, 'k': '决定', 'p': '大学申请', 't': '我决定 ED 交 MIT', 'q': 'ED 交 MIT'},
                               {'m': 2, 'k': '建议', 'p': '大学申请', 't': 'AI 建议收窄主文书论点', 'q': '论点收窄'}]))
        cards.build_conv('c1')
        llm.use_embed(vec)
        cards.embed_missing()
        self.prompts = []

    def tearDown(self):
        llm.use(None)
        llm.use_embed(None)

    def answer_with(self, text, keywords=('MIT', 'ED', '文书')):
        def fake(p):
            self.prompts.append(p)
            if p.startswith('你在帮一个人查'):
                return json.dumps({'q': '我 ED 交哪', 'keywords': list(keywords), 'since': None, 'until': None})
            return text
        llm.use(fake)

    def test_cites_map_back_and_fakes_are_dropped(self):
        self.answer_with('你决定 ED 交 MIT [c1]，AI 当时建议先改文书 [c2, c9]。')
        r = qa.answer('我早申交的哪？')
        self.assertEqual(r['answer'], '你决定 ED 交 MIT [[m1·1]]，AI 当时建议先改文书 [[m2·1]]。')
        self.assertEqual([c['id'] for c in r['cites']], ['m1·1', 'm2·1'])
        self.assertEqual(r['cites'][0]['msg_id'], 'm1')
        self.assertIn('[c1] 2026-09-12 01:00 我 决定 · 大学申请 · 《申请季》：我决定 ED 交 MIT「ED 交 MIT」', self.prompts[1])
        self.assertIn('在定早申学校', self.prompts[1])                         # 窗口小结当背景
        h = qa.history()
        self.assertEqual([x['role'] for x in h], ['user', 'assistant'])
        self.assertEqual(h[1]['cites'][0]['quote'], 'ED 交 MIT')                # 存了快照

    def test_meaning_finds_cards_words_miss(self):
        """关键词一个都没命中，按意思也找得到（假向量：问题和卡都讲早申）。"""
        self.answer_with('你决定 ED 交 MIT [c1]。', keywords=['早申的去向'])
        r = qa.answer('我早申最后去哪了')
        self.assertEqual([c['id'] for c in r['cites']], ['m1·1'])

    def test_cite_ranges_and_stray_text(self):
        alias = {i: {'id': f'x{i}'} for i in range(1, 10)}
        self.assertEqual(qa.clean('a [c2-c4] b [c5， 背景] c [c9, c99]', alias),
                         ('a [[x2]][[x3]][[x4]] b [[x5]] c [[x9]]', ['x2', 'x3', 'x4', 'x5', 'x9']))

    def test_nothing_found(self):
        llm.use_embed(None)
        cards._vec_cache.clear()
        store.run('DELETE FROM card_vecs')
        llm.use(lambda p: json.dumps({'q': 'x', 'keywords': ['火星'], 'since': None, 'until': None}))
        r = qa.answer('我去过火星吗')
        self.assertEqual(r['cites'], [])
        self.assertIn('没找到', r['answer'])

    def test_a_day_that_fits_is_given_whole(self):
        llm.use(lambda p: (self.prompts.append(p), json.dumps({'q': '那天聊了啥', 'keywords': [], 'since': DAY, 'until': DAY})
                           if p.startswith('你在帮一个人查') else '[c1][c2]')[1])
        r = qa.answer('9 月 12 号我聊了啥')
        self.assertEqual(r['considered'], 2)

    def test_follow_up_carries_previous_cards(self):
        self.answer_with('你决定 ED 交 MIT [c1]。')
        qa.answer('我早申交的哪？')
        self.prompts.clear()
        llm.use_embed(None)
        store.run('DELETE FROM card_vecs')
        llm.use(lambda p: (self.prompts.append(p), json.dumps({'q': '后来呢', 'keywords': ['不相干'], 'since': None, 'until': None})
                           if p.startswith('你在帮一个人查') else '还是 MIT [c1]')[1])
        r = qa.answer('那后来呢')
        self.assertEqual([c['id'] for c in r['cites']], ['m1·1'])                # 关键词没命中，靠上一轮的卡接上


if __name__ == '__main__':
    unittest.main()
