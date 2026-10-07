"""主页：日历、某一天、搜索、闪回卡，以及写日记（单篇重写、补全缺的）、手写日记（图片、从 Notion 刷新）。"""
from flask import Blueprint, jsonify, render_template, request, send_from_directory

import checkin
import diary
import flashback
import journal
import ledger
import mood
import search
import store

bp = Blueprint('day', __name__)


@bp.route('/')
def home():
    return render_template('index.html')


# ---------------------------------------------------------------- 日历

@bp.route('/api/days')
def api_days():
    rows = store.q("""SELECT m.date,
                       COUNT(DISTINCT m.conv_id) threads,
                       COUNT(*) msgs, SUM(m.chars) chars,
                       MIN(m.hhmm) first_at, MAX(m.hhmm) last_at
                  FROM messages m WHERE m.role = 'human'
                 GROUP BY m.date ORDER BY m.date""")
    diaries = {r['date']: r for r in store.q('SELECT date, headline, mood FROM diaries')}
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
    missing = [r for r in rows if not r['has_diary'] and (r['chars'] or 0) >= diary.MIN_CHARS]
    return jsonify({'days': rows, 'total_days': len(rows),
                    'missing': len(missing),
                    'cost_estimate': round(len(missing) * diary.COST_PER_DAY, 2),
                    # 打开应用落在哪一天：最近一篇写好的日记（早上 08:30 刚写出来的昨天那篇）。
                    # 不用「最近有数据的一天」—— 那常常是今天，只有一两条打卡，页面是空的。
                    'latest': next((r['date'] for r in reversed(rows) if r['has_diary']),
                                   rows[-1]['date'] if rows else None)})


# ---------------------------------------------------------------- 一天

@bp.route('/api/day/<date>')
def api_day(date):
    """某一天：日记 + 按窗口分组的全部消息（含 AI 回复，前端决定显不显示）。"""
    msgs = store.q("""SELECT m.id, m.conv_id, m.source, m.hhmm, m.role, m.text, m.chars,
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

    nav = store.one("""SELECT
                 (SELECT MAX(d) FROM (SELECT date d FROM messages WHERE role='human' AND date < ?
                                      UNION SELECT date FROM journal WHERE chars > 0 AND date < ?
                                      UNION SELECT date FROM checkins WHERE date < ?)) prev,
                 (SELECT MIN(d) FROM (SELECT date d FROM messages WHERE role='human' AND date > ?
                                      UNION SELECT date FROM journal WHERE chars > 0 AND date > ?
                                      UNION SELECT date FROM checkins WHERE date > ?)) next""",
                    date, date, date, date, date, date)
    return jsonify({'date': date, 'threads': out,
                    'diary': diary.get(date),
                    'themes': ledger.chips(date),
                    'journal': journal.for_date(date),
                    'signals': mood.day_signals(date),
                    'generating': diary.writing(date),
                    'prev': nav['prev'], 'next': nav['next']})


@bp.route('/api/conversation/<conv_id>')
def api_conv(conv_id):
    """一个窗口横跨了哪些天 —— 这是整个产品和「回 Claude 翻记录」的区别。"""
    c = store.one('SELECT * FROM conversations WHERE id = ?', conv_id)
    if not c:
        return jsonify({'error': 'not found'}), 404
    days = store.q("""SELECT date, COUNT(*) msgs, SUM(chars) chars
                  FROM messages WHERE conv_id = ? AND role='human'
                 GROUP BY date ORDER BY date""", conv_id)
    return jsonify({'conversation': c, 'days': days})


# ---------------------------------------------------------------- 搜索 / 闪回

@bp.route('/api/search')
def api_search():
    kw = (request.args.get('q') or '').strip()
    if len(kw) < 2:
        return jsonify({'hits': []})
    hits = search.messages(kw)
    # 手写日记单独一路，排在前面：自己写的比聊天里的更值得先看
    jh = [{'date': h['date'], 'kind': 'journal', 'page_id': h['page_id'], 'snip': h['snip'],
           'conv_title': '手写日记', 'hhmm': ''} for h in search.journal(kw)]
    return jsonify({'hits': jh + hits, 'q': kw})


# ---------------------------------------------------------------- 手写日记（journal.py）

@bp.route('/journal-files/<path:name>')
def journal_file(name):
    """手写日记里的图片（同步时从 Notion 下载到本地的）。"""
    return send_from_directory(journal.FILES_DIR, name)


@bp.route('/api/journal/refresh', methods=['POST'])
def api_journal_refresh():
    """打开 / 切回页面时前端悄悄调一下：Notion 里最近改过的几页有新的就拉下来。不调模型，两分钟内最多一次。"""
    try:
        return jsonify(journal.refresh_recent())
    except Exception as e:                                   # noqa: BLE001  连不上 Notion 就算了，早上那次还会全量对
        return jsonify({'changed': [], 'error': f'{type(e).__name__}: {e}'[:200]})


@bp.route('/api/flashback')
def api_flashback():
    return jsonify(flashback.get(request.args.get('mode') or 'random'))


# ---------------------------------------------------------------- 写日记（diary.py）

@bp.route('/api/diary/<date>', methods=['POST'])
def api_diary_make(date):
    diary.start_one(date)
    return jsonify({'generating': True})


@bp.route('/api/diary/batch', methods=['POST'])
def api_batch_start():
    """补全日记。顺序跑（限流友好），前端轮询进度。

    默认只做还没有日记的天；?force=1 连已有的也重写（改了 prompt 或前情逻辑之后用）。
    """
    return jsonify(diary.start_batch(force=request.args.get('force') == '1'))


@bp.route('/api/diary/batch', methods=['DELETE'])
def api_batch_stop():
    return jsonify(diary.stop_batch())


@bp.route('/api/diary/batch')
def api_batch_status():
    return jsonify(diary.batch_status())
