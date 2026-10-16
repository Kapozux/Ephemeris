"""隐私锁（2026-10-16）。

没设密码就什么都不管。设了以后：
    · 页面一被切走（换标签、最小化）就上锁：前端盖上锁屏，同时 POST /api/lock/lock；
      在三页之间点导航不算切走（前端靠 pagehide 分辨）。
    · 服务器这头也锁：没解锁的请求，接口回 423，页面换成锁屏页 —— 新开标签、curl 都看不到内容。
    · 亮着的页面每 20 秒报到一次（/api/lock/ping）；超过 IDLE 秒没人报到（标签关了、电脑睡了）也算锁上。
      关掉最后一个标签：前端发 /api/lock/leave，LEAVE 秒内没有新页面接上就锁（在三页之间跳转赶得上）。
解锁发一个新的随机令牌放 cookie（只这次有效，上锁就作废）。密码只存 PBKDF2 的哈希，在 store 的 app_lock 表。
忘了密码：`python3 lock.py --reset` 把锁去掉（能碰到这台电脑的文件，本来也就能直接读 chats.db）。
"""
import secrets
import sys
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

import store

COOKIE = 'eph_unlock'
IDLE = 60          # 秒：这么久没有亮着的页面报到，就锁
LEAVE = 5          # 秒：页面关掉 / 跳走后，等下一页接上的时间
PING = 20          # 秒：前端报到间隔（给前端看的，IDLE 要比它宽）
MIN_LEN = 4

_mu = threading.Lock()
_st = {'token': None, 'seen': 0.0, 'leave_at': None, 'fails': 0, 'wait_until': 0.0}


def _hash():
    row = store.one('SELECT hash FROM app_lock WHERE id = 1')
    return row['hash'] if row else None


def enabled():
    return bool(_hash())


def _live(now):
    """当前令牌还算不算数（调用时拿着 _mu）。过期了顺手作废。"""
    st = _st
    if not st['token']:
        return False
    if now - st['seen'] > IDLE or (st['leave_at'] and now > st['leave_at']):
        st['token'] = None
        return False
    return True


def ok(token):
    """这个请求能不能看内容。没设密码 → 能。算数的话顺便记一笔「有人在看」。"""
    if not enabled():
        return True
    now = time.time()
    with _mu:
        if not token or not _live(now) or not secrets.compare_digest(token, _st['token']):
            return False
        _st['seen'], _st['leave_at'] = now, None
        return True


def unlock(password):
    """密码对 → 新令牌；不对 → (None, 错误句)。连错几次之后要等一会儿，别被一直猜。"""
    h = _hash()
    if not h:
        return None, '没设密码'
    now = time.time()
    with _mu:
        if now < _st['wait_until']:
            return None, f'错太多次了，{int(_st["wait_until"] - now) + 1} 秒后再试'
    if not check_password_hash(h, password or ''):
        with _mu:
            _st['fails'] += 1
            if _st['fails'] >= 5:
                _st['wait_until'] = now + min(300, 15 * 2 ** (_st['fails'] - 5))
        return None, '密码不对'
    with _mu:
        _st.update(token=secrets.token_urlsafe(32), seen=now, leave_at=None, fails=0, wait_until=0.0)
        return _st['token'], None


def lock():
    with _mu:
        _st['token'] = None


def leave():
    with _mu:
        if _st['token']:
            _st['leave_at'] = time.time() + LEAVE


def set_password(old, new):
    """设 / 改 / 去掉（new 为空）。已经有密码的，先对旧密码。返回 (错误句, 新令牌)；设好后当前这次算解锁。"""
    h = _hash()
    if h and not check_password_hash(h, old or ''):
        return '原来的密码不对', None
    if not new:
        store.run('DELETE FROM app_lock')
        lock()
        return None, None
    if len(new) < MIN_LEN:
        return f'至少 {MIN_LEN} 个字', None
    store.run('INSERT INTO app_lock(id, hash, set_at) VALUES (1, ?, ?) '
              'ON CONFLICT(id) DO UPDATE SET hash = excluded.hash, set_at = excluded.set_at',
              generate_password_hash(new, method='pbkdf2:sha256'), time.strftime('%Y-%m-%dT%H:%M:%S'))
    lock()
    token, err = unlock(new)
    return err, token


if __name__ == '__main__':
    if sys.argv[1:] == ['--reset']:
        store.run('DELETE FROM app_lock')
        print('隐私锁去掉了。重新设：设置 → 隐私锁。（应用开着的话不用重启）')
    else:
        print('有没有设密码：', '设了' if enabled() else '没设')
        print('忘了密码：python3 lock.py --reset')
