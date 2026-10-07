"""早上那一串（daily.morning）：拉完写缺的日记（今天还没过完，不写），同步失败也照样整理。
claude.ai 和整理都换成假的 —— 只测这一串怎么接。"""
import _env  # noqa: F401
import unittest
from datetime import datetime, timedelta

import claude_sync
import daily
import days
import diary
import jobs
import llm
import store

OLD = '2026-02-01'            # 很早以前缺日记的一天（比如导入了旧聊天）：早上不自动写


class Morning(unittest.TestCase):
    def setUp(self):
        today = days.today()
        self.past = (datetime.fromisoformat(today) - timedelta(days=2)).date().isoformat()
        with store.tx() as db:
            for t in ('messages', 'diaries', 'conversations'):
                db.execute(f'DELETE FROM {t}')
            db.execute("INSERT INTO conversations(id, source, title, first_ts, last_ts) "
                       "VALUES ('c', 'claude', 't', ?, ?)", (OLD, today))
            for i, d in enumerate((OLD, self.past, today)):
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                           (f'm{i}', 'c', 'claude', d + 'T10:00', d, '10:00', 'human', '字' * 900, 900))
        self.saved = claude_sync.sync, daily.tidy, diary.RETRY_AFTER
        self.tidied = []
        daily.tidy = lambda call_model: self.tidied.append(1)
        diary.RETRY_AFTER = 0
        llm.use(lambda prompt: '')                  # 写不出来 → 记在补全任务的 errors 里，正好看它试了哪几天
        jobs.state(diary.BATCH)['errors'] = []

    def tearDown(self):
        claude_sync.sync, daily.tidy, diary.RETRY_AFTER = self.saved
        llm.use(None)

    def run_morning(self):
        jobs.run(daily.JOB, daily.morning, False, **daily.FRESH)
        return jobs.get(daily.JOB, **daily.IDLE)

    def test_writes_missing_days_but_not_today(self):
        claude_sync.sync = lambda force=False, progress=None: {'days': [self.past], 'new_msgs': 1}
        st = self.run_morning()
        # 写不出来会重试一次，所以同一天可能记两条；要看的是只试了以前那天，没碰今天
        self.assertEqual({e['date'] for e in diary.batch_status()['errors']}, {self.past})   # 最近的写；很早的 OLD、今天都不写
        self.assertEqual((st['running'], st['error'], st['last']['new_msgs']), (False, None, 1))
        self.assertEqual(self.tidied, [1])

    def test_sync_failure_still_tidies_and_writes_nothing(self):
        def boom(force=False, progress=None):
            raise claude_sync.SyncError('sessionKey 过期')
        claude_sync.sync = boom
        st = self.run_morning()
        self.assertIn('sessionKey 过期', st['error'])
        self.assertEqual(diary.batch_status()['errors'], [])
        self.assertEqual(self.tidied, [1])


if __name__ == '__main__':
    unittest.main()
