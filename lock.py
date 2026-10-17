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
10-17 又加了一种、优先用它：macOS 自己的指纹框（touchid.swift，LocalAuthentication，跟 sudo 那个一样）——
用户嫌浏览器那个通行密钥框难用、要点一下才弹也麻烦。不用登记；服务器跑在这台 Mac 上，直接弹，按对了发令牌。
第一次用时现编译 touchid（要 Xcode 命令行工具），编不出来 / 这台没指纹就只剩上面那两种。
10-17 还有 Mac App（mac/Ephemeris.swift）：锁屏是原生的，指纹嵌在窗口里（LAAuthenticationView，跟「密码」App 一样不弹框）。
App 自己验过指纹后拿 APP_KEY_FILE 里的钥匙换令牌（app_unlock）。钥匙文件只有这个用户能读；能读它的程序本来也能直接读 chats.db。
忘了密码：`python3 lock.py --reset` 把锁去掉（能碰到这台电脑的文件，本来也就能直接读 chats.db）。
"""
import base64
import hashlib
import json
import os
import secrets
import subprocess
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


# ---------------------------------------------------------------- macOS 自己的指纹框（touchid.swift）
HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE_SRC = os.path.join(HERE, 'touchid.swift')
NATIVE_BIN = os.path.join(HERE, 'touchid')            # 编译出来的（.gitignore 白名单外，不进仓库）
NATIVE_TIMEOUT = 60
_native = {'ok': None, 'proc': None}


def native_ok():
    """这台 Mac 能不能弹系统指纹框。第一次问的时候编译 + 试一下（几秒），之后记着。还没试过返回 False（别卡住页面）。"""
    return bool(_native['ok'])


def native_probe():
    """编译（源码比二进制新时）+ check。app 启动时在后台调一次。"""
    if sys.platform != 'darwin' or not os.path.exists(NATIVE_SRC):
        _native['ok'] = False
        return False
    try:
        if not os.path.exists(NATIVE_BIN) or os.path.getmtime(NATIVE_BIN) < os.path.getmtime(NATIVE_SRC):
            subprocess.run(['swiftc', '-O', NATIVE_SRC, '-o', NATIVE_BIN], capture_output=True, timeout=180, check=True)
        _native['ok'] = subprocess.run([NATIVE_BIN, 'check'], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        _native['ok'] = False
    return _native['ok']


def native_unlock():
    """弹系统指纹框，等你按（最多 NATIVE_TIMEOUT 秒）。→ (令牌, 错误句)。同时只弹一个；cancel_native() 能收起来。"""
    if not _hash():
        return None, '没设密码'
    if not native_ok():
        return None, '这台电脑用不了系统指纹'
    now = time.time()
    err = _throttled(now)
    if err:
        return None, err
    with _mu:
        if _native['proc'] and _native['proc'].poll() is None:
            return None, '指纹框已经弹着了'
        p = _native['proc'] = subprocess.Popen([NATIVE_BIN, 'auth', '解锁 Ephemeris'],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        code = p.wait(timeout=NATIVE_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.terminate()
        code = 1
    finally:
        with _mu:
            if _native['proc'] is p:
                _native['proc'] = None
    if code != 0:
        return None, '没按指纹'                         # 取消、切走、按错几次：系统自己管次数，这里不算「输错密码」
    return _grant(time.time()), None


# ---------------------------------------------------------------- Mac App（mac/Ephemeris.swift）
APP_KEY_FILE = os.path.join(HERE, '.app_key')           # 白名单外，不进仓库


def app_key():
    """App 和服务器之间的钥匙：没有就生成一把，文件只给本人读写。"""
    if not os.path.exists(APP_KEY_FILE):
        fd = os.open(APP_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(secrets.token_urlsafe(32))
    with open(APP_KEY_FILE, encoding='utf-8') as f:
        return f.read().strip()


def app_unlock(key):
    """App 那边验过指纹了：拿钥匙换令牌。→ (令牌, 错误句)。钥匙不对跟输错密码一样算次数。"""
    if not _hash():
        return None, '没设密码'
    now = time.time()
    err = _throttled(now)
    if err:
        return None, err
    if not key or not secrets.compare_digest(str(key), app_key()):
        _fail(now)
        return None, '钥匙不对'
    return _grant(now), None


def cancel_native():
    """页面切走了：收起还弹着的指纹框（touchid 收到 SIGTERM 会 invalidate 再退出）。"""
    with _mu:
        p = _native['proc']
    if p and p.poll() is None:
        p.terminate()
        return True
    return False


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
