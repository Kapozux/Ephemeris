"""隐私锁（2026-10-16）。

没设密码就什么都不管。设了以后：
    · 页面一被切走（换标签、最小化）就上锁：前端盖上锁屏，同时 POST /api/lock/lock；
      窗口还露着、但焦点去了别的应用 / 窗口（10-16：在终端打字时它也该锁着）也一样；
      在三页之间点导航不算切走（前端靠 pagehide 分辨）。
    · 服务器这头也锁：没解锁的请求，接口回 423，页面换成锁屏页 —— 新开标签、curl 都看不到内容。
    · 亮着的页面每 20 秒报到一次（/api/lock/ping）；超过 IDLE 秒没人报到（标签关了、电脑睡了）也算锁上。
      关掉最后一个标签：前端发 /api/lock/leave，LEAVE 秒内没有新页面接上就锁（在三页之间跳转赶得上）。
也能用 Touch ID 解锁（10-16 用户照着 macOS「密码」App 的锁屏要的）：浏览器的通行密钥（WebAuthn），rpId 只能是 localhost
（127.0.0.1 上用不了，退回输密码）。只存公钥；解锁时核对 challenge、来源、指纹确实验过（UV），再用公钥验签名。
跟输密码共用「连错几次要等」。10-16 去掉过一次（回到窗口就自动弹，后台标签也弹）；10-17 加回来，改成只在锁屏上点了才弹（规则在 lock.js）。
解锁发一个新的随机令牌放 cookie（只这次有效，上锁就作废）。密码只存 PBKDF2 的哈希，在 store 的 app_lock 表。
忘了密码：`python3 lock.py --reset` 把锁去掉（能碰到这台电脑的文件，本来也就能直接读 chats.db）。
"""
import base64
import hashlib
import json
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
RP_ID = 'localhost'
CHALLENGE_TTL = 120   # 秒

_mu = threading.Lock()
_st = {'token': None, 'seen': 0.0, 'leave_at': None, 'fails': 0, 'wait_until': 0.0}
_challenges = {}      # challenge -> (要干什么 'get' / 'create', 过期时刻)


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


def _throttled(now):
    with _mu:
        if now < _st['wait_until']:
            return f'错太多次了，{int(_st["wait_until"] - now) + 1} 秒后再试'


def _fail(now):
    with _mu:
        _st['fails'] += 1
        if _st['fails'] >= 5:
            _st['wait_until'] = now + min(300, 15 * 2 ** (_st['fails'] - 5))


def _grant(now):
    with _mu:
        _st.update(token=secrets.token_urlsafe(32), seen=now, leave_at=None, fails=0, wait_until=0.0)
        return _st['token']


def unlock(password):
    """密码对 → 新令牌；不对 → (None, 错误句)。连错几次之后要等一会儿，别被一直猜。"""
    h = _hash()
    if not h:
        return None, '没设密码'
    now = time.time()
    err = _throttled(now)
    if err:
        return None, err
    if not check_password_hash(h, password or ''):
        _fail(now)
        return None, '密码不对'
    return _grant(now), None


# ---------------------------------------------------------------- Touch ID（WebAuthn）

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    s = s or ''
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def touch_ids():
    return [r['id'] for r in store.q('SELECT id FROM app_lock_keys ORDER BY added_at')]


def challenge(kind):
    """给浏览器一次性的 challenge（kind：'get' 解锁 / 'create' 登记）。"""
    now = time.time()
    c = _b64(secrets.token_bytes(32))
    with _mu:
        for k in [k for k, (_, exp) in _challenges.items() if exp < now]:
            del _challenges[k]
        _challenges[c] = (kind, now + CHALLENGE_TTL)
    return {'challenge': c, 'rp': RP_ID, 'ids': touch_ids()}


def _client_data(raw, kind):
    """浏览器签进去的 clientDataJSON：类型、challenge（用一次就作废）、来源都得对。"""
    try:
        cd = json.loads(_unb64(raw))
    except (ValueError, TypeError):
        return None
    with _mu:
        got = _challenges.pop(cd.get('challenge') or '', None)
    if not got or got[0] != kind or got[1] < time.time() or cd.get('type') != f'webauthn.{kind}':
        return None
    origin = cd.get('origin') or ''
    if origin != f'http://{RP_ID}' and not origin.startswith(f'http://{RP_ID}:'):
        return None
    return cd


def add_key(cid, spki, alg, client_data):
    """登记一把（已经解锁的时候才让调）。浏览器给的是 getPublicKey() 的 SPKI，不用解 CBOR。"""
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    if alg not in (-7, -257) or not cid:
        return '这个浏览器给的密钥类型不认识'
    if not _client_data(client_data, 'create'):
        return '登记过期了，再点一次'
    try:
        der = _unb64(spki)
        load_der_public_key(der)
    except ValueError:
        return '公钥读不出来'
    store.run('INSERT OR IGNORE INTO app_lock_keys(id, spki, alg, added_at) VALUES (?, ?, ?, ?)',
              cid, der, alg, time.strftime('%Y-%m-%dT%H:%M:%S'))
    return None


def remove_keys():
    store.run('DELETE FROM app_lock_keys')


def _verify(row, data, sig):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    pk = load_der_public_key(row['spki'])
    try:
        if row['alg'] == -7:
            pk.verify(sig, data, ec.ECDSA(hashes.SHA256()))
        else:
            pk.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def touch_unlock(cid, client_data, auth_data, sig):
    """Touch ID 验过 → 新令牌；跟 unlock() 一样返回 (令牌, 错误句)。"""
    if not _hash():
        return None, '没设密码'
    now = time.time()
    err = _throttled(now)
    if err:
        return None, err
    row = store.one('SELECT spki, alg FROM app_lock_keys WHERE id = ?', cid or '')
    try:
        ad, s = _unb64(auth_data), _unb64(sig)
    except (ValueError, TypeError):
        ad = s = b''
    ok = (row and len(ad) >= 37 and _client_data(client_data, 'get')
          and ad[:32] == hashlib.sha256(RP_ID.encode()).digest()
          and ad[32] & 0x05 == 0x05                                   # UP（碰了）+ UV（指纹 / 密码验过）
          and _verify(row, ad + hashlib.sha256(_unb64(client_data)).digest(), s))
    if not ok:
        _fail(now)
        return None, 'Touch ID 没通过，输密码吧'
    return _grant(now), None


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
        remove_keys()
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
        remove_keys()
        print('隐私锁去掉了。重新设：设置 → 隐私锁。（应用开着的话不用重启）')
    else:
        print('有没有设密码：', '设了' if enabled() else '没设')
        print('忘了密码：python3 lock.py --reset')
