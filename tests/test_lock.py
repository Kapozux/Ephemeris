"""隐私锁（2026-10-16）：设了密码，没解锁的请求看不到内容；切走（上锁）、关掉、太久没人报到都要重新输。"""
import _env  # noqa: F401
import unittest
from unittest import mock

import app as ephemeris
import lock
import store

H = {'Origin': 'http://localhost:5055'}


class PrivacyLock(unittest.TestCase):
    def setUp(self):
        store.run('DELETE FROM app_lock')
        lock.lock()
        lock._st.update(fails=0, wait_until=0.0)
        self.c = ephemeris.app.test_client()

    tearDown = setUp

    def set_pw(self, pw='abcd'):
        r = self.c.post('/api/lock/password', json={'new': pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_off_by_default(self):
        self.assertEqual(self.c.get('/api/days').status_code, 200)
        self.assertNotIn(b'lock-page', self.c.get('/').data)

    def test_setting_password_keeps_this_page_unlocked(self):
        self.set_pw()
        self.assertEqual(self.c.get('/api/days').status_code, 200)

    def test_other_browser_sees_nothing(self):
        self.set_pw()
        other = ephemeris.app.test_client()
        self.assertEqual(other.get('/api/days').status_code, 423)
        page = other.get('/chat')
        self.assertIn(b'lock-page', page.data)
        self.assertEqual(other.get('/static/lock.js').status_code, 200)

    def test_lock_then_unlock(self):
        self.set_pw()
        self.c.post('/api/lock/lock', headers=H)
        self.assertEqual(self.c.get('/api/days').status_code, 423)
        self.assertEqual(self.c.post('/api/lock/ping', headers=H).status_code, 423)
        bad = self.c.post('/api/lock/unlock', json={'password': 'nope'}, headers=H)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.c.get('/api/days').status_code, 423)
        self.assertEqual(self.c.post('/api/lock/unlock', json={'password': 'abcd'}, headers=H).status_code, 200)
        self.assertEqual(self.c.get('/api/days').status_code, 200)

    def test_too_many_wrong_guesses_wait(self):
        self.set_pw()
        lock.lock()
        for _ in range(5):
            lock.unlock('nope')
        token, err = lock.unlock('abcd')
        self.assertIsNone(token)
        self.assertIn('秒后再试', err)

    def test_idle_and_leave_expire(self):
        self.set_pw()
        now = lock.time.time()
        with mock.patch.object(lock.time, 'time', return_value=now + lock.IDLE + 1):
            self.assertEqual(self.c.get('/api/days').status_code, 423)
        self.c.post('/api/lock/unlock', json={'password': 'abcd'}, headers=H)
        self.c.post('/api/lock/leave', headers=H)
        self.assertEqual(self.c.get('/api/days').status_code, 200)        # 下一页及时接上：不锁
        self.c.post('/api/lock/leave', headers=H)
        now = lock.time.time()
        with mock.patch.object(lock.time, 'time', return_value=now + lock.LEAVE + 1):
            self.assertEqual(self.c.get('/api/days').status_code, 423)

    def test_change_and_remove_need_old_password(self):
        self.set_pw()
        r = self.c.post('/api/lock/password', json={'old': 'wrong', 'new': ''}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(lock.enabled())
        r = self.c.post('/api/lock/password', json={'old': 'abcd', 'new': ''}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(lock.enabled())
        self.assertEqual(ephemeris.app.test_client().get('/api/days').status_code, 200)

    def test_password_route_is_locked_too(self):
        self.set_pw()
        other = ephemeris.app.test_client()
        self.assertEqual(other.post('/api/lock/password', json={'old': 'abcd', 'new': 'zzzz'}, headers=H).status_code, 423)


if __name__ == '__main__':
    unittest.main()
