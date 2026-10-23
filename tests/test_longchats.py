"""长聊清单（10-23：长期在聊的窗口标出来，换账号时带走）：按聊过的天数排、分还在聊 / 聊完了、标要迁、续集并进去。"""
import _env  # noqa: F401
import unittest
from datetime import date, timedelta

import app
import longchats
import store

H = {'Origin': 'http://localhost:5055'}
TODAY = '2026-10-23'


def conv(cid, title, start, n, source='claude'):
    """从 start 起连着 n 天每天一条。"""
    d0 = date.fromisoformat(start)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    store.run('INSERT INTO conversations(id, source, title, n_days, n_msgs, first_ts, last_ts) VALUES (?,?,?,?,?,?,?)',
              cid, source, title, n, n, dates[0] + 'T10:00', dates[-1] + 'T10:00')
    for i, d in enumerate(dates):
        store.run('INSERT INTO messages(id, conv_id, source, ts, date, hhmm, role, text) VALUES (?,?,?,?,?,?,?,?)',
                  f'{cid}-{i}', cid, source, d + 'T10:00', d, '10:00', 'human', '嗯')


class LongChats(unittest.TestCase):
    def setUp(self):
        with store.tx() as db:
            for t in ('messages', 'conversations', 'long_chats'):
                db.execute(f'DELETE FROM {t}')
        conv('plan', '大规划师', '2026-08-01', 40)                # 聊完了（9-09 最后一次）
        conv('phy', 'PHY SUMMER', '2026-06-01', 20)
        conv('phy2', 'PHY_2', '2026-10-01', 15)                  # 续集，还在聊
        conv('once', '随便问问', '2026-10-20', 2)                # 太短，不算
        conv('cc1', 'CC·写代码', '2026-09-01', 50, source='cc')    # 写代码的不算

    def test_rank_and_active(self):
        ls = longchats.lines(TODAY)
        self.assertEqual([x['id'] for x in ls], ['plan', 'phy', 'phy2'])
        self.assertEqual([x['active'] for x in ls], [False, False, True])
        self.assertEqual(ls[2]['guess']['id'], 'phy')           # PHY_2 像是接着 PHY SUMMER

    def test_join_mark_split(self):
        c = app.app.test_client()
        r = c.post('/api/longchats/phy2', json={'after': 'phy', 'move': True}, headers=H).get_json()['lines']
        phy = next(x for x in r if x['id'] == 'phy')
        self.assertEqual([p['id'] for p in phy['parts']], ['phy2'])
        self.assertEqual(phy['days'], 35)                        # 两段合起来算
        self.assertTrue(phy['active'])                           # 续集还在聊，整条线就还在聊
        self.assertNotIn('phy2', [x['id'] for x in r])
        longchats.mark('phy', True)
        longchats.follow('phy2', None)                           # 拆开：续集自己的标记还在
        ls = {x['id']: x for x in longchats.lines(TODAY)}
        self.assertTrue(ls['phy2']['move'])
        longchats.mark('phy', None)
        self.assertIsNone({x['id']: x for x in longchats.lines(TODAY)}['phy']['move'])
        self.assertEqual(c.post('/api/longchats/nope', json={'move': True}, headers=H).status_code, 400)
        self.assertEqual(c.post('/api/longchats/phy', json={'after': 'phy'}, headers=H).status_code, 400)


if __name__ == '__main__':
    unittest.main()
