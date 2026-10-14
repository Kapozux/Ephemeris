"""Ephemeris —— 本地 Flask 单页应用。

`python3 app.py` -> http://localhost:5055

按 Verbatim 的路子：控制流全在 Python，模型只做叶子（写日记、回顾、打分……）；
索引落 SQLite，读路径全是确定性查询。

路由按页面分开，每个文件只管收参数、调一下、jsonify；做事的在各自的模块里：
    routes_day.py      主页：日历、某一天、搜索、闪回、写日记         → diary / mood / journal / ledger
    routes_review.py   回顾面板、话题河流、按窗口回顾                 → reflect_chat / river / conv_review
    routes_checkin.py  打卡页、星座图                                 → checkin / days
    routes_data.py     窗口与导入、claude.ai 同步、备份、Notion       → index / daily / backup / to_notion
后台常驻的只有两样：启动时把回顾面板算好（reflect_chat.warm），和每分钟看一眼的调度（daily.scheduler）。
"""
import os
import sys
import threading

from urllib.parse import urlsplit

from flask import Flask, abort, request

import daily
import llm
import reflect_chat
import routes_checkin
import routes_data
import routes_day
import routes_review
import store

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0   # 本地工具，改完就要看到
app.config['TEMPLATES_AUTO_RELOAD'] = True   # 上面那条只管 static；模板不加这条要重启才生效
for _bp in (routes_day.bp, routes_review.bp, routes_checkin.bp, routes_data.bp):
    app.register_blueprint(_bp)


LOCAL_HOSTS = ('localhost', '127.0.0.1', '[::1]')


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


@app.context_processor
def _static_version():
    """模板里的 {{ v }}：static 下最新的修改时间，拼在 ?v= 上。改了任何前端文件，浏览器就拿新的。"""
    newest = max(os.path.getmtime(os.path.join(root, f)) for root, _, files in os.walk(STATIC) for f in files)
    return {'v': int(newest)}


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
    err = llm.check()
    print(f"[llm] 后端 {llm.backend()}：{'不可用 —— ' + err if err else '正常'}", flush=True)
    print(f'Ephemeris -> http://localhost:{port}')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
