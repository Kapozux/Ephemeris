"""Ephemeris —— 本地 Flask 单页应用。

`python3 app.py` -> http://localhost:5055

按 Verbatim 的路子：控制流全在 Python，模型只在「生成日记」那一步做叶子；
索引落 SQLite，读路径全是确定性查询。
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

import bundle
import checkin
import claude_sync
import journal
import mood
import ledger
import conv_review
import index as idx
import reflect_chat

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0   # 本地工具，改完就要看到
app.config['TEMPLATES_AUTO_RELOAD'] = True   # 上面那条只管 static；模板不加这条要重启才生效
DB = idx.connect()
_lock = idx.LOCK          # 和 index / reflect_chat 共用
_generating = set()
_batch = {'running': False, 'total': 0, 'done': 0, 'failed': 0, 'current': None,
          'started': None, 'stop': False, 'errors': []}


def _fail(date, why):
    """批量补全里某天失败的原因。以前是 except: pass，跑完只剩个计数，没法查。"""
    print(f'[diary] {date} 失败：{why}', flush=True)
    errs = _batch.setdefault('errors', [])
    errs.append({'date': date, 'error': why[:300]})
    del errs[:-30]

# flash-lite 一天约 6 万字进去，按「回顾面板约定」记的 $0.006/次 放宽一点算
COST_PER_DAY = 0.01
MIN_CHARS = 800          # 少于这个字数的天不值得写日记


def q(sql, *args):
    with _lock:
        return [dict(r) for r in DB.execute(sql, args).fetchall()]


def _parse_diary(d):
    for k in ('topics', 'artifacts', 'open_loops'):
        try:
            d[k] = json.loads(d.get(k) or '[]')
        except (TypeError, ValueError):
            d[k] = []
    return d


@app.route('/')
def home():
    static = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    v = int(max(os.path.getmtime(os.path.join(static, f)) for f in ('app.js', 'style.css')))
    return render_template('index.html', v=v)


# ---------------------------------------------------------------- 日历

@app.route('/api/days')
def api_days():
    rows = q("""SELECT m.date,
                       COUNT(DISTINCT m.conv_id) threads,
                       COUNT(*) msgs, SUM(m.chars) chars,
                       MIN(m.hhmm) first_at, MAX(m.hhmm) last_at
                  FROM messages m WHERE m.role = 'human'
                 GROUP BY m.date ORDER BY m.date""")
    diaries = {r['date']: r for r in q('SELECT date, headline, mood FROM diaries')}
    for r in rows:
        d = diaries.get(r['date'])
        r['has_diary'] = bool(d)
        r['headline'] = d['headline'] if d else ''
        r['mood'] = d['mood'] if d else ''
    # 手写日记：有聊天的那天打个标；只有手写的那天单独补一行（窗口数 0）
    jd = journal.dates()
    for r in rows:
        r['journal'] = jd.get(r['date'], 0)
    have = {r['date'] for r in rows}
    rows += [{'date': d, 'threads': 0, 'msgs': 0, 'chars': 0, 'first_at': '', 'last_at': '',
              'has_diary': False, 'headline': '', 'mood': '', 'journal': c}
             for d, c in jd.items() if d not in have]
    have = {r['date'] for r in rows}
    rows += [{'date': d, 'threads': 0, 'msgs': 0, 'chars': 0, 'first_at': '', 'last_at': '',
              'has_diary': False, 'headline': '', 'mood': '', 'journal': 0}
             for d in checkin.dates() if d not in have]
    rows.sort(key=lambda r: r['date'])
    # 两个指标：feel = 过得怎么样（手写，-2..+2），stuck = 卡得多狠（聊天，0..4）
    sig = mood.all_signals()
    for r in rows:
        s = sig.get(r['date'], {})
        r['feel'], r['stuck'], r['ck'] = s.get('mood'), s.get('friction'), bool(s.get('checkin'))
    missing = [r for r in rows if not r['has_diary'] and (r['chars'] or 0) >= MIN_CHARS]
    return jsonify({'days': rows, 'total_days': len(rows),
                    'missing': len(missing),
                    'cost_estimate': round(len(missing) * COST_PER_DAY, 2),
                    'latest': rows[-1]['date'] if rows else None})


# ---------------------------------------------------------------- 一天

@app.route('/api/day/<date>')
def api_day(date):
    """某一天：日记 + 按窗口分组的全部消息（含 AI 回复，前端决定显不显示）。"""
    msgs = q("""SELECT m.id, m.conv_id, m.source, m.hhmm, m.role, m.text, m.chars,
                       c.title conv_title, c.first_ts, c.last_ts
                  FROM messages m JOIN conversations c ON c.id = m.conv_id
                 WHERE m.date = ? ORDER BY m.ts""", date)
    threads = {}
    for m in msgs:
        t = threads.setdefault(m['conv_id'], {
            'conv_id': m['conv_id'], 'title': m['conv_title'], 'source': m['source'],
            'span_from': m['first_ts'][:10], 'span_to': m['last_ts'][:10],
            'messages': []})
        t['messages'].append({'id': m['id'], 'hhmm': m['hhmm'], 'role': m['role'],
                              'text': m['text'], 'chars': m['chars']})
    out = []
    for t in threads.values():
        human = [x for x in t['messages'] if x['role'] == 'human']
        if not human:
            continue
        t['first'] = human[0]['hhmm']
        t['last'] = human[-1]['hhmm']
        t['n_msgs'] = len(human)
        t['chars'] = sum(x['chars'] for x in human)
        t['multi_day'] = t['span_from'] != t['span_to']
        out.append(t)
    out.sort(key=lambda x: x['first'])

    d = q('SELECT * FROM diaries WHERE date = ?', date)
    nav = q("""SELECT
                 (SELECT MAX(d) FROM (SELECT date d FROM messages WHERE role='human' AND date < ?
                                      UNION SELECT date FROM journal WHERE chars > 0 AND date < ?)) prev,
                 (SELECT MIN(d) FROM (SELECT date d FROM messages WHERE role='human' AND date > ?
                                      UNION SELECT date FROM journal WHERE chars > 0 AND date > ?)) next""",
            date, date, date, date)[0]
    return jsonify({'date': date, 'threads': out,
                    'diary': _parse_diary(d[0]) if d else None,
                    'themes': ledger.chips(date),
                    'journal': journal.for_date(date),
                    'signals': mood.day_signals(date),
                    'generating': date in _generating,
                    'prev': nav['prev'], 'next': nav['next']})


@app.route('/api/conversation/<conv_id>')
def api_conv(conv_id):
    """一个窗口横跨了哪些天 —— 这是整个产品和「回 Claude 翻记录」的区别。"""
    c = q('SELECT * FROM conversations WHERE id = ?', conv_id)
    if not c:
        return jsonify({'error': 'not found'}), 404
    days = q("""SELECT date, COUNT(*) msgs, SUM(chars) chars
                  FROM messages WHERE conv_id = ? AND role='human'
                 GROUP BY date ORDER BY date""", conv_id)
    return jsonify({'conversation': c[0], 'days': days})


# ---------------------------------------------------------------- 搜索

@app.route('/api/search')
def api_search():
    kw = (request.args.get('q') or '').strip()
    if len(kw) < 2:
        return jsonify({'hits': []})
    safe = '"' + kw.replace('"', '""') + '"'
    hits = q("""SELECT f.date, f.conv_title, f.msg_id, m.conv_id, m.hhmm,
                       snippet(msg_fts, 0, '<mark>', '</mark>', '…', 20) snip
                  FROM msg_fts f JOIN messages m ON m.id = f.msg_id
                 WHERE msg_fts MATCH ?
                 ORDER BY f.date DESC LIMIT 100""", safe)
    # 手写日记单独一路，排在前面：自己写的比聊天里的更值得先看
    jh = [{'date': h['date'], 'kind': 'journal', 'page_id': h['page_id'], 'snip': h['snip'],
           'conv_title': '手写日记', 'hhmm': ''} for h in journal.search(kw)]
    return jsonify({'hits': jh + hits, 'q': kw})


# ---------------------------------------------------------------- 日记生成

_day_bundle = bundle.day_bundle


def _generate_one(date):
    import diary as diary_mod
    entry = diary_mod.generate(_day_bundle(date), refresh=True,
                               prev=ledger.previous_diary(date),
                               ledger=ledger.render_for_prompt(date))
    if not entry:
        # generate() 返回 None = 模型输出解析不成 JSON。不记下来的话批量跑完只剩一个数字。
        _fail(date, '模型返回解析不出 JSON')
        return False
    with _lock:
        DB.execute(
            'INSERT OR REPLACE INTO diaries VALUES (?,?,?,?,?,?,?,?,?)',
            (date, entry.get('headline', ''), entry.get('narrative', ''),
             json.dumps(entry.get('topics') or [], ensure_ascii=False),
             json.dumps(entry.get('artifacts') or [], ensure_ascii=False),
             json.dumps(entry.get('open_loops') or [], ensure_ascii=False),
             entry.get('mood', ''), os.environ.get('REFLECT_MODEL', ''),
             datetime.now().isoformat(timespec='seconds')))
        DB.commit()
    ledger.record(date, ledger.match(date, entry.get('threads'), diary_mod._call_model))
    return True


def _generate_bg(date):
    try:
        _generate_one(date)
    except Exception as e:  # noqa: BLE001
        app.logger.warning('diary %s failed: %s', date, e)
    finally:
        _generating.discard(date)


@app.route('/api/diary/<date>', methods=['POST'])
def api_diary_make(date):
    if date in _generating:
        return jsonify({'generating': True})
    _generating.add(date)
    threading.Thread(target=_generate_bg, args=(date,), daemon=True).start()
    return jsonify({'generating': True})


def _try_one(date):
    try:
        return _generate_one(date)
    except Exception as e:                                  # noqa: BLE001
        _fail(date, f'{type(e).__name__}: {e}')
        return False


def _batch_worker(dates):
    # 日记是一天接一天的（上一篇 + 账本），必须从早往晚写
    dates = sorted(dates)
    _batch.update(running=True, total=len(dates), done=0, failed=0, errors=[],
                  started=datetime.now().isoformat(timespec='seconds'), stop=False)
    try:
        for d in dates:
            if _batch['stop']:
                break
            _batch['current'] = d
            _generating.add(d)
            try:
                # 模型偶尔抽风（返回的东西解析不成 JSON、或者超时），重试一次就好。
                # 2026-09-20 实测：73 天里 1 天这样，隔一会儿原样再跑就过了。
                ok = _try_one(d)
                if not ok:
                    time.sleep(3)
                    ok = _try_one(d)
            finally:
                _generating.discard(d)
            _batch['done' if ok else 'failed'] += 1
    finally:
        _batch.update(running=False, current=None)


def _missing_dates(force=False):
    have = set() if force else {r['date'] for r in q('SELECT date FROM diaries')}
    # 别名不能叫 chars：SQLite 在 HAVING 里会把它解析成原始列，不是聚合值（实测 35 vs 271 天）
    rows = q("""SELECT date, SUM(chars) total FROM messages WHERE role='human'
                 GROUP BY date HAVING total >= ? ORDER BY date DESC""", MIN_CHARS)
    return [r['date'] for r in rows if r['date'] not in have]


@app.route('/api/diary/batch', methods=['POST'])
def api_batch_start():
    """补全日记。顺序跑（限流友好），前端轮询进度。

    默认只做还没有日记的天；?force=1 连已有的也重写（改了 prompt 或前情逻辑之后用）。
    """
    if _batch['running']:
        return jsonify(_batch)
    dates = _missing_dates(force=request.args.get('force') == '1')
    if not dates:
        return jsonify({**_batch, 'total': 0})
    threading.Thread(target=_batch_worker, args=(dates,), daemon=True).start()
    return jsonify({**_batch, 'running': True, 'total': len(dates)})


@app.route('/api/diary/batch', methods=['DELETE'])
def api_batch_stop():
    _batch['stop'] = True
    return jsonify(_batch)


@app.route('/api/diary/batch')
def api_batch_status():
    return jsonify(_batch)


# ---------------------------------------------------------------- 打卡 + 星座图

@app.route('/api/checkin')
def api_checkin_get():
    date = request.args.get('date') or checkin.today()
    return jsonify({'date': date, 'today': checkin.today(), 'items': checkin.for_date(date),
                    'words': [{'word': w, 'v': v} for w, v in checkin.WORDS]})


@app.route('/api/checkin', methods=['POST'])
def api_checkin_add():
    j = request.get_json(force=True)
    try:
        cid = checkin.add(j.get('date') or checkin.today(), j['word'], j.get('note', ''))
    except (ValueError, KeyError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'id': cid})


@app.route('/api/checkin/<int:cid>', methods=['PUT'])
def api_checkin_edit(cid):
    j = request.get_json(force=True)
    try:
        checkin.update(cid, j.get('word'), j.get('note'))
    except (ValueError, KeyError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True})


@app.route('/api/checkin/<int:cid>', methods=['DELETE'])
def api_checkin_del(cid):
    checkin.delete(cid)
    return jsonify({'ok': True})


@app.route('/checkin')
def checkin_page():
    """打卡单独一个全屏页：只干一件事，要简单。"""
    static = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    v = int(max(os.path.getmtime(os.path.join(static, f)) for f in ('checkin.js', 'checkin.css')))
    return render_template('checkin.html', v=v)


@app.route('/api/checkin/home')
def api_checkin_home():
    date = request.args.get('date') or checkin.today()
    return jsonify({'date': date, 'today': checkin.today(), 'items': checkin.for_date(date),
                    'words': [{'word': w, 'v': v} for w, v in checkin.WORDS],
                    'progress': checkin.progress(), 'orbs': checkin.orbs()})


@app.route('/api/checkin/<int:cid>/reply', methods=['POST'])
def api_checkin_reply(cid):
    import diary
    return jsonify({'reply': checkin.oracle(cid, diary._call_model)})


@app.route('/api/checkin/month')
def api_checkin_month():
    return jsonify(checkin.month_view(request.args.get('month') or checkin.today()[:7]))


@app.route('/api/stars')
def api_stars():
    month = request.args.get('month') or checkin.today()[:7]
    return jsonify({'month': month, 'stars': checkin.month_stars(month),
                    'stats': checkin.month_stats(month), 'note': checkin.month_note(month)})


@app.route('/api/stars/note', methods=['POST'])
def api_stars_note():
    import diary
    month = request.args.get('month') or checkin.today()[:7]
    return jsonify({'note': checkin.month_note(month, diary._call_model, refresh=request.args.get('refresh') == '1')})


@app.route('/api/river')
def api_river():
    import river
    return jsonify(river.series(start=request.args.get('from') or '2025-08-18'))


@app.route('/api/themes')
def api_themes():
    return jsonify({'themes': ledger.all_themes()})


# ---------------------------------------------------------------- 情绪实验（本地页面，不对外）

@app.route('/lab/mood')
def lab_mood_page():
    static = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    return render_template('lab_mood.html', v=int(os.path.getmtime(os.path.join(static, 'style.css'))))


@app.route('/api/lab/mood')
def api_lab_mood():
    import mood
    days = mood.lab_days()
    rated = {r['date']: r for r in q('SELECT * FROM mood_ratings')} if days else {}
    items = [{'date': d, 'ai': mood.ai_text(d), 'hand': mood.hand_text(d),
              'rated': rated.get(d, {}).get('valence'), 'note': rated.get(d, {}).get('note', '')}
             for d in days]
    done = bool(days) and all(i['rated'] is not None for i in items)
    return jsonify({'ready': bool(days), 'items': items, 'done': done,
                    'result': mood.lab_result() if done else None,
                    'overview': mood.overview() if done else None})


@app.route('/api/lab/mood/<date>', methods=['POST'])
def api_lab_rate(date):
    import mood
    j = request.get_json(force=True)
    mood.rate(date, j['valence'], j.get('note', ''))
    return jsonify({'ok': True})


# ---------------------------------------------------------------- 窗口管理 / 导入

@app.route('/api/conversations')
def api_conversations():
    """全部窗口：标题、来源、首末日期、活跃天数、条数、字数、来自哪次导入。"""
    rows = q("""SELECT c.id, c.source, c.title, c.title_fallback, c.n_msgs, c.chars, c.n_days,
                       c.first_import, c.last_import,
                       substr(c.first_ts,1,10) first_day, substr(c.last_ts,1,10) last_day,
                       (SELECT MAX(date) FROM messages m WHERE m.conv_id=c.id AND m.role='human') last_date,
                       (SELECT MIN(date) FROM messages m WHERE m.conv_id=c.id AND m.role='human') first_date,
                       (SELECT substr(text,1,80) FROM messages m WHERE m.conv_id=c.id AND m.role='human'
                         ORDER BY ts LIMIT 1) preview
                  FROM conversations c ORDER BY last_date DESC, c.n_msgs DESC""")
    by_src = {}
    for r in rows:
        by_src[r['source']] = by_src.get(r['source'], 0) + 1
    return jsonify({'conversations': rows, 'total': len(rows), 'by_source': by_src,
                    'multi_day': sum(1 for r in rows if (r['n_days'] or 0) > 1)})


@app.route('/api/imports')
def api_imports():
    return jsonify({'imports': q('SELECT * FROM imports ORDER BY id DESC LIMIT 100')})


@app.route('/api/import', methods=['POST'])
def api_import():
    """上传一个或多个导出文件，增量入库。重复的消息不会重复，新的加上；被改动的天日记作废。"""
    files = request.files.getlist('file')
    if not files:
        return jsonify({'error': '没有文件'}), 400
    os.makedirs(idx.IMPORT_DIR, exist_ok=True)
    results = []
    for f in files:
        name = f.filename or 'upload'
        safe = secure_filename(name) or 'upload.md'
        dst = os.path.join(idx.IMPORT_DIR, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe}")
        f.save(dst)
        try:
            with _lock:
                r = idx.ingest(DB, dst, label=name)
                idx.rebuild_fts(DB)
            r['ok'] = True
        except Exception as e:  # noqa: BLE001
            r = {'file': name, 'ok': False, 'error': str(e)}
        results.append(r)
    return jsonify({'results': results})


@app.route('/api/reflect')
def api_reflect():
    rk = request.args.get('range') or '1m'
    if rk not in reflect_chat.RANGES:
        rk = '1m'
    return jsonify(reflect_chat.build(DB, rk, refresh=request.args.get('refresh') == '1'))


# ------------------------------------------------------------------ 按窗口回顾
# 日记是 date-major（一天里所有窗口合成一篇），这里是 conv-major（一个窗口的时间轴）。
# 只有跨天的窗口才值得看：1657 个里 165 个跨天，其余 90% 一天聊完，日记已经覆盖。

@app.route('/api/windows')
def api_windows():
    """侧栏的窗口列表。默认只给跨天的，?all=1 给全部。"""
    conv_review.ensure_table()
    only_multi = request.args.get('all') != '1'
    rows = q("""SELECT c.id, c.source, c.title, c.title_fallback, c.n_msgs,
                       COUNT(DISTINCT m.date) days, SUM(m.chars) chars,
                       MIN(m.date) first_date, MAX(m.date) last_date,
                       (SELECT COUNT(*) FROM conv_notes n
                         WHERE n.conv_id = c.id AND n.date <> '') n_notes,
                       (SELECT substr(text,1,90) FROM messages x
                         WHERE x.conv_id = c.id AND x.role='human' ORDER BY x.ts LIMIT 1) preview
                  FROM conversations c JOIN messages m ON m.conv_id = c.id AND m.role='human'
                 GROUP BY c.id ORDER BY days DESC, chars DESC""")
    if only_multi:
        rows = [r for r in rows if (r['days'] or 0) > 1]
    return jsonify({'windows': rows, 'total': len(rows),
                    'reviewed': sum(1 for r in rows if r['n_notes'])})


@app.route('/api/window/<conv_id>')
def api_window(conv_id):
    w = conv_review.window(conv_id)
    if not w:
        return jsonify({'error': 'not found'}), 404
    n = conv_review.notes_for(conv_id)
    todo = set(conv_review.pending(conv_id, w))
    for d in w['days']:
        d['note'] = n['days'].get(d['date'])
        d['stale'] = d['date'] in todo and d['note'] is not None     # 有小结，但这天后来又聊了
        d['has_diary'] = bool(q('SELECT 1 FROM diaries WHERE date=?', d['date']))
    return jsonify({**w, 'arc': n['arc'], 'generated_at': n['at'], 'pending': len(todo),
                    'job': conv_review.job(conv_id)})


@app.route('/api/window/<conv_id>', methods=['POST'])
def api_window_gen(conv_id):
    if not conv_review.window(conv_id):
        return jsonify({'error': 'not found'}), 404
    conv_review.generate_bg(conv_id, refresh=request.args.get('refresh') == '1')
    return jsonify({'job': conv_review.job(conv_id)})


@app.route('/api/window/<conv_id>/day/<date>')
def api_window_day(conv_id, date):
    """这个窗口在这一天说了什么（展开时间轴上某一条时拉）。"""
    rows = q("""SELECT id, hhmm, role, text FROM messages
                 WHERE conv_id=? AND date=? ORDER BY ts""", conv_id, date)
    return jsonify({'date': date, 'messages': rows,
                    'human': sum(1 for r in rows if r['role'] == 'human')})


# ------------------------------------------------------------------ claude.ai 自动同步
# 不挂 cron/launchd —— 这个进程本来就随「启动服务.command」常驻，自己管就行。
# 每天 08:30 同步一次，拉完紧接着把缺的日记写掉 —— 早上打开就是全的。
# 那一刻 Mac 在睡觉就错过了：醒来（或应用启动）后发现今天的 08:30 还没跑过，马上补。
# 全量第一次要 25 分钟，之后每天只拉你动过的那几个对话，一两分钟。

SYNC_AT = (8, 30)
RETRY_MIN = 30           # 失败了隔多久再试

_csync = {'running': False, 'stage': None, 'done': 0, 'total': 0,
          'last': None, 'error': None}


def _csync_worker(force=False):
    if _csync['running']:
        return
    _csync.update(running=True, stage='起浏览器', done=0, total=0, error=None)

    def prog(stage, a, b):
        _csync.update(stage=stage, done=a, total=b)

    try:
        r = claude_sync.sync(force=force, progress=prog)
        _csync['last'] = {**r, 'at': datetime.now().isoformat(timespec='seconds')}
        if r.get('days'):
            print(f"[claude-sync] 新消息 {r.get('new_msgs', 0)}，涉及 {len(r['days'])} 天", flush=True)
    except Exception as e:                                   # noqa: BLE001
        _csync['error'] = f'{type(e).__name__}: {e}'[:300]
        print(f'[claude-sync] 失败：{_csync["error"]}', flush=True)
    finally:
        _csync.update(running=False, stage=None)


def _last_slot(now):
    """最近一个已经过去的 08:30。"""
    slot = now.replace(hour=SYNC_AT[0], minute=SYNC_AT[1], second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def _csync_due(now):
    slot = _last_slot(now)
    ok = claude_sync.last_ok_run()
    if ok and datetime.fromisoformat(ok['started_at']) >= slot:
        return False                                 # 这一轮已经成功过了
    last = claude_sync.last_run()
    if last and not last.get('ok') and last.get('started_at'):
        # 刚失败过：别每分钟都撞一次，隔 RETRY_MIN 再来
        if now - datetime.fromisoformat(last['started_at']) < timedelta(minutes=RETRY_MIN):
            return False
    return True


def _csync_scheduler():
    time.sleep(90)                       # 开机先让应用把自己的事做完
    while True:
        try:
            if not _csync['running'] and _csync_due(datetime.now()):
                _csync_worker()
                # 同步完就写日记。缺的都补，不只是今天碰过的 —— 顺手把漏网的也收了
                if claude_sync.last_run().get('ok') and not _batch['running']:
                    dates = _missing_dates()
                    if dates:
                        print(f'[claude-sync] 接着写日记：{len(dates)} 天', flush=True)
                        _batch_worker(dates)
                # 手写日记：只读 Notion，改过的才拉正文，平时几秒钟
                try:
                    jr = journal.sync(say=lambda m: None)
                    if jr['fetched'] or jr['removed']:
                        print(f"[journal] 拉了 {jr['fetched']} 页，删了 {jr['removed']} 页", flush=True)
                except Exception as e:                       # noqa: BLE001
                    print(f'[journal] 失败：{e}', flush=True)
                # 两个指标：新的聊天天打「卡得多狠」，新的手写天打「过得怎么样」
                try:
                    import diary as _d
                    n = mood.fill(_d._call_model, say=lambda m: None)
                    if any(n):
                        print(f'[mood] 卡得多狠 {n[0]} 天，过得怎么样 {n[1]} 天', flush=True)
                except Exception as e:                       # noqa: BLE001
                    print(f'[mood] 失败：{e}', flush=True)
        except Exception as e:                               # noqa: BLE001
            print(f'[claude-sync] 调度器出错：{e}', flush=True)
        time.sleep(60)


@app.route('/api/claude/sync')
def api_csync_status():
    last = claude_sync.last_run()
    return jsonify({'job': _csync, 'last_run': last,
                    'configured': bool(claude_sync.env('CLAUDE_SESSION_KEY')),
                    'at': '%02d:%02d' % SYNC_AT})


@app.route('/api/claude/sync', methods=['POST'])
def api_csync_start():
    if _csync['running']:
        return jsonify(_csync)
    threading.Thread(target=_csync_worker,
                     args=(request.args.get('force') == '1',), daemon=True).start()
    return jsonify({**_csync, 'running': True})


# ------------------------------------------------------------------ Notion 同步

_nsync = {'running': False, 'total': 0, 'done': 0, 'failed': 0,
          'current': None, 'action': None, 'errors': [], 'finished': None}


def _nsync_worker(only, fix):
    import to_notion as tn
    _nsync.update(running=True, total=0, done=0, failed=0,
                  current=None, action=None, errors=[], finished=None)

    def prog(i, total, date, action):
        _nsync.update(total=total, done=i, current=date, action=action)

    try:
        r = tn.fix_duplicates(progress=prog) if fix else tn.sync(only=only, progress=prog)
        _nsync['failed'] = len(r['failed'])
        _nsync['errors'] = r['failed'][:20]
    except Exception as e:                                    # noqa: BLE001
        _nsync['errors'] = [{'date': '-', 'error': str(e)}]
        _nsync['failed'] = 1
    finally:
        tn.invalidate_scan()          # 远端变了，下次 status 重新扫
        _nstat['data'] = None
        _nsync.update(running=False, current=None,
                      finished=datetime.now().isoformat(timespec='seconds'))


_nstat = {'data': None, 'computing': False}


def _nstat_worker():
    import to_notion as tn
    try:
        _nstat['data'] = tn.status()
    except Exception as e:                                    # noqa: BLE001
        _nstat['data'] = {'connected': False, 'error': str(e)}
    finally:
        _nstat['computing'] = False


@app.route('/api/notion/status')
def api_notion_status():
    """扫一遍远端要 90s+（1200 页 / 12 次分页），不能让 HTTP 干等 ——
    首次返回「算着呢」，后台线程算完，前端轮询下一拍就拿到。"""
    fresh = request.args.get('refresh') == '1'
    if fresh or (_nstat['data'] is None and not _nstat['computing']):
        if fresh:
            import to_notion as tn
            tn.invalidate_scan()
        _nstat['computing'] = True
        threading.Thread(target=_nstat_worker, daemon=True).start()
    if _nstat['data'] is None:
        return jsonify({'computing': True, 'job': _nsync})
    return jsonify({**_nstat['data'], 'computing': _nstat['computing'], 'job': _nsync})


@app.route('/api/notion/sync', methods=['POST'])
def api_notion_sync():
    if _nsync['running']:
        return jsonify(_nsync)
    only = (request.args.get('only') or '').split(',') if request.args.get('only') else None
    fix = request.args.get('fix') == '1'
    threading.Thread(target=_nsync_worker, args=(only, fix), daemon=True).start()
    return jsonify({**_nsync, 'running': True})


@app.route('/api/notion/sync')
def api_notion_job():
    return jsonify(_nsync)


if __name__ == '__main__':
    port = int(os.environ.get('PORT') or 5055)
    threading.Timer(3.0, reflect_chat.warm, args=(DB,)).start()   # 提前算好，打开面板不用等
    threading.Thread(target=_csync_scheduler, daemon=True).start()
    print(f'Ephemeris -> http://localhost:{port}')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
