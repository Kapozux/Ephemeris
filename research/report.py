"""演示页面：一个自包含的 HTML（数据和 Chart.js 都内嵌，不用起服务，双击打开）。给研究者看，所以是英文。

    ../venv/bin/python -m research.report       读 data/out/ 和各参与者的库，写 data/out/report.html

三屏：参与者总表 / 指标 × 时间（叠 CES-D，原始值 ↔ 个人内 z）/ 点一个数据点看支撑它的原话。
另有「流程自检」和「导出」两栏。
"""
import csv
import json
import os
import sqlite3
from collections import Counter

import research
from research import measures
from research import personas as P
from research.export import OUT, tz_of
from research.participant import load

ROOT = os.path.dirname(research.HERE)


def _read(name):
    path = os.path.join(OUT, name)
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None


def gather(study):
    with open(os.path.join(OUT, 'data_clean.csv'), newline='') as f:
        rows = list(csv.DictReader(f))
    people, convs = [], {}
    for pid in study['participants']:
        person, persona = load(pid, tz_of(pid), study.get('sources')), P.by_pid(pid)
        said = [m for ms in person.convs.values() for m in ms if m.role == 'human']
        db = sqlite3.connect(f'file:{research.db_path(pid)}?mode=ro', uri=True)
        titles = dict(db.execute('SELECT id, title FROM conversations'))
        db.close()
        for c, ms in person.convs.items():
            convs[c] = {'pid': pid, 'title': titles.get(c, ''), 'msgs': [
                {'id': m.id, 'role': m.role, 'source': m.source, 'at': m.at.strftime('%Y-%m-%d %H:%M'), 'text': m.text}
                for m in ms]}
        empty = [p.number for p in person.periods if not p.said()]
        people.append({
            'pid': pid, 'name': persona['name'], 'age': persona['age'], 'lang': persona['lang'], 'tz': persona['tz'],
            'platforms': dict(Counter(m.source for m in said)), 'windows': len(person.convs),
            'typed': sum(m.typed for m in said), 'pasted': sum(not m.typed for m in said),
            'first': min(m.at for m in said).strftime('%Y-%m-%d') if said else '',
            'last': max(m.at for m in said).strftime('%Y-%m-%d') if said else '',
            'periods': len(person.periods), 'empty_periods': empty,
            'cesd': [float(r['cesd_score']) for r in rows if r['pid'] == pid],
        })
    return {
        'study': study, 'manifest': _read('manifest.json'), 'selfcheck': _read('selfcheck.json'),
        'rows': rows, 'evidence': _read('evidence.json') or {}, 'people': people, 'convs': convs,
        'measures': {n: {'kind': measures.get(n).KIND, 'description': measures.get(n).DESCRIPTION}
                     for n in study['measures']},
        'plants': P.PLANTS,
    }


def main():
    study = research.study()
    data = gather(study)
    chart = open(os.path.join(ROOT, 'static', 'vendor', 'chart.umd.min.js'), encoding='utf-8').read()
    page = open(os.path.join(research.HERE, 'report_template.html'), encoding='utf-8').read()
    page = (page.replace('/*CHART_JS*/', chart.replace('</script', '<\\/script'))
                .replace('/*DATA*/null', json.dumps(data, ensure_ascii=False).replace('</', '<\\/')))
    path = os.path.join(OUT, 'report.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(page)
    print('->', path)


if __name__ == '__main__':
    main()
