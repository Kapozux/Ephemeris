"""流程自检：埋下的故事线（personas.PLANTS）从导出的表里找不找得回来。

    ../venv/bin/python -m research.selfcheck        读 data/out/data_clean.csv，写 data/out/selfcheck.json

**这只说明流程没坏，不说明指标有效**：故事线、聊天、量表分数都是我们自己配的。所以只报召回、误报和一致率，
不算相关、不报 p 值、不跑预测。
- 找回：埋的那几期，指标的个人内 z 分数（相对本人所有期的均值和标准差）平均 ≥ FOUND_Z（方向对）
- 误报：没埋任何东西的参与者（personas.CONTROL）里，|z| > 2 的期数
- 一致率：「想法由谁先提」的判断，跟生成聊天的模型自己标的 idea_from 对上几成。那个标签也是模型写的、
  抽查过错得不少，所以叫一致率，不叫准确率
- 盲评：spotcheck.demo.json 里 20 个随机窗口，另一个模型（Claude，不是人）不看两边标签自己标的；
  报判断模型和生成标签各对几个。人工抽查还没做
"""
import csv
import json
import os
import statistics

import research
from research import personas as P
from research.export import OUT

FOUND_Z = 0.5


def zscores(values):
    xs = [v for v in values.values() if v is not None]
    if len(xs) < 3:
        return {}
    mu, sd = statistics.mean(xs), statistics.pstdev(xs)
    return {k: (v - mu) / sd if sd and v is not None else None for k, v in values.items()}


def series(rows, pid, col):
    return {int(r['period_number']): float(r[col]) if r[col] != '' else None for r in rows if r['pid'] == pid}


def recall(rows):
    out = []
    for plant in P.PLANTS:
        z = zscores(series(rows, plant['pid'], plant['measure']))
        hits = [z.get(k) for k in plant['periods'] if z.get(k) is not None]
        mean = statistics.mean(hits) if hits else None
        sign = 1 if plant['direction'] == 'up' else -1
        out.append(dict(plant, mean_z=mean, found=mean is not None and sign * mean >= FOUND_Z))
    return out


def false_alarms(rows, measures):
    out = {}
    for pid in P.CONTROL:
        n, flagged = 0, []
        for col in measures:
            for k, v in zscores(series(rows, pid, col)).items():
                if v is not None:
                    n += 1
                    if abs(v) > 2:
                        flagged.append({'measure': col, 'period': k, 'z': v})
        out[pid] = {'checked': n, 'flagged': flagged}
    return out


def agreement(pids):
    pairs = []
    for pid in pids:
        truth_path = os.path.join(research.DATA, f'{pid}.truth.json')
        judge_path = os.path.join(research.DATA, f'{pid}.judge.json')
        if not (os.path.exists(truth_path) and os.path.exists(judge_path)):
            continue
        truth, judged = json.load(open(truth_path)), json.load(open(judge_path))
        pairs += [(truth[c]['idea_from'], judged[c]['idea_from'], judged[c]['verified'])
                  for c in truth if c in judged]
    verified = [(t, j) for t, j, ok in pairs if ok]
    return {'windows': len(pairs), 'quote_verified': len(verified),
            'agree': sum(t == j for t, j in verified),
            'agree_rate': sum(t == j for t, j in verified) / len(verified) if verified else None,
            'confusion': {f'{t}->{j}': sum(1 for a, b in verified if (a, b) == (t, j))
                          for t in ('user', 'ai', 'none') for j in ('user', 'ai', 'none')}}


def spotcheck(path=os.path.join(research.HERE, 'spotcheck.demo.json')):
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding='utf-8'))
    judge, truth = {}, {}
    for pid in {w['pid'] for w in d['windows']}:
        judge[pid] = json.load(open(os.path.join(research.DATA, f'{pid}.judge.json')))
        truth[pid] = json.load(open(os.path.join(research.DATA, f'{pid}.truth.json')))
    ws = d['windows']
    return {'annotator': d['annotator'], 'note': d['note'], 'n': len(ws),
            'judge_correct': sum(judge[w['pid']][w['conv']]['idea_from'] == w['label'] for w in ws),
            'generator_correct': sum(truth[w['pid']][w['conv']]['idea_from'] == w['label'] for w in ws)}


def main():
    study = research.study()
    with open(os.path.join(OUT, 'data_clean.csv'), newline='') as f:
        rows = list(csv.DictReader(f))
    r = {'recall': recall(rows), 'false_alarms': false_alarms(rows, study['measures']),
         'idea_agreement': agreement(study['participants']), 'spotcheck': spotcheck(), 'found_z': FOUND_Z}
    with open(os.path.join(OUT, 'selfcheck.json'), 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    found = sum(p['found'] for p in r['recall'])
    print(f'找回 {found}/{len(r["recall"])} 条故事线')
    for p in r['recall']:
        print(f"  {'✓' if p['found'] else '✗'} {p['pid']} {p['measure']} 期 {p['periods']}: "
              f"平均 z = {p['mean_z'] if p['mean_z'] is None else round(p['mean_z'], 2)}")
    for pid, fa in r['false_alarms'].items():
        print(f'  对照 {pid}：{fa["checked"]} 个数里 |z|>2 的 {len(fa["flagged"])} 个 {fa["flagged"]}')
    a = r['idea_agreement']
    print(f"  想法谁先提：{a['windows']} 个窗口，原话核对通过 {a['quote_verified']}，和生成标签一致 {a['agree']}"
          f"（{a['agree_rate'] if a['agree_rate'] is None else round(a['agree_rate'], 2)}）")
    sc = r['spotcheck']
    if sc:
        print(f"  盲评 {sc['n']} 个窗口（{sc['annotator'][:40]}…）：判断模型对 {sc['judge_correct']}，生成标签对 {sc['generator_correct']}")


if __name__ == '__main__':
    main()
