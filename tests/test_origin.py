"""只认本机页面发来的请求（2026-10-10）：别的网站、别的域名指过来的一律拒。"""
import _env  # noqa: F401
import unittest
from unittest import mock

import app as ephemeris


class SameOriginOnly(unittest.TestCase):
    def setUp(self):
        self.c = ephemeris.app.test_client()

    def test_other_sites_cannot_write(self):
        for h in ({'Origin': 'https://evil.example'}, {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.c.post('/api/setup', json={}, headers=h).status_code, 403, h)
        self.assertEqual(self.c.post('/api/setup', json={}, headers={'Origin': 'http://localhost:5055'}).status_code, 200)

    def test_other_hostnames_rejected(self):                             # DNS rebinding
        self.assertEqual(self.c.get('/api/days', headers={'Host': 'evil.example:5055'}).status_code, 403)
        self.assertEqual(self.c.get('/api/days', headers={'Host': '127.0.0.1:5055'}).status_code, 200)

    @mock.patch.object(ephemeris, 'REAL_DB', True)
    def test_browser_pages_go_to_localhost(self):                      # 10-18：只用一个地址
        nav = {'Host': '127.0.0.1:5055', 'Sec-Fetch-Mode': 'navigate'}
        r = self.c.get('/chat/3?x=1', headers=nav)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers['Location'], 'http://localhost:5055/chat/3?x=1')
        self.assertEqual(self.c.get('/api/days', headers=nav).status_code, 200)            # 接口不跳
        self.assertNotEqual(self.c.get('/chat', headers={'Host': '127.0.0.1:5055'}).status_code, 302)   # 脚本（没 Sec-Fetch）不跳
        self.assertNotEqual(self.c.get('/chat', headers={'Host': 'localhost:5055', 'Sec-Fetch-Mode': 'navigate'}).status_code, 302)

    def test_copy_db_not_for_people(self):                             # 10-18：测试版别给人用（测试用的是临时库）
        self.assertFalse(ephemeris.REAL_DB)
        r = self.c.get('/chat', headers={'Host': 'localhost:5055', 'Sec-Fetch-Mode': 'navigate'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('测试拷贝', r.get_data(as_text=True))
        self.assertEqual(self.c.get('/api/days', headers={'Sec-Fetch-Mode': 'navigate'}).status_code, 403)
        self.assertEqual(self.c.get('/api/days').status_code, 200)                         # 脚本 / 测试照常


if __name__ == '__main__':
    unittest.main()
