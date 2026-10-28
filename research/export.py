"""算指标、和量表对齐、导出。

    ../venv/bin/python -m research.export [研究方案.json]

写到 data/out/：
    data_clean.csv   一行 = 一个参与者的一期，列名照 Merve 代码的输入（within-person-cesd-screenome 的
                     data/raw/data_clean.csv）：pid, period_number, survey_date, period_start, period_end, cesd_score,
                     days_in_period, active_days, active_day_ratio + tz + 研究方案里的 chat_* 指标。
                     她的特征列表是写死的（modeling_table / delta_table 的 behavioral_cols、prepare_features 的
                     CONTINUOUS_*）：不改的话 chat_* 会被静默丢掉，要用得在这 3 个文件 4 张列表里加上
    chat_features.csv 要并进她自己的表用这份：只有 pid, period_number, survey_date + 全带 chat_ 前缀的列
                     （chat_active_days 等），不会跟她截图算的 active_days 撞名
    evidence.json    每个数背后的原话（做法 A 才写：做法 B 原话不出本机）
    manifest.json    研究方案的指纹、代码版本、指标说明——拿结果的人能复现
"""
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import research
from research import measures, participant
from research import personas as P

OUT = os.path.join(research.DATA, 'out')
BASE = ['pid', 'period_number', 'survey_date', 'period_start', 'period_end', 'tz', 'cesd_score',
        'days_in_period', 'active_days', 'active_day_ratio']
LOCAL = '%Y-%m-%dT%H:%M:%S'   # 不带偏移的当地时间 + tz 列：跨夏令时的人偏移会混着 -08/-07，pandas 新版 to_datetime 会报错


def tz_of(pid):
    """演示版参与者的时区来自 personas；真研究里改成读参与者表。"""
    return P.by_pid(pid)['tz']


def build(study):
    rows, evidence = [], {}
    for pid in study['participants']:
        person = participant.load(pid, tz_of(pid), study.get('sources'))
        results = measures.run(person, study)
        msgs = {m.id: m for ms in person.convs.values() for m in ms}
        evidence[pid] = {}
        for p in person.periods:
            active = len(p.active_days())
            row = {'pid': pid, 'period_number': p.number, 'survey_date': p.end.strftime(LOCAL),
                   'period_start': p.start.strftime(LOCAL), 'period_end': p.end.strftime(LOCAL), 'tz': person.tz,
                   'cesd_score': p.score,
                   'days_in_period': p.days, 'active_days': active, 'active_day_ratio': active / p.days}
            evidence[pid][p.number] = {}
            for name, by_period in results.items():
                value, ev = by_period.get(p.number, (None, []))
                row[name] = value
                evidence[pid][p.number][name] = [_quote(e, msgs) for e in ev]
            rows.append(row)
    return rows, evidence


def _quote(e, msgs):
    m = msgs.get(e['msg'])
    if not m:
        return {'why': e['why']}
    return {'why': e['why'], 'msg': m.id, 'conv': m.conv, 'source': m.source, 'role': m.role,
            'at': m.at.isoformat(), 'text': m.text}


def _cell(v):
    return '' if v is None else round(v, 6) if isinstance(v, float) else v


def _git():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True,
                              cwd=os.path.dirname(research.HERE)).stdout.strip()
    except OSError:
        return ''


def write(study, study_path, rows, evidence):
    os.makedirs(OUT, exist_ok=True)
    cols = BASE + study['measures']
    with open(os.path.join(OUT, 'data_clean.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r[c]) for c in cols})
    keys = ['pid', 'period_number', 'survey_date', 'tz']
    merge = keys + ['chat_active_days', 'chat_active_day_ratio'] + study['measures']
    with open(os.path.join(OUT, 'chat_features.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, merge)
        w.writeheader()
        for r in rows:
            r = dict(r, chat_active_days=r['active_days'], chat_active_day_ratio=r['active_day_ratio'])
            w.writerow({c: _cell(r[c]) for c in merge})
    if study.get('privacy') == 'A':
        with open(os.path.join(OUT, 'evidence.json'), 'w', encoding='utf-8') as f:
            json.dump(evidence, f, ensure_ascii=False, indent=1)
    raw = open(study_path, 'rb').read()
    manifest = {
        'study': study['study'], 'study_sha256': hashlib.sha256(raw).hexdigest(), 'study_file': study,
        'code_version': _git(), 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'rows': len(rows), 'participants': len(study['participants']),
        'measures': {n: {'kind': measures.get(n).KIND, 'description': measures.get(n).DESCRIPTION}
                     for n in study['measures']},
        'period_definition': 'Period t runs from survey t-1 (inclusive) to survey t (exclusive). Dates are participant '
                             'local time without offset; the tz column gives the IANA time zone.',
        'speaker': 'Only messages the participant typed; text over 400 chars, with code blocks or many line breaks '
                   'is treated as pasted and excluded.',
    }
    with open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(research.HERE, 'study.demo.json')
    study = research.study(path)
    rows, evidence = build(study)
    m = write(study, path, rows, evidence)
    print(f"{m['rows']} rows, {m['participants']} participants -> {OUT}")


if __name__ == '__main__':
    main()
