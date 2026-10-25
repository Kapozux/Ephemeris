"""记忆（10-25）：从证据卡整理档案，每条带出处；换下来的不删；他写的留着；关掉的「问」看不到。"""
import _env  # noqa: F401
import json
import unittest
from datetime import date, timedelta

import days
import llm
import memory
import qa
import store
from test_look_sql import run


def ago(n):
    return (date.fromisoformat(days.today()) - timedelta(days=n)).isoformat()


def seed():
    with store.tx() as db:
        for t in ('messages', 'conversations', 'cards', 'card_vecs', 'memory_topics', 'memory_facts', 'themes', 'theme_days'):
            db.execute(f'DELETE FROM {t}')
        db.execute("INSERT INTO conversations(id, source, title) VALUES ('v1', 'claude', '申请季')")
        for cid, d, who, text in (('k1', ago(20), 'me', '我决定 ED 交 MIT'), ('k2', ago(3), 'me', 'ED 改成交 Purdue 了'),
                                  ('k3', ago(2), 'me', '我希望你回答别列流水账')):
            db.execute("INSERT INTO cards(id, date, conv_id, msg_id, who, kind, text, quote, topic) VALUES (?, ?, 'v1', ?, ?, '决定', ?, ?, '大学申请')",
                       (cid, d, 'm-' + cid, who, text, text))


class Memory(unittest.TestCase):
    def setUp(self):
        seed()
        self.prompts = []

    def tearDown(self):
        llm.use(None)

    def fake(self, topic_out):
        def fn(p):
            self.prompts.append(p)
            if '定出他现在的「领域」' in p:
                return json.dumps({'areas': [{'name': '大学申请', 'summary': '早申和文书', 'keywords': ['ED', 'MIT', 'Purdue']}]})
            return json.dumps(topic_out(p), ensure_ascii=False)
        llm.use(fn)

    def test_facts_need_real_cites_and_replace_keeps_old(self):
        self.fake(lambda p: {'summary': '在定 ED', 'facts': [{'text': 'ED 交 MIT', 'as_of': '2099-01-01', 'cites': ['c1']},
                                                              {'text': '编的', 'cites': ['c99']}, {'text': '没出处'}]})
        memory.refresh(force=True)
        t = next(t for t in memory.topics() if t['name'] == '大学申请')
        self.assertEqual([f['text'] for f in t['facts']], ['ED 交 MIT'])
        self.assertEqual(t['facts'][0]['cites'], ['k1'])
        self.assertEqual(t['facts'][0]['as_of'], ago(20))                     # 截至哪天只认出处卡的日期
        first = t['facts'][0]['id']
        memory.add('大学申请', '我自己补的')
        self.fake(lambda p: {'summary': '改了 ED', 'facts': [{'text': 'ED 改成 Purdue', 'cites': ['c2'], 'replaces': ['f1']}]})
        memory.refresh(force=True, name='大学申请')
        self.assertIn('[f1] ED 交 MIT', self.prompts[-1])
        self.assertIn(' hand', self.prompts[-1])
        t = next(t for t in memory.topics(all=True) if t['name'] == '大学申请')
        self.assertEqual([f['text'] for f in t['facts']], ['ED 改成 Purdue', '我自己补的'])   # 他写的没被模型写进去也留着
        old = t['old'][0]
        self.assertEqual((old['id'], old['old_by']), (first, t['facts'][0]['id']))
        self.assertEqual(t['facts'][0]['src'][0]['msg_id'], 'm-k2')

    def test_prompt_directory_page_and_off(self):
        self.fake(lambda p: {'summary': '在定 ED', 'facts': [{'text': 'ED 交 MIT', 'cites': ['c1']}]})
        memory.refresh(force=True)
        self.assertIn('· 大学申请（截至', memory.directory())
        self.assertIn('ED 交 MIT（截至', run('memory', '大学申请'))
        self.assertIn('[[c:k1]]', memory.page('大学申请'))
        p = qa.DEEP_PROMPT.format(name='他', today='2026-10-25', roots='~', skills='', memory=memory.directory(), look='look.py',
                                  history='', question='我 ED 交哪')
        self.assertIn('look.py memory <名字>', p)
        self.assertIn('· 大学申请', p)
        memory.switch('大学申请', False)
        self.assertNotIn('大学申请', memory.directory())
        self.assertIsNone(memory.page('大学申请'))
        self.assertIn('没有「大学申请」', run('memory', '大学申请'))

    def test_weekly_only_and_strike(self):
        self.fake(lambda p: {'summary': 's', 'facts': [{'text': 'ED 交 MIT', 'cites': ['c1']}]})
        memory.refresh()
        n = len(self.prompts)
        self.assertFalse(memory.due())
        memory.refresh()                                                     # 这周写过：一次都不调
        self.assertEqual(len(self.prompts), n)
        fid = memory.topics()[-1]['facts'][0]['id']
        memory.strike(fid)
        self.assertEqual(store.one('SELECT old_by FROM memory_facts WHERE id=?', fid)['old_by'], -1)


if __name__ == '__main__':
    unittest.main()
