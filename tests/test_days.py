"""days.py：「哪天」和「那天过得怎么样」两条规则。日历、星座图、回顾、闪回卡、打卡回话都从这里拿。"""
import _env  # noqa: F401
import unittest
from datetime import datetime, timezone

import checkin
import days
import index as idx
import mood


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Bucket(unittest.TestCase):
    def test_evening_utc_is_next_local_day(self):
        # UTC 13:00 = 北京 21:00，同一天
        self.assertEqual(days.bucket(utc(2026, 3, 1, 13, 0)), ('2026-03-01', '21:00'))
        # UTC 17:00 = 北京次日 01:00，凌晨 4 点前 → 还算 3 月 1 日，时刻照写 01:00
        self.assertEqual(days.bucket(utc(2026, 3, 1, 17, 0)), ('2026-03-01', '01:00'))
        # UTC 20:00 = 北京次日 04:00，过了分界 → 3 月 2 日
        self.assertEqual(days.bucket(utc(2026, 3, 1, 20, 0)), ('2026-03-02', '04:00'))

    def test_today_uses_cutoff(self):
        self.assertEqual(days.today(datetime(2026, 3, 2, 3, 59, tzinfo=days.LOCAL_TZ)), '2026-03-01')
        self.assertEqual(days.today(datetime(2026, 3, 2, 4, 0, tzinfo=days.LOCAL_TZ)), '2026-03-02')


class Feel(unittest.TestCase):
    def setUp(self):
        checkin.ensure_table()
        mood.ensure_table()
        db = idx.connect()
        db.execute('DELETE FROM checkins')
        db.execute('DELETE FROM mood_scores')
        db.commit()

    def hand(self, date, v, label, evidence=''):
        db = idx.connect()
        db.execute("INSERT INTO mood_scores(date, src, valence, label, evidence) VALUES (?, 'hand', ?, ?, ?)",
                   (date, v, label, evidence))
        db.commit()

    def test_checkin_beats_hand(self):
        self.hand('2026-03-01', -1.5, '低落')
        checkin.add('2026-03-01', '开心', '见到朋友')
        f = days.feel('2026-03-01')
        self.assertEqual((f['src'], f['v'], f['word'], f['note']), ('checkin', 1.5, '开心', '见到朋友'))

    def test_hand_only(self):
        self.hand('2026-03-02', -1.0, '焦虑', '写不完')
        f = days.feel('2026-03-02')
        self.assertEqual((f['src'], f['v'], f['word'], f['words'], f['note']), ('hand', -1.0, '焦虑', '焦虑', '写不完'))

    def test_several_checkins_average_last_word_and_sequence(self):
        for w in ('崩溃', '崩溃', '满足'):           # 连着两次「崩溃」只算一次
            checkin.add('2026-03-03', w)
        f = days.feel('2026-03-03')
        self.assertEqual(f['v'], round((-2 - 2 + 1) / 3, 2))
        self.assertEqual((f['word'], f['words'], f['n']), ('满足', '崩溃→满足', 3))

    def test_nothing_recorded(self):
        self.assertIsNone(days.feel('2026-03-04'))

    def test_range_is_inclusive_and_sorted(self):
        self.hand('2026-03-10', 0, '平稳')
        checkin.add('2026-03-01', '开心')
        checkin.add('2026-03-31', '疲惫')
        self.hand('2026-04-01', 1, '满足')
        self.assertEqual(list(days.feel_range('2026-03-01', '2026-03-31')), ['2026-03-01', '2026-03-10', '2026-03-31'])
        self.assertEqual([s['date'] for s in checkin.month_stars('2026-03')], ['2026-03-01', '2026-03-10', '2026-03-31'])

    def test_calendar_marks_checkin_days(self):
        self.hand('2026-03-05', 1, '满足')
        checkin.add('2026-03-06', '低落')
        sig = mood.all_signals()
        self.assertEqual(sig['2026-03-05'], {'mood': 1})
        self.assertEqual(sig['2026-03-06'], {'mood': -1.5, 'checkin': True})


if __name__ == '__main__':
    unittest.main()
