"""Ephemeris —— 本地 Flask 单页应用。

`python3 app.py` -> http://localhost:5055

按 Verbatim 的路子：控制流全在 Python，模型只做叶子（写日记、回顾、打分……）；
索引落 SQLite，读路径全是确定性查询。

路由按页面分开，每个文件只管收参数、调一下、jsonify；做事的在各自的模块里：
    routes_day.py      主页：日历、某一天、搜索、闪回、写日记         → diary / mood / journal / ledger
    routes_review.py   回顾面板、话题河流、按窗口回顾                 → reflect_chat / river / conv_review
    routes_checkin.py  打卡页、星座图                                 → checkin / days
    routes_data.py     窗口与导入、claude.ai 同步、备份、Notion       → index / daily / backup / to_notion
    routes_lock.py     隐私锁：解锁、上锁、报到、设密码                → lock
后台常驻的只有两样：启动时把回顾面板算好（reflect_chat.warm），和每分钟看一眼的调度（daily.scheduler）。
"""
import os
import sys
import threading

from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request

import daily
import llm
import lock
import reflect_chat
import routes_checkin
import routes_data
import routes_day
import routes_lock
import routes_review
import store

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0   # 本地工具，改完就要看到
app.config['TEMPLATES_AUTO_RELOAD'] = True   # 上面那条只管 static；模板不加这条要重启才生效
for _bp in (routes_day.bp, routes_review.bp, routes_checkin.bp, routes_data.bp, routes_lock.bp):
    app.register_blueprint(_bp)


LOCAL_HOSTS = ('localhost', '127.0.0.1', '[::1]')


@app.before_request
def _one_address():
    """浏览器只用 localhost（10-18：127.0.0.1 和 localhost 两个都在用，浏览器当成两个网站，解锁、选的模型各记各的）。
    只跳页面（浏览器地址栏打开的 GET）；接口照常认 127.0.0.1（ask.py、run.sh 用它）。"""
    if request.method == 'GET' and request.headers.get('Sec-Fetch-Mode') == 'navigate' \
            and (request.host or '').split(':')[0] == '127.0.0.1' and not request.path.startswith(('/api/', '/static/')):
        return redirect(request.url.replace('//127.0.0.1', '//localhost', 1), 302)


REAL_DB = os.path.abspath(store.DB_PATH) == os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chats.db')


@app.before_request
def _copy_not_for_people():
    """库不是真的 chats.db（测试拷贝）：浏览器打开页面只给一句话，不给用（10-16 用户在别的会话起的测试版 5056 上问了 11 次，
    拷贝被清掉就没了；10-18）。测试直接调代码，用不着网页；截图工具要拍拷贝时设 EPHEMERIS_UI_TEST=1。"""
    if REAL_DB or os.environ.get('EPHEMERIS_UI_TEST') or request.headers.get('Sec-Fetch-Mode') != 'navigate':
        return
    return ('<!doctype html><meta charset="utf-8"><title>测试拷贝</title>'
            '<body style="font:16px/1.7 system-ui;max-width:32em;margin:20vh auto;padding:0 16px">'
            '<p>这是 Ephemeris 的<b>测试拷贝</b>，不给人用：在这里问的、记的都不会进你的真库，拷贝随时会被删掉。</p>'
            '<p>请用 <a href="http://localhost:5055/chat">localhost:5055</a>。</p></body>'), 403


@app.before_request
def _same_origin_only():
    """只认本机这个页面发来的请求（2026-10-10）。不加的话，浏览器里随便哪个网页都能偷偷给 localhost:5055 发请求：
    触发同步、写日记花你的钱、改 .env 里的 key。Host 不是本机的一律拒（防 DNS rebinding）；
    会改东西的请求（POST / PUT / DELETE）再看 Origin / Sec-Fetch-Site，别的网站来的拒。"""
    host = (request.host or '').rsplit(':', 1)[0] if not (request.host or '').startswith('[') else request.host.split(']')[0] + ']'
    if host not in LOCAL_HOSTS:
        abort(403)
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403)
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).hostname not in ('localhost', '127.0.0.1', '::1'):     # 'null'（沙箱 iframe）也拒
        abort(403)


@app.before_request
def _privacy_lock():
    """隐私锁（lock.py）：设了密码又没解锁，接口回 423，页面换成锁屏页。静态文件和锁自己的接口放行。"""
    p = request.path
    if p.startswith('/static/') or p.startswith('/api/lock/') and p != '/api/lock/password':
        return
    if lock.ok(request.cookies.get(lock.COOKIE)):
        return
    if p.startswith('/api/'):
        return jsonify(locked=True, error='锁着：先输密码'), 423
    return render_template('lock.html'), 200, {'Cache-Control': 'no-store'}


@app.context_processor
def _static_version():
    """模板里的 {{ v }}：static 下最新的修改时间，拼在 ?v= 上。改了任何前端文件，浏览器就拿新的。"""
    newest = max(os.path.getmtime(os.path.join(root, f)) for root, _, files in os.walk(STATIC) for f in files)
    return {'v': int(newest), 'lock_on': lock.enabled(), 'lock_ping': lock.PING, 'lock_touch': bool(lock.touch_ids()),
            'lock_native': lock.native_ok()}


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):              # Windows 控制台默认 GBK：打印 emoji / 生僻字别把进程打崩
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    port = int(os.environ.get('PORT') or 5055)
    store.connect()                                                # 建表 / 老库补列，出错就在启动时报
    threading.Timer(3.0, reflect_chat.warm).start()               # 提前算好，打开面板不用等
    threading.Thread(target=daily.scheduler, daemon=True).start()
    threading.Thread(target=lock.native_probe, daemon=True).start()   # 隐私锁的系统指纹框：头一次要编译几秒，别卡启动
    err = llm.check()
    print(f"[llm] 后端 {llm.backend()}：{'不可用 —— ' + err if err else '正常'}", flush=True)
    print(f'Ephemeris -> http://localhost:{port}')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
