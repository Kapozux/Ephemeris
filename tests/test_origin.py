"""只认本机页面发来的请求（2026-10-10）：别的网站、别的域名指过来的一律拒。"""
import _env  # noqa: F401
import unittest

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


if __name__ == '__main__':
    unittest.main()
