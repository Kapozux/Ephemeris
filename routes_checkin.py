"""打卡页（/checkin，单独一个全屏页）：点心情、补前几天、星座图。"""
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
    month = request.args.get('month') or days.today()[:7]
    return jsonify({'note': checkin.month_note(month, llm.ask, refresh=request.args.get('refresh') == '1')})
