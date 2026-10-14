"""Claude Code 的对话进 Ephemeris（2026-10-13）：他说的、Claude 回的文字进来，系统塞的 / 子代理 / 自己调 claude -p 的不进；
日记材料按来源分预算（AI 聊天 80、Claude Code 10、问 10）；做卡只看他在 Claude Code 里说的。"""
import _env  # noqa: F401
import json
import os
import shutil
import unittest

import bundle
import cards
import llm
import claude_code
import slice as sl
import store

DAY = '2026-09-20'


def line(**kw):
    base = {'timestamp': f'{DAY}T02:00:00.000Z', 'cwd': '/Users/me/Documents/CODEelse/ephemeris'}
    return json.dumps({**base, **kw}, ensure_ascii=False)


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


class Sync(unittest.TestCase):
    def setUp(self):
        self.root = claude_code.ROOT
        shutil.rmtree(self.root, ignore_errors=True)
        with store.tx() as db:
            for t in ('messages', 'conversations', 'cc_files', 'cards', 'card_convs'):
                db.execute(f'DELETE FROM {t}')
        proj = os.path.join(self.root, '-Users-me-Documents-CODEelse-ephemeris')
        write(os.path.join(proj, 's1.jsonl'), [
            line(type='user', uuid='u1', message={'role': 'user', 'content': '证据卡抽卡用 Claude，Verbatim 才用 DeepSeek'}),
            line(type='assistant', uuid='a1', timestamp=f'{DAY}T02:00:05.000Z', message={'role': 'assistant', 'content': [
                {'type': 'text', 'text': '明白，我把 Ephemeris 改回 Claude。'}, {'type': 'tool_use', 'name': 'Bash', 'input': {}}]}),
            line(type='user', uuid='u2', message={'role': 'user', 'content': [{'type': 'tool_result', 'content': 'ok'}]}),
            line(type='user', uuid='u3', message={'role': 'user', 'content': '<task-notification><task-id>x</task-id></task-notification>'}),
            line(type='user', uuid='u4', isMeta=True, message={'role': 'user', 'content': '系统的'}),
            line(type='user', uuid='u5', message={'role': 'user', 'content': '[Request interrupted by user]'}),
            line(type='user', uuid='u6', isSidechain=True, message={'role': 'user', 'content': '子代理的'}),
        ])
        write(os.path.join(proj, 's1', 'subagents', 'agent-1.jsonl'),
              [line(type='user', uuid='x1', message={'role': 'user', 'content': '子代理的文件'})])
        write(os.path.join(self.root, '-Users-me--ephemeris-claude', 's9.jsonl'),
              [line(type='user', uuid='h1', message={'role': 'user', 'content': '日记的提示词（Ephemeris 自己调的）'})])

    def test_only_real_words_come_in(self):
        r = claude_code.sync(say=None)
        rows = store.q('SELECT id, role, text, source, date FROM messages ORDER BY ts, id')
        self.assertEqual([(x['role'], x['text']) for x in rows],
                         [('human', '证据卡抽卡用 Claude，Verbatim 才用 DeepSeek'), ('assistant', '明白，我把 Ephemeris 改回 Claude。')])
        self.assertEqual({x['source'] for x in rows}, {'cc'})
        self.assertEqual(store.one('SELECT title FROM conversations')['title'], 'CC·ephemeris：证据卡抽卡用 Claude，Verbatim 才用 DeepSeek')
        self.assertEqual((r['files'], r['new']), (1, 2))

    def test_unchanged_files_are_not_reread(self):
        claude_code.sync(say=None)
        self.assertEqual(claude_code.sync(say=None), {'files': 0, 'new': 0, 'days': []})

    def test_window_ending_on_claudes_reply_is_not_redone(self):
        # 10-14：存进度时的指纹算上了 Claude 的回复、判断时又不算，两边永远对不上，每次做卡都整窗重做一遍
        claude_code.sync(say=None)
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'cc-u9', 'cc-s1', 'cc', f'{DAY}T10:10:00+00:00', DAY, '18:10',
                  'human', '那就这么改', 5)                                         # Claude 的回复夹在他两句话中间
        llm.use(lambda p: json.dumps({'summary': '在改做卡', 'cards': []}, ensure_ascii=False))
        try:
            cards.build_conv('cc-s1')
        finally:
            llm.use(None)
        self.assertEqual(cards.due(['cc-s1']), [])

    def test_cards_skip_claudes_replies_in_claude_code(self):
        claude_code.sync(say=None)
        mode, segs = cards.segments('cc-s1')
        self.assertEqual([r['role'] for s in segs for r in s], ['human'])


class Weights(unittest.TestCase):
    def day(self, cc_chars, chat_chars=200000):
        th = lambda src, n, title: {'conv_id': title, 'source': src, 'conversation': title, 'first': '10:00', 'last': '11:00',
                                     'n_msgs': 50, 'prior': None,
                                     'messages': [{'time': '10:00', 'text': f'{title}的话。' * (n // 50 // 5)} for _ in range(50)]}
        threads = [th('claude', chat_chars, '聊天')] + ([th('cc', cc_chars, '写代码')] if cc_chars else [])
        return {'date': DAY, 'n_threads': len(threads), 'n_user_msgs': 100, 'user_chars': chat_chars + cc_chars, 'threads': threads}

    def test_claude_code_gets_at_most_a_tenth(self):
        text = sl.render_day(self.day(200000), char_budget=60000)
        cc = text.split(sl.SOURCE_HEAD['cc'])[1]
        self.assertLess(len(cc), 60000 * 0.1 + 3000)                       # 份额 + 题头和窗口行
        self.assertIn('【AI 聊天】', text)
        self.assertGreater(len(text.split(sl.SOURCE_HEAD['cc'])[0]), 40000)

    def test_without_claude_code_chat_gets_it_all_and_no_headers(self):
        text = sl.render_day(self.day(0), char_budget=60000)
        self.assertNotIn('【AI 聊天】', text)
        self.assertGreater(len(text), 50000)

    def test_bundle_knows_the_source(self):
        store.run('DELETE FROM messages')
        store.run("INSERT OR IGNORE INTO conversations(id, source, title) VALUES ('cc-z', 'cc', 'CC·x：y')")
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'cc-m', 'cc-z', 'cc', f'{DAY}T10:00', DAY, '10:00', 'human', '做项目', 3)
        self.assertEqual(bundle.day_bundle(DAY)['threads'][0]['source'], 'cc')


if __name__ == '__main__':
    unittest.main()
