"""「卡得多狠」不打今天（10-19 体检：早上 08:30 打了当天的分，那天才开了个头，之后也不再重打）。"""
import _env  # noqa: F401
import unittest
from datetime import date, timedelta

import days
import mood
import store


class FrictionSkipsToday(unittest.TestCase):
    def setUp(self):
        self.today = days.today()
        self.yday = (date.fromisoformat(self.today) - timedelta(days=1)).isoformat()
        with store.tx() as db:
            db.execute('DELETE FROM messages')
            db.execute('DELETE FROM mood_scores')
            for i, d in enumerate((self.yday, self.today)):
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                           (f'm{i}', 'c', 'claude', d + 'T10:00', d, '10:00', 'human', '卡住了', 3))

    def test_today_waits_until_it_is_over(self):
        chat, _ = mood.pending()
        self.assertEqual(chat, [self.yday])


if __name__ == '__main__':
    unittest.main()
