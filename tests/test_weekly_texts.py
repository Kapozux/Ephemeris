"""打卡页「这个月」那一句、按窗口回顾：数据变了也每周最多重写一次（以前每打一次卡 / 每多聊一天就重写，浪费钱）。"""
import _env  # noqa: F401
import hashlib
import json
import unittest
from datetime import date, timedelta

import checkin
import conv_review
import days
import llm
import store

MONTH = '2026-03'


def last_week():
    return (date.fromisoformat(days.week_start()) - timedelta(days=3)).isoformat() + 'T09:00:00'


class MonthNote(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('checkins', 'mood_scores', 'star_notes'):
                db.execute(f'DELETE FROM {t}')
        checkin._note_tried.clear()
        checkin.add('2026-03-01', '开心')
        checkin.add('2026-03-02', '平稳')
        self.calls = []
        self.answer = '这个月整体还行。'

    def model(self, prompt):
        self.calls.append(1)
        return self.answer

    def fp(self):                                   # 跟 month_note 里一样的算法
        cur = checkin._stats(checkin.month_stars(MONTH))
        prv = checkin._stats(checkin.month_stars(checkin._prev_month(MONTH)))
        return hashlib.sha1(json.dumps([cur, prv], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]

    def put(self, fp, made_at):
        store.run('INSERT OR REPLACE INTO star_notes VALUES (?,?,?,?)', MONTH, fp, '旧的那句。', made_at)

    def test_first_time_writes(self):
        self.assertEqual(checkin.month_note(MONTH, self.model), '这个月整体还行。')
        self.assertEqual(len(self.calls), 1)

    def test_new_checkin_this_week_keeps_the_line(self):
        checkin.month_note(MONTH, self.model)
        checkin.add('2026-03-03', '崩溃')                       # 以前：下次打开月视图就重写
        self.assertEqual(checkin.month_note(MONTH, self.model), '这个月整体还行。')
        self.assertEqual(len(self.calls), 1)

    def test_last_week_and_changed_rewrites_once(self):
        self.put('old-fp', last_week())
        self.assertEqual(checkin.month_note(MONTH, self.model), '这个月整体还行。')
        checkin.add('2026-03-03', '崩溃')
        checkin.month_note(MONTH, self.model)
        self.assertEqual(len(self.calls), 1)

    def test_last_week_unchanged_keeps(self):
        self.put(self.fp(), last_week())
        self.assertEqual(checkin.month_note(MONTH, self.model), '旧的那句。')
        self.assertEqual(len(self.calls), 0)

    def test_failure_is_not_retried_this_week(self):
        self.put('old-fp', last_week())
        self.answer = ''
        checkin.month_note(MONTH, self.model)
        tries = len(self.calls)
        checkin.month_note(MONTH, self.model)
        self.assertEqual(len(self.calls), tries)
        self.assertEqual(checkin.month_note(MONTH), '旧的那句。')

    def test_refresh_forces(self):
        self.put(self.fp(), days.week_start() + 'T09:00:00')
        self.assertEqual(checkin.month_note(MONTH, self.model, refresh=True), '这个月整体还行。')


class WindowReview(unittest.TestCase):
    """一个做过回顾的窗口：上周写的汇总，之后又聊了两天（其中一天是今天）。"""
    CID = 'win-1'

    def setUp(self):
        self.today = days.today()
        self.past = (date.fromisoformat(self.today) - timedelta(days=1)).isoformat()
        with store.tx() as db:
            for t in ('messages', 'conversations', 'conv_notes'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title, first_ts, last_ts) VALUES (?, 'claude', '经济论文', ?, ?)",
                       (self.CID, '2026-01-01', self.today))
            for i, d in enumerate(['2026-01-01', '2026-01-02', self.past, self.today]):
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                           (f'm{i}', self.CID, 'claude', d + 'T10:00', d, '10:00', 'human', '改稿' * 50, 100))
            for d in ('2026-01-01', '2026-01-02'):                  # 这两天早就写过小结了
                db.execute('INSERT INTO conv_notes VALUES (?,?,?,?,?)', (self.CID, d, '旧小结', last_week(), '1:100'))
            db.execute("INSERT INTO conv_notes VALUES (?, '', '旧的汇总', ?, NULL)", (self.CID, last_week()))
        conv_review._auto_tried.clear()
        self.calls = []
        llm.use(lambda prompt: (self.calls.append(1), '新写的一句')[1])

    def tearDown(self):
        llm.use(None)

    def notes(self):
        return conv_review.notes_for(self.CID)

    def test_weekly_update_writes_new_past_days_not_today(self):
        done = conv_review.update_weekly()
        self.assertEqual(done, [{'conv': self.CID[:10], 'days': 1}])
        n = self.notes()
        self.assertIn(self.past, n['days'])
        self.assertNotIn(self.today, n['days'])                     # 今天还没过完
        self.assertEqual(n['arc'], '新写的一句')
        self.assertEqual(len(self.calls), 2)                        # 一天的小结 + 汇总

    def test_already_updated_this_week_waits(self):
        conv_review.update_weekly()
        calls = len(self.calls)
        store.run('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)', 'm9', self.CID, 'claude', self.past + 'T11:00',
                  self.past, '11:00', 'human', '又聊', 2)               # 这周后来又聊了
        self.assertEqual(conv_review.update_weekly(), [])
        self.assertEqual(len(self.calls), calls)

    def test_never_reviewed_window_is_left_alone(self):
        store.run("DELETE FROM conv_notes WHERE conv_id=?", self.CID)
        self.assertEqual(conv_review.update_weekly(), [])
        self.assertEqual(len(self.calls), 0)

    def test_catch_up_now_button_includes_today(self):
        conv_review.generate(self.CID)                               # 面板上「现在就补」
        self.assertIn(self.today, self.notes()['days'])


if __name__ == '__main__':
    unittest.main()
