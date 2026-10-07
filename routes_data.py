"""数据进出：「窗口与导入」面板（窗口一览、导入、claude.ai 同步、备份）和 Notion 面板。"""
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

import backup
import daily
import export
import index as idx
import store
import to_notion

bp = Blueprint('data', __name__)


@bp.route('/api/conversations')
def api_conversations():
    """全部窗口：标题、来源、首末日期、活跃天数、条数、字数、来自哪次导入。"""
    rows = store.q("""SELECT c.id, c.source, c.title, c.title_fallback, c.n_msgs, c.chars, c.n_days,
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
    # 每个来源最新的一条我的消息是哪天：备份新到哪天就看它（Gemini 没有自动同步，要靠 Takeout）
    latest = {r['source']: r['d'] for r in store.q("SELECT source, MAX(date) d FROM messages WHERE role='human' GROUP BY source")}
    return jsonify({'conversations': rows, 'total': len(rows), 'by_source': by_src, 'latest': latest,
                    'multi_day': sum(1 for r in rows if (r['n_days'] or 0) > 1)})


@bp.route('/api/imports')
def api_imports():
    return jsonify({'imports': store.q('SELECT * FROM imports ORDER BY id DESC LIMIT 100')})


@bp.route('/api/import', methods=['POST'])
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
            with store.LOCK:                 # 整个导入期间别人别插进来写
                r = idx.ingest(store.connect(), dst, label=name)
                idx.rebuild_fts(store.connect())
            r['ok'] = True
        except Exception as e:  # noqa: BLE001
            r = {'file': name, 'ok': False, 'error': str(e)}
        results.append(r)
    return jsonify({'results': results})


# ------------------------------------------------------------------ claude.ai 同步（早上那一串，见 daily.py）

@bp.route('/api/claude/sync')
def api_csync_status():
    return jsonify(daily.status())


@bp.route('/api/claude/sync', methods=['POST'])
def api_csync_start():
    """立刻跑一遍早上那一串：拉 claude.ai → 写缺的日记 → 整理。"""
    return jsonify(daily.start(force=request.args.get('force') == '1'))


# ------------------------------------------------------------------ 备份

@bp.route('/api/backup')
def api_backup():
    return jsonify(backup.status())


@bp.route('/api/backup', methods=['POST'])
def api_backup_run():
    backup.run_bg()
    return jsonify(backup.status())


# ------------------------------------------------------------------ 导出（export.py）

@bp.route('/api/export')
def api_export():
    return jsonify(export.status())


@bp.route('/api/export/<kind>', methods=['POST'])
def api_export_run(kind):
    if kind not in ('archive', 'handoff'):
        return jsonify({'error': '没有这种导出'}), 404
    skip = [k for k in re.split(r'[,，、\s]+', (request.get_json(silent=True) or {}).get('skip') or '') if k]
    return jsonify(export.start(kind, skip))


@bp.route('/api/export/<kind>/open', methods=['POST'])
def api_export_open(kind):
    return jsonify({'ok': export.reveal(kind)})


# ------------------------------------------------------------------ Notion（to_notion.py）

@bp.route('/api/notion/status')
def api_notion_status():
    return jsonify(to_notion.panel(refresh=request.args.get('refresh') == '1'))


@bp.route('/api/notion/sync', methods=['POST'])
def api_notion_sync():
    only = (request.args.get('only') or '').split(',') if request.args.get('only') else None
    return jsonify(to_notion.start_sync(only, fix=request.args.get('fix') == '1'))


@bp.route('/api/notion/sync')
def api_notion_job():
    return jsonify(to_notion.job())
