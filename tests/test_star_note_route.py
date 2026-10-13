"""打卡页「这个月」那一句：打开月视图只读缓存、不调模型；只有用户点「写一句这个月」（POST /api/stars/note）才写。

踩过（2026-10-14）：打卡页一打开「这个月」，前端就自动 POST /api/stars/note，用户没点也在花额度。
现在前端不自动调（ui_smoke_checkin 验：打开「这个月」不发这个 POST）；后端这边保证：
- GET /api/checkin/month 不调模型，有缓存给缓存，没有给空；
- POST 是用户点的：这周自动试过、失败过也照样写；写不出来要说原因，不能回一个空 note 让按钮没反应。
"""
import _env  # noqa: F401
import unittest

import app as ephemeris
import checkin
import llm
import store

MONTH = '2026-03'


class StarNoteRoute(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('checkins', 'mood_scores', 'star_notes'):
                db.execute(f'DELETE FROM {t}')
        checkin._note_tried.clear()
        checkin.add('2026-03-01', '开心')
        checkin.add('2026-03-02', '平稳')
        self.calls = []
        self.answer = '这个月整体还行。'
        llm.use(self.model)
        self.c = ephemeris.app.test_client()

    def tearDown(self):
        llm.use(None)

    def model(self, prompt):
        self.calls.append(1)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def test_open_month_never_calls_model(self):
        r = self.c.get(f'/api/checkin/month?month={MONTH}').get_json()
        self.assertEqual((r['note'], self.calls), ('', []))

    def test_open_month_shows_cache(self):
        store.run('INSERT OR REPLACE INTO star_notes VALUES (?,?,?,?)', MONTH, 'old', '旧的那句。', '2026-01-01T09:00:00')
        r = self.c.get(f'/api/checkin/month?month={MONTH}').get_json()
        self.assertEqual((r['note'], self.calls), ('旧的那句。', []))

    def test_click_writes(self):
        r = self.c.post(f'/api/stars/note?month={MONTH}').get_json()
        self.assertEqual(r, {'note': '这个月整体还行。'})
        self.assertEqual(self.c.get(f'/api/checkin/month?month={MONTH}').get_json()['note'], '这个月整体还行。')

    def test_click_after_failed_auto_try_still_writes(self):
        checkin._note_tried[MONTH] = __import__('days').week_start()     # 这周已经试过（失败了）
        r = self.c.post(f'/api/stars/note?month={MONTH}').get_json()
        self.assertEqual(r.get('note'), '这个月整体还行。')
        self.assertTrue(self.calls)

    def test_click_failure_says_why(self):
        self.answer = RuntimeError('没有 Gemini key')
        r = self.c.post(f'/api/stars/note?month={MONTH}')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body.get('note'), '')
        self.assertIn('没有 Gemini key', body.get('error', ''))

    def test_click_empty_answer_says_why(self):
        self.answer = ''
        body = self.c.post(f'/api/stars/note?month={MONTH}').get_json()
        self.assertTrue(body.get('error'))

    def test_click_empty_month_says_why(self):
        body = self.c.post('/api/stars/note?month=2025-01').get_json()
        self.assertEqual(self.calls, [])
        self.assertTrue(body.get('error'))


if __name__ == '__main__':
    unittest.main()
