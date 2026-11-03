"""数据进出：「窗口与导入」面板（窗口一览、搜索、导入、claude.ai 同步、备份）、「设置 → 导出」和 Notion 面板。"""
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import backup
from sources import claude_sync
import daily
import export
import hidden
import index as idx
import longchats
import memory
import search
import setup
import store
from sources import to_notion

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


@bp.route('/api/conversations/search')
def api_conversations_search():
    """「窗口」栏的搜索框：标题和全文（我和 AI 说的都算）。"""
    kw = (request.args.get('q') or '').strip()
    return jsonify({'q': kw, 'hits': search.conversations(kw) if kw else {}})


@bp.route('/tools/export_gemini.js')
def tool_export_gemini():
    """「窗口与导入」里「复制导出脚本」拿的就是这个文件（在 gemini.google.com 的控制台里跑）。"""
    return send_from_directory(os.path.join(idx.HERE, 'scripts'), 'export_gemini.js', mimetype='text/javascript')


@bp.route('/tools/export_chatgpt.js')
def tool_export_chatgpt():
    """「数据来源 → 导入」里 ChatGPT 那行的「复制导出脚本」（在 chatgpt.com 的控制台里跑，2026-10-14）。"""
    return send_from_directory(os.path.join(idx.HERE, 'scripts'), 'export_chatgpt.js', mimetype='text/javascript')


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


@bp.route('/api/claude/extra')
def api_cextra():
    """列表里看不到、按 id 拉的 claude.ai 窗口（无痕窗口）。"""
    return jsonify({'items': claude_sync.extras()})


@bp.route('/api/claude/extra', methods=['POST'])
def api_cextra_set():
    """body：{text: 贴进来的链接（可以几个）} 加；{remove: id} 不再拉。"""
    body = request.get_json(silent=True) or {}
    try:
        if body.get('remove'):
            claude_sync.remove_extra(body['remove'])
        else:
            claude_sync.add_extra(body.get('text'))
    except ValueError as e:
        return jsonify({'error': str(e), 'items': claude_sync.extras()}), 400
    return jsonify({'items': claude_sync.extras()})


# ------------------------------------------------------------------ 备份

@bp.route('/api/backup')
def api_backup():
    return jsonify(backup.status())


@bp.route('/api/backup', methods=['POST'])
def api_backup_run():
    backup.run_bg()
    return jsonify(backup.status())


# ------------------------------------------------------------------ 导出（export.py）

def _skip(raw):
    return [k for k in re.split(r'[,，、\s]+', raw or '') if k]


@bp.route('/api/export')
def api_export():
    """两种导出的状态，外加交接包按现在填的「跳过」估多少钱。"""
    return jsonify(export.status(_skip(request.args.get('skip'))))


@bp.route('/api/export/<kind>', methods=['POST'])
def api_export_run(kind):
    if kind not in export.KINDS:
        return jsonify({'error': '没有这种导出'}), 404
    return jsonify(export.start(kind, _skip((request.get_json(silent=True) or {}).get('skip'))))


@bp.route('/api/export/<kind>/open', methods=['POST'])
def api_export_open(kind):
    return jsonify({'ok': export.reveal(kind)})


# ------------------------------------------------------------------ 长聊清单（longchats.py）：换账号时要带走哪些

@bp.route('/api/longchats')
def api_longchats():
    return jsonify({'lines': longchats.lines()})


@bp.route('/api/longchats/<conv_id>', methods=['POST'])
def api_longchats_mark(conv_id):
    """body：{move: true / false / null} 标要迁 / 不迁 / 取消；{after: 窗口 id / null} 是接着哪个开的续集 / 拆开。"""
    body = request.get_json(silent=True) or {}
    try:
        if 'move' in body:
            longchats.mark(conv_id, body['move'])
        if 'after' in body:
            longchats.follow(conv_id, body['after'])
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'lines': longchats.lines()})


# ------------------------------------------------------------------ 记忆（memory.py）：「问」先看的档案

def _memory():
    return {'topics': memory.topics(all=True), 'job': memory.status()}


@bp.route('/api/memory')
def api_memory():
    return jsonify(_memory())


@bp.route('/api/memory/run', methods=['POST'])
def api_memory_run():
    """body：{name?}。手动按的一律重写（这周写过的也写）；name 只整理那一页。"""
    body = request.get_json(silent=True) or {}
    memory.start(force=True, name=body.get('name') or None)
    return jsonify(_memory())


@bp.route('/api/memory/edit', methods=['POST'])
def api_memory_edit():
    """body：{name, on} 某页让不让「问」看；{name, text} 自己补一条；{strike: 事实 id} 划掉一条（折进「以前的」）。"""
    body = request.get_json(silent=True) or {}
    try:
        if 'strike' in body:
            memory.strike(int(body['strike']))
        elif 'text' in body:
            memory.add(body.get('name'), body['text'])
        elif 'on' in body:
            memory.switch(body.get('name'), bool(body['on']))
    except (ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_memory())


# ------------------------------------------------------------------ 屏蔽（hidden.py）：「问」一律查不到的日子 / 窗口 / 手写日记

@bp.route('/api/hidden')
def api_hidden():
    return jsonify({'items': hidden.items()})


@bp.route('/api/hidden', methods=['POST'])
def api_hidden_set():
    """body：{kind: day | conv | journal, ref, on}。"""
    body = request.get_json(silent=True) or {}
    try:
        hidden.set_(body.get('kind'), body.get('ref'), bool(body.get('on', True)))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'items': hidden.items()})


# ------------------------------------------------------------------ 第一次用（setup.py）

@bp.route('/api/setup')
def api_setup():
    return jsonify(setup.status())


@bp.route('/api/setup', methods=['POST'])
def api_setup_save():
    """「开始用」页填的 key 写进 .env。只回填了没有，不回值。"""
    return jsonify(setup.save(request.get_json(silent=True) or {}))


@bp.route('/api/models/<name>')
def api_models(name):
    """设置「模型」栏：这家 /models 有哪些模型，每个带模型资料（能不能调工具、价格；不认识的是 null）。"""
    import providers
    if name not in providers.PRESETS:
        return jsonify({'error': '不认识这家'}), 404
    try:
        ids = providers.models(name)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502
    return jsonify({'models': [{'id': m, 'info': providers.info(m)} for m in ids]})


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
