"""Chrome 浏览记录（10-20）：每天拷进来，只增不删；只要 http(s)；不进日记（别的模块不读这张表）。"""
import _env  # noqa: F401
import os
import sqlite3
import unittest
from datetime import datetime

import backup
import browsing
import store


def vt(iso):
    return int((datetime.fromisoformat(iso) - browsing.EPOCH).total_seconds() * 1e6)


def make_history(profile, visits):
    d = os.path.join(browsing.DIR, profile)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, 'History')
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)')
    db.execute('CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, visit_duration INTEGER)')
    for i, (when, url, title) in enumerate(visits, 1):
        db.execute('INSERT INTO urls VALUES (?,?,?)', (i, url, title))
        db.execute('INSERT INTO visits VALUES (?,?,?,?)', (i, i, vt(when), 30_000_000))
    db.commit()
    db.close()


class Sync(unittest.TestCase):
    def setUp(self):
        store.run('DELETE FROM browse_visits')
        make_history('Default', [('2026-10-19T02:00:00+00:00', 'https://example.com/a', '甲'),
                                 ('2026-10-19T03:00:00+00:00', 'chrome://settings', '设置')])
        make_history('System Profile', [('2026-10-19T02:00:00+00:00', 'https://example.com/x', '不读')])

    def rows(self):
        return store.q('SELECT profile, date, hhmm, url, title, secs FROM browse_visits ORDER BY vt')

    def test_reads_http_visits_with_local_day(self):
        self.assertEqual(browsing.sync(say=None), {'profiles': 1, 'new': 1})
        self.assertEqual([dict(r) for r in self.rows()],
                         [{'profile': 'Default', 'date': '2026-10-19', 'hhmm': '10:00',
                           'url': 'https://example.com/a', 'title': '甲', 'secs': 30}])

    def test_chrome_forgetting_old_visits_keeps_ours(self):
        browsing.sync(say=None)
        make_history('Default', [('2026-10-20T02:00:00+00:00', 'https://example.com/b', '乙')])   # 旧的被 Chrome 删了
        self.assertEqual(browsing.sync(say=None)['new'], 1)
        self.assertEqual([r['url'] for r in self.rows()], ['https://example.com/a', 'https://example.com/b'])
        self.assertEqual(browsing.sync(say=None)['new'], 0)

    def test_backed_up_row_by_row(self):
        self.assertEqual(backup.CORE['browse_visits'], 'id')


if __name__ == '__main__':
    unittest.main()
