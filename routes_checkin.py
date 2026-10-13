"""打卡页（/checkin，单独一个全屏页）：点心情、补前几天、星座图。"""
import re

from flask import Blueprint, jsonify, render_template, request

import checkin
import days
import llm

bp = Blueprint('checkin', __name__)


@bp.route('/checkin')
def checkin_page():
    """打卡单独一个全屏页：只干一件事，要简单。"""
    return render_template('checkin.html')


def _words():
    return [{'word': w, 'v': v} for w, v in checkin.WORDS]


@bp.route('/api/checkin')
def api_checkin_get():
    date = request.args.get('date') or days.today()
    return jsonify({'date': date, 'today': days.today(), 'items': checkin.for_date(date), 'words': _words()})


@bp.route('/api/checkin/home')
def api_checkin_home():
    date = request.args.get('date') or days.today()
    return jsonify({'date': date, 'today': days.today(), 'items': checkin.for_date(date), 'words': _words(),
                    'progress': checkin.progress(), 'orbs': checkin.orbs()})


@bp.route('/api/checkin', methods=['POST'])
def api_checkin_add():
    j = request.get_json(force=True)
    try:
        cid = checkin.add(j.get('date') or days.today(), j['word'], j.get('note', ''))
    except (ValueError, KeyError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'id': cid})


@bp.route('/api/checkin/<int:cid>', methods=['PUT'])
def api_checkin_edit(cid):
    j = request.get_json(force=True)
    try:
        checkin.update(cid, j.get('word'), j.get('note'))
    except (ValueError, KeyError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True})


@bp.route('/api/checkin/<int:cid>', methods=['DELETE'])
def api_checkin_del(cid):
    checkin.delete(cid)
    return jsonify({'ok': True})


@bp.route('/api/checkin/<int:cid>/reply', methods=['POST'])
def api_checkin_reply(cid):
    return jsonify({'reply': checkin.oracle(cid, llm.ask)})


# ---------------------------------------------------------------- 这个月（星座图）

@bp.route('/api/checkin/month')
def api_checkin_month():
    return jsonify(checkin.month_view(request.args.get('month') or days.today()[:7]))


@bp.route('/api/stars')
def api_stars():
    month = request.args.get('month') or days.today()[:7]
    return jsonify({'month': month, 'stars': checkin.month_stars(month),
                    'stats': checkin.month_stats(month), 'note': checkin.month_note(month)})


@bp.route('/api/stars/note', methods=['POST'])
def api_stars_note():
    """用户点了「写一句这个月」才来：打开月视图只读缓存（month_view），从不自动调模型。
    点了就是手动（规矩 5 的手动按钮）：强制写；写不出来带上原因，按钮那边好说。"""
    month = request.args.get('month') or days.today()[:7]
    if not checkin.month_stats(month):
        return jsonify({'note': '', 'error': '这个月还没有打卡，也没有手写日记读出来的心情'})
    failed = llm.status()['failures']
    note = checkin.month_note(month, llm.ask, refresh=True)
    if note:
        return jsonify({'note': note})
    why = llm.status()['last_error'] if llm.status()['failures'] > failed else ''
    return jsonify({'note': '', 'error': re.sub(r'^\w+(Error|Exception): ', '', why or '') or '模型回了空'})
