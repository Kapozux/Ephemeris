"""终端里的「问」（ask.py）碰上隐私锁（2026-10-17：ask.py 一跑就「锁着：先输密码」）：
服务器回 423 就问一次密码，解锁后拿着令牌接着发；令牌作废了（网页那边上了锁）再问。临时起一个服务器，碰不到真的 5055。"""
import _env  # noqa: F401
import threading
import unittest
from unittest import mock

from werkzeug.serving import make_server

import app as ephemeris
import ask
import lock
import store

H = {'Origin': 'http://localhost:5055'}


class AskUnlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = make_server('127.0.0.1', 0, ephemeris.app, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = ask.BASE
        ask.BASE = f'http://127.0.0.1:{cls.srv.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        ask.BASE = cls.base

    def setUp(self):
        store.run('DELETE FROM app_lock')
        lock.lock()
        lock._st.update(fails=0, wait_until=0.0)
        ask.TOKEN[0] = None
        self.assertEqual(ephemeris.app.test_client().post('/api/lock/password', json={'new': 'abcd'}, headers=H).status_code, 200)
        lock.lock()

    def tearDown(self):
        store.run('DELETE FROM app_lock')
        lock.lock()

    def test_asks_password_once_then_goes_through(self):
        with mock.patch('getpass.getpass', side_effect=['wrong', 'abcd']) as gp:
            r = ask._post('/api/qa/new', {})
        self.assertIn('thread', r)
        self.assertEqual(gp.call_count, 2)                         # 错一次、对一次
        with mock.patch('getpass.getpass') as gp:
            self.assertIn('threads', ask._get('/api/qa'))
        gp.assert_not_called()                                     # 解开了：接着用不再问

    def test_web_locking_asks_again(self):
        with mock.patch('getpass.getpass', return_value='abcd'):
            ask._get('/api/qa')
        lock.lock()                                                # 网页那边一切走就上锁
        with mock.patch('getpass.getpass', return_value='abcd') as gp:
            self.assertIn('threads', ask._get('/api/qa'))
        self.assertEqual(gp.call_count, 1)

    def test_empty_password_quits(self):
        with mock.patch('getpass.getpass', return_value=''), self.assertRaises(SystemExit):
            ask._get('/api/qa')


class Commands(unittest.TestCase):
    """/ 开头的是命令，不当问题发给 Claude（10-17：「python ask.py /new」被问了两遍）。"""
    def setUp(self):
        self.sent = []
        self.p = mock.patch.object(ask, '_post', side_effect=lambda path, body: (self.sent.append(path), {'thread': 7})[1])
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_new_opens_thread_not_question(self):
        ask.command('/new')
        self.assertEqual(self.sent, ['/api/qa/new'])
        self.assertEqual(ask.THREAD[0], 7)

    def test_unknown_command_not_sent(self):
        with mock.patch('builtins.print') as pr:
            ask.command('/neww')
        self.assertEqual(self.sent, [])
        self.assertIn('不认识', pr.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
