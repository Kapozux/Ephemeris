"""每个读页面的接口在一个小临时库上都能返回 200 + JSON。

踩过（2026-10-02 重构第 2 步）：删了 checkin.today()，progress() 里还有一处裸调用，
打卡页的进度条 500 —— 新旧接口对比也没发现，因为两边都是同一个错误页。这个测试专门兜这种。
不测会出网的（Notion 状态会去扫远端）。
"""
import _env  # noqa: F401
import unittest

import app as ephemeris
import checkin
import llm
import store

DAY = '2026-03-03'
CONV = 'conv-1'


def seed():
    with store.tx() as db:
        for t in ('conversations', 'messages', 'msg_fts', 'diaries', 'checkins', 'journal', 'mood_scores',
                  'conv_notes', 'themes', 'theme_days'):
            db.execute(f'DELETE FROM {t}')
        db.execute("INSERT INTO conversations(id, source, title, first_ts, last_ts, n_msgs, chars, n_days) "
                   "VALUES (?, 'claude', '经济 IA', '2026-03-02T10:00', '2026-03-03T11:00', 2, 900, 2)", (CONV,))
        for i, (d, role, text) in enumerate([('2026-03-02', 'human', '开题怎么写' * 100),
                                              (DAY, 'human', '改稿' * 400), (DAY, 'assistant', '好的')]):
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       (f'm{i}', CONV, 'claude', f'{d}T10:0{i}', d, f'10:0{i}', role, text, len(text)))
        db.execute("INSERT INTO msg_fts VALUES ('改稿改稿', '经济 IA', ?, 'm1')", (DAY,))
        db.execute("INSERT INTO diaries(date, headline, narrative, topics, artifacts, open_loops, mood) "
                   "VALUES (?, '改经济 IA', '一整天在改稿。', '[\"经济 IA\"]', '[]', '[]', '平稳')", (DAY,))
        db.execute("INSERT INTO journal(page_id, date, title, text, chars) VALUES ('p1', ?, '日记', '今天还行', 4)", (DAY,))
        db.execute("INSERT INTO mood_scores(date, src, valence, label) VALUES (?, 'hand', 1, '满足')", ('2026-03-02',))
        db.execute("INSERT INTO themes(id, name, created_on) VALUES (1, '经济 IA', '2026-03-02')")
        db.execute("INSERT INTO theme_days VALUES (1, '2026-03-02', '开题', 'ai'), (1, ?, '改稿', 'ai')", (DAY,))
    checkin.add(DAY, '开心', '交了')


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        llm.use(lambda prompt: '')          # 谁也别真去调模型
        seed()
        cls.c = ephemeris.app.test_client()

    @classmethod
    def tearDownClass(cls):
        llm.use(None)

    def test_pages(self):
        for url in ['/', '/checkin']:
            with self.subTest(url=url):
                self.assertEqual(self.c.get(url).status_code, 200)

    def test_json_endpoints(self):
        for url in ['/api/days', f'/api/day/{DAY}', '/api/day/2026-01-01', f'/api/conversation/{CONV}',
                    '/api/search?q=改稿', '/api/diary/batch', f'/api/checkin?date={DAY}', '/api/checkin/home',
                    '/api/checkin/month?month=2026-03', '/api/stars?month=2026-03', '/api/backup',
                    '/api/flashback', '/api/river', '/api/themes', '/api/conversations', '/api/imports',
                    '/api/windows', '/api/windows?all=1', f'/api/window/{CONV}', f'/api/window/{CONV}/day/{DAY}',
                    '/api/reflect?range=1m', '/api/claude/sync']:
            with self.subTest(url=url):
                r = self.c.get(url)
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[-300:])
                self.assertIsNotNone(r.get_json())

    def test_day_shows_checkin_feel(self):
        sig = self.c.get(f'/api/day/{DAY}').get_json()['signals']
        self.assertEqual((sig['mood']['src'], sig['mood']['word']), ('checkin', '开心'))


if __name__ == '__main__':
    unittest.main()
