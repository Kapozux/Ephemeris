"""隐私锁（2026-10-16）：设了密码，没解锁的请求看不到内容；切走（上锁）、关掉、太久没人报到都要重新输。"""
import _env  # noqa: F401
import hashlib
import json
import unittest
from unittest import mock

import app as ephemeris
import lock
import store

H = {'Origin': 'http://localhost:5055'}


class PrivacyLock(unittest.TestCase):
    def setUp(self):
        store.run('DELETE FROM app_lock')
        store.run('DELETE FROM app_lock_keys')
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


class TouchID(unittest.TestCase):
    """Touch ID（WebAuthn）：这里自己当「指纹设备」，用一把 P-256 私钥按浏览器的格式签。"""
    def setUp(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        store.run('DELETE FROM app_lock')
        store.run('DELETE FROM app_lock_keys')
        lock.lock()
        lock._st.update(fails=0, wait_until=0.0)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.c = ephemeris.app.test_client()
        self.c.post('/api/lock/password', json={'new': 'abcd'}, headers=H)

    def tearDown(self):
        store.run('DELETE FROM app_lock')
        store.run('DELETE FROM app_lock_keys')
        lock.lock()

    def cd(self, kind, ch, origin='http://localhost:5055'):
        return lock._b64(json.dumps({'type': f'webauthn.{kind}', 'challenge': ch, 'origin': origin}).encode())

    def register(self):
        from cryptography.hazmat.primitives import serialization
        ch = self.c.post('/api/lock/touch/begin', json={'kind': 'create'}, headers=H).get_json()['challenge']
        spki = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        r = self.c.post('/api/lock/touch/add', headers=H,
                        json={'id': 'cred1', 'spki': lock._b64(spki), 'alg': -7, 'clientData': self.cd('create', ch)})
        self.assertEqual(r.status_code, 200, r.get_json())

    def assertion(self, client, flags=0x05, origin='http://localhost:5055', key=None):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        ch = client.post('/api/lock/touch/begin', json={'kind': 'get'}, headers=H).get_json()['challenge']
        cd = self.cd('get', ch, origin)
        ad = hashlib.sha256(b'localhost').digest() + bytes([flags]) + b'\0\0\0\1'
        sig = (key or self.key).sign(ad + hashlib.sha256(lock._unb64(cd)).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': 'cred1', 'clientData': cd, 'authData': lock._b64(ad), 'sig': lock._b64(sig)}

    def test_touch_unlocks_other_page(self):
        self.register()
        other = ephemeris.app.test_client()
        self.assertEqual(other.get('/api/days').status_code, 423)
        r = other.post('/api/lock/touch/unlock', json=self.assertion(other), headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(other.get('/api/days').status_code, 200)

    def test_bad_signature_wrong_origin_or_no_fingerprint_fail(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.register()
        other = ephemeris.app.test_client()
        for a in (self.assertion(other, key=ec.generate_private_key(ec.SECP256R1())),
                  self.assertion(other, origin='http://evil.example'),
                  self.assertion(other, flags=0x01)):                     # 碰了但没验指纹
            self.assertEqual(other.post('/api/lock/touch/unlock', json=a, headers=H).status_code, 400)
        self.assertEqual(other.get('/api/days').status_code, 423)

    def test_challenge_used_once(self):
        self.register()
        other = ephemeris.app.test_client()
        a = self.assertion(other)
        self.assertEqual(other.post('/api/lock/touch/unlock', json=a, headers=H).status_code, 200)
        lock.lock()
        self.assertEqual(other.post('/api/lock/touch/unlock', json=a, headers=H).status_code, 400)

    def test_register_needs_unlocked(self):
        other = ephemeris.app.test_client()
        self.assertEqual(other.post('/api/lock/touch/begin', json={'kind': 'create'}, headers=H).status_code, 400)
        self.assertEqual(other.post('/api/lock/touch/add', json={}, headers=H).status_code, 423)

    def test_removing_password_drops_keys(self):
        self.register()
        self.assertEqual(len(lock.touch_ids()), 1)
        self.c.post('/api/lock/password', json={'old': 'abcd', 'new': ''}, headers=H)
        self.assertEqual(lock.touch_ids(), [])


if __name__ == '__main__':
    unittest.main()
