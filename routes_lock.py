"""隐私锁的接口（做事的在 lock.py）。这几个不受锁拦：没解锁也得能解锁、能报到。"""
from flask import Blueprint, jsonify, make_response, request

import lock

bp = Blueprint('lock', __name__)


def _with_cookie(body, token, status=200):
    r = make_response(jsonify(body), status)
    if token:
        r.set_cookie(lock.COOKIE, token, httponly=True, samesite='Strict', path='/')
    return r


@bp.get('/api/lock/status')
def status():
    return jsonify(on=lock.enabled(), unlocked=lock.ok(request.cookies.get(lock.COOKIE)), ping=lock.PING,
                   touch=len(lock.touch_ids()))


@bp.post('/api/lock/unlock')
def unlock():
    token, err = lock.unlock((request.get_json(silent=True) or {}).get('password'))
    return _with_cookie({'ok': False, 'error': err} if err else {'ok': True}, token, 400 if err else 200)


@bp.post('/api/lock/lock')
def lock_now():
    lock.lock()
    return jsonify(ok=True)


@bp.post('/api/lock/leave')
def leave():
    lock.leave()
    return jsonify(ok=True)


@bp.post('/api/lock/ping')
def ping():
    if lock.ok(request.cookies.get(lock.COOKIE)):
        return jsonify(ok=True)
    return jsonify(locked=True), 423


@bp.post('/api/lock/password')
def password():
    """设 / 改 / 去掉密码。已经设了的话，这个请求本身也得先解锁（下面 app 的 _privacy_lock 管）。"""
    b = request.get_json(silent=True) or {}
    err, token = lock.set_password(b.get('old'), b.get('new'))
    return _with_cookie({'ok': False, 'error': err} if err else {'ok': True, 'on': lock.enabled()}, token, 400 if err else 200)


# ---------------------------------------------------------------- Touch ID（lock.py 里的 WebAuthn）
def _unlocked():
    return lock.enabled() and lock.ok(request.cookies.get(lock.COOKIE))


@bp.post('/api/lock/touch/begin')
def touch_begin():
    """要一个 challenge。登记（create）得先解锁；解锁（get）谁都能要。"""
    kind = (request.get_json(silent=True) or {}).get('kind')
    if kind not in ('get', 'create') or kind == 'create' and not _unlocked():
        return jsonify(ok=False, error='先设好密码、解锁了再登记 Touch ID'), 400
    return jsonify(ok=True, **lock.challenge(kind))


@bp.post('/api/lock/touch/add')
def touch_add():
    if not _unlocked():
        return jsonify(ok=False, error='锁着：先输密码'), 423
    b = request.get_json(silent=True) or {}
    err = lock.add_key(b.get('id'), b.get('spki'), b.get('alg'), b.get('clientData'))
    return (jsonify(ok=False, error=err), 400) if err else jsonify(ok=True, touch=len(lock.touch_ids()))


@bp.post('/api/lock/touch/remove')
def touch_remove():
    if not _unlocked():
        return jsonify(ok=False, error='锁着：先输密码'), 423
    lock.remove_keys()
    return jsonify(ok=True, touch=0)


@bp.post('/api/lock/touch/unlock')
def touch_unlock():
    b = request.get_json(silent=True) or {}
    token, err = lock.touch_unlock(b.get('id'), b.get('clientData'), b.get('authData'), b.get('sig'))
    return _with_cookie({'ok': False, 'error': err} if err else {'ok': True}, token, 400 if err else 200)
