"""回顾：一段时间在聊什么（回顾面板、话题河流、主题账本），和按窗口回顾。"""
from flask import Blueprint, jsonify, render_template, request

import cards
import conv_review
import index as idx
import ledger
import llm
import qa
import reflect_chat
import river
import store

bp = Blueprint('review', __name__)


@bp.route('/api/reflect')
def api_reflect():
    rk = request.args.get('range') or '1m'
    if rk not in reflect_chat.RANGES:
        rk = '1m'
    return jsonify(reflect_chat.build(rk, refresh=request.args.get('refresh') == '1'))


@bp.route('/api/river')
def api_river():
    return jsonify(river.series(start=request.args.get('from') or '2025-08-18'))


@bp.route('/api/themes')
def api_themes():
    return jsonify({'themes': ledger.all_themes()})


# ------------------------------------------------------------------ 按窗口回顾
# 日记是 date-major（一天里所有窗口合成一篇），这里是 conv-major（一个窗口的时间轴）。
# 只有跨天的窗口才值得看：1657 个里 165 个跨天，其余 90% 一天聊完，日记已经覆盖。

@bp.route('/api/windows')
def api_windows():
    """侧栏的窗口列表。默认只给跨天的，?all=1 给全部。"""
    only_multi = request.args.get('all') != '1'
    rows = store.q("""SELECT c.id, c.source, c.title, c.title_fallback, c.n_msgs,
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


@bp.route('/api/window/<conv_id>')
def api_window(conv_id):
    w = conv_review.window(conv_id)
    if not w:
        return jsonify({'error': 'not found'}), 404
    n = conv_review.notes_for(conv_id)
    todo = set(conv_review.pending(conv_id, w))
    for d in w['days']:
        d['note'] = n['days'].get(d['date'])
        d['stale'] = d['date'] in todo and d['note'] is not None     # 有小结，但这天后来又聊了
        d['has_diary'] = bool(store.one('SELECT 1 FROM diaries WHERE date=?', d['date']))
    # 标价：现在点「生成 / 现在就补」、和「全部重写」各要调几次模型、多少钱
    price = {k: {'calls': c, 'cost': round(c * conv_review.COST_PER_CALL, 3)}
             for k, c in (('now', conv_review.calls(conv_id, w)), ('all', conv_review.calls(conv_id, w, refresh=True)))}
    return jsonify({**w, 'arc': n['arc'], 'generated_at': n['at'], 'pending': len(todo), 'price': price,
                    'job': conv_review.job(conv_id)})


@bp.route('/api/window/<conv_id>', methods=['POST'])
def api_window_gen(conv_id):
    if not conv_review.window(conv_id):
        return jsonify({'error': 'not found'}), 404
    conv_review.generate_bg(conv_id, refresh=request.args.get('refresh') == '1')
    return jsonify({'job': conv_review.job(conv_id)})


@bp.route('/api/window/<conv_id>/day/<date>')
def api_window_day(conv_id, date):
    """这个窗口在这一天说了什么（展开时间轴上某一条时拉）。"""
    rows = store.q("""SELECT id, hhmm, role, text FROM messages
                 WHERE conv_id=? AND date=? ORDER BY ts""", conv_id, date)
    return jsonify({'date': date, 'messages': rows,
                    'human': sum(1 for r in rows if r['role'] == 'human')})


# ---------------------------------------------------------------- 问（qa.py）+ 证据卡（cards.py）

@bp.route('/api/qa')
def api_qa():
    """「问」那一栏：证据卡做到哪了（还差几天、估价、正在做的进度）+ 对话记录。"""
    thread = request.args.get('thread', type=int) or qa.current()
    return jsonify({'cards': cards.status(), 'history': qa.history(thread=thread), 'live': qa.live(),
                    'thread': thread, 'threads': qa.threads()})


@bp.route('/api/qa/live')
def api_qa_live():
    """正在问的那一个：查找进度、写到哪了、写完的结果（前端 / ask.py 每半秒多看一眼，轻量）。"""
    return jsonify(qa.live())


@bp.route('/api/qa/ask', methods=['POST'])
def api_qa_ask():
    body = request.get_json(silent=True) or {}
    q = (body.get('question') or '').strip()
    if not q:
        return jsonify({'error': '问题是空的'}), 400
    if body.get('deep') and not llm.claude_on():
        return jsonify({'error': 'Claude 现在用不了（没装 / 没登录 / 额度用完停着），先用普通的「问」'}), 409
    if body.get('wait'):                               # 老的同步用法：问完才返回
        try:
            return jsonify(qa.answer(q))
        except Exception as e:                           # noqa: BLE001
            return jsonify({'error': f'{type(e).__name__}: {e}'[:300]}), 500
    # 后台问、边写边看：进度在 /api/qa/live
    return jsonify({'started': qa.start_ask(q, bool(body.get('deep')), body.get('thread')), 'live': qa.live()})


@bp.route('/api/qa/stop', methods=['POST'])
def api_qa_stop():
    """停下正在写的回答（聊天页的停止按钮）：写出来的那段留在对话里。"""
    return jsonify({'stopped': qa.stop()})


@bp.route('/chat')
@bp.route('/chat/<int:thread>')
def chat_page(thread=None):
    """「问」单独一个全屏聊天页（2026-10-13：独立网址，像 claude.ai）。一段对话一个网址：/chat/<id>。"""
    return render_template('chat.html', thread=thread or '', name=idx.who())


@bp.route('/api/qa/new', methods=['POST'])
@bp.route('/api/qa/clear', methods=['POST'])           # 老名字：以前是清空，现在是开一段新的（以前的留着）
def api_qa_new():
    return jsonify({'ok': True, 'thread': qa.new_thread()})


@bp.route('/api/qa/cards', methods=['POST'])
def api_qa_cards():
    """做卡：把还差的窗口都做了（新窗口整个做，老窗口只做后来的消息）。花钱的，前端先问过再调。"""
    return jsonify(cards.start(cards.due()))


@bp.route('/api/qa/cards/stop', methods=['POST'])
def api_qa_cards_stop():
    cards.stop()
    return jsonify(cards.status())
