"""回顾面板那段话：每周一写一次，平时加数据、打卡都不重写（以前一变就重写四个范围，浪费钱）。"""
import _env  # noqa: F401
import json
import time
import unittest
from datetime import date, timedelta

import jobs
import llm
import reflect_chat
import store

DAY = '2026-03-03'
GOOD = json.dumps({'headline': '这段时间在改稿', 'narrative': '这段时间几乎都在改经济 IA。', 'groups': []}, ensure_ascii=False)


class Weekly(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'diaries', 'conversations', 'reflect', 'checkins'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title, first_ts, last_ts) VALUES ('c', 'claude', 't', ?, ?)", (DAY, DAY))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       ('m1', 'c', 'claude', DAY + 'T10:00', DAY, '10:00', 'human', '改稿' * 300, 600))
            db.execute("INSERT INTO diaries(date, headline, narrative, topics, artifacts, open_loops, mood, generated_at) "
                       "VALUES (?, '改经济 IA', '一整天在改稿。', '[\"经济 IA\"]', '[]', '[]', '平稳', ?)", (DAY, DAY + 'T23:00:00'))
        reflect_chat._auto_tried.clear()
        self.calls = []
        self.answer = GOOD
        llm.use(lambda prompt: (self.calls.append(1), self.answer)[1])
        self.monday = reflect_chat._week_start()
        self.last_week = (date.fromisoformat(self.monday) - timedelta(days=3)).isoformat()

    def tearDown(self):
        llm.use(None)

    def put(self, rk, generated_on, version=None, headline='旧的那版'):
        store.run('INSERT OR REPLACE INTO reflect VALUES (?,?,?,?,?,?)', rk,
                  f'{version or reflect_chat.PROMPT_VERSION}|x', headline, '旧的叙事。', '[]', generated_on + 'T09:00:00')

    def build(self, rk, **kw):
        out = reflect_chat.build(rk, **kw)
        for _ in range(200):                                  # 后台那一下写完
            if not jobs.running(f'reflect:{rk}'):
                break
            time.sleep(0.01)
        return out

    def headline(self, rk):
        return store.one('SELECT headline FROM reflect WHERE range_key=?', rk)['headline']

    def test_first_time_writes(self):
        self.build('1w')
        self.assertEqual((len(self.calls), self.headline('1w')), (1, '这段时间在改稿'))

    def test_new_data_this_week_does_not_rewrite(self):
        self.put('1w', self.monday)
        # 又聊了几句、打了一条卡：以前这就会把四个范围全部重写
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'm2', 'c', 'claude', DAY + 'T11:00', DAY, '11:00', 'human', '再改', 2)
        store.run("INSERT INTO checkins(date, word, valence, note) VALUES (?, '开心', 1.5, '')", DAY)
        out = self.build('1w')
        self.assertEqual((len(self.calls), self.headline('1w')), (0, '旧的那版'))
        self.assertEqual(out['headline'], '旧的那版')
        self.assertEqual(out['totals']['msgs'], 2)            # 数字是实时的

    def test_last_week_rewrites_once_even_if_it_fails(self):
        self.put('1w', self.last_week)
        self.answer = ''                                       # 模型这次写不出来
        self.build('1w')
        tries = len(self.calls)
        self.assertGreater(tries, 0)
        self.build('1w')
        self.build('1w')
        self.assertEqual(len(self.calls), tries)              # 这周不再自动试
        self.assertEqual(self.headline('1w'), '旧的那版')

    def test_refresh_button_always_rewrites(self):
        self.put('1w', self.monday)
        self.build('1w', refresh=True)
        self.assertEqual((len(self.calls), self.headline('1w')), (1, '这段时间在改稿'))

    def test_prompt_change_rewrites(self):
        self.put('1w', self.monday, version='v0-old')
        self.build('1w')
        self.assertEqual(self.headline('1w'), '这段时间在改稿')

    def test_weekly_writes_only_ranges_not_done_this_week(self):
        self.put('1w', self.monday)
        self.put('1m', self.last_week)
        self.put('all', self.monday)                           # 3m 还没有
        self.assertEqual(reflect_chat.weekly(), ['1m', '3m'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(reflect_chat.weekly(), [])            # 同一周再跑：什么都不做

    def test_panel_says_when_it_was_written(self):
        self.put('1m', self.monday)
        out = self.build('1m')
        self.assertTrue(out['generated'])
        self.assertTrue(out['generated_at'].startswith(self.monday))


if __name__ == '__main__':
    unittest.main()
