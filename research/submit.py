"""参与者交数据之前先看一遍：本机开一个页面，把要交出去的东西原样摆出来，能拿掉窗口、删证据、退出，确认了才生成提交包。

    ../venv/bin/python -m research.submit P01            开 http://127.0.0.1:5066 给参与者 P01 看
    ../venv/bin/python -m research.submit P01 --package  不开页面，直接按现在的决定生成提交包

提交包在 data/submit/<pid>/：chat_features.csv、data_clean.csv、manifest.json（做法 A 还有 evidence.*）+ receipt.json
（每个文件的 sha256、生成时间、删了几个窗口 / 几条证据）。研究者拿到的就是这个文件夹，交出去这一步（上传 / 发邮件）在外面。
参与者的决定只存在他这边（research.consent）；改了决定，之前的确认作废，要重新确认。
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

import research
from research import consent, export

PORT = 5066
SUBMIT = os.path.join(research.DATA, 'submit')
STUDY = os.path.join(research.HERE, 'study.demo.json')


def folder(pid):
    return os.path.join(SUBMIT, pid)


def preview(pid, study):
    """要交出去的东西（按现在的决定算）：每期的数 + 会交出去的原话证据。"""
    rows, evidence, _ = export.build(study, [pid])
    quotes = []
    for k, by_measure in (evidence.get(pid) or {}).items():
        for name, items in by_measure.items():
            quotes += [{'period': k, 'measure': name, 'msg': e['msg'], 'conv': e['conv'], 'at': e['at'][:16],
                        'role': e['role'], 'text': e['text'], 'why': e['why']} for e in items if e.get('msg')]
    cols = ['period_number', 'survey_date', 'active_days'] + study['measures']
    return [{c: r.get(c) for c in cols} for r in rows], quotes


def windows(pid):
    """这个人的全部窗口（含拿掉的），给页面列出来勾。"""
    db = sqlite3.connect(f'file:{research.db_path(pid)}?mode=ro', uri=True)
    out = [{'id': c, 'title': t, 'source': s, 'first': a[:10], 'last': b[:10], 'n': n}
           for c, t, s, a, b, n in db.execute(
               'SELECT m.conv_id, COALESCE(v.title, ""), m.source, MIN(m.ts), MAX(m.ts), COUNT(*) FROM messages m '
               'LEFT JOIN conversations v ON v.id = m.conv_id GROUP BY m.conv_id ORDER BY MIN(m.ts)')]
    db.close()
    return out


def package(pid, study_path=STUDY):
    """按现在的决定生成提交包，记下回执。退出了的人不生成，旧的包删掉。"""
    import shutil
    out = folder(pid)
    if os.path.isdir(out):
        shutil.rmtree(out)
    if consent.get(pid)['withdrawn']:
        return None
    study = research.study(study_path)
    rows, evidence, cov = export.build(study, [pid])
    export.write(study, study_path, rows, evidence, cov, out=out, pids=[pid])
    files = {f: hashlib.sha256(open(os.path.join(out, f), 'rb').read()).hexdigest() for f in sorted(os.listdir(out))}
    receipt = {'pid': pid, 'study': study['study'], 'made_at': datetime.now().isoformat(timespec='seconds'),
               'privacy': study.get('privacy'), 'files': files, **consent.counts(pid)}
    receipt['submitted'] = True
    with open(os.path.join(out, 'receipt.json'), 'w', encoding='utf-8') as f:
        json.dump(receipt, f, ensure_ascii=False, indent=1)
    consent.submitted(pid, receipt)
    return receipt


def serve(pid, study_path=STUDY):
    from flask import Flask, abort, jsonify, request, send_file
    study = research.study(study_path)
    if pid not in study['participants']:
        sys.exit(f'{pid} 不在研究方案 {study["study"]} 里')
    app = Flask(__name__)

    @app.before_request
    def local_only():
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(403)
        if request.method == 'POST' and request.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
            abort(403)

    @app.get('/')
    def page():
        return send_file(os.path.join(os.path.dirname(__file__), 'review.html'))

    @app.get('/api/review')
    def review():
        mine = consent.get(pid)
        rows, quotes = ([], []) if mine['withdrawn'] else preview(pid, study)
        hidden = set(mine['hidden_evidence'])
        ws = windows(pid)
        excluded = set(mine['excluded_convs'])
        return jsonify(pid=pid, study=study['study'], privacy=study.get('privacy'), measures=study['measures'],
                       models=study.get('models', []), rows=rows, quotes=quotes, hidden=len(hidden),
                       windows=[dict(w, excluded=w['id'] in excluded) for w in ws],
                       other=mine['other_sources'], withdrawn=mine['withdrawn'], submitted=mine['submitted'],
                       folder=folder(pid))

    @app.post('/api/decide')
    def decide():
        d = request.get_json() or {}
        what, item, on = d.get('what'), d.get('id'), bool(d.get('on', True))
        if consent.get(pid)['withdrawn']:
            abort(409)
        if what == 'window':
            consent.exclude(pid, item, on)
        elif what == 'quote':
            consent.hide(pid, item, on)
        elif what == 'other':
            consent.other_sources(pid, d.get('names') or [])
        elif what == 'unhide_all':
            for m in consent.get(pid)['hidden_evidence']:
                consent.hide(pid, m, False)
        else:
            abort(400)
        return jsonify(ok=True)

    @app.post('/api/submit')
    def submit():
        return jsonify(receipt=package(pid, study_path))

    @app.post('/api/withdraw')
    def withdraw():
        consent.withdraw(pid)
        package(pid, study_path)                        # 删掉已经生成的包
        return jsonify(ok=True)

    print(f'参与者 {pid} 的提交前审阅：http://127.0.0.1:{PORT}')
    app.run('127.0.0.1', PORT)


if __name__ == '__main__':
    if '--package' in sys.argv:
        print(json.dumps(package(sys.argv[1]), ensure_ascii=False, indent=1))
    else:
        serve(sys.argv[1])
