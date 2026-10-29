"""稳定性：同一批窗口、同一个提示词，「想法谁先提」再判几遍，看结果稳不稳（Krippendorff α，名义尺度）。

    ../venv/bin/python -m research.stability [遍数，默认再跑 2 遍]

第 1 遍就是 data/<pid>.judge.json；新跑的存 data/judge_runs/<pid>.r<n>.json（有了就跳过，不重复花钱）。
结果写进 data/out/stability.json，selfcheck / 演示页读它。模型照研究方案的 models（research.route）。
"""
import json
import os
import sys
from collections import Counter
from itertools import permutations

import research
from research import participant
from research.export import OUT, tz_of
from research.measures import chat_ai_first_idea_ratio as idea

RUNS = os.path.join(research.DATA, 'judge_runs')
LABELS = ('user', 'ai', 'none')


def alpha(units):
    """Krippendorff α（名义）。units：每个窗口一组标签（缺的不放），少于 2 个标签的窗口不计。"""
    pairs = Counter()
    for labels in units:
        m = len(labels)
        if m < 2:
            continue
        for a, b in permutations(range(m), 2):
            pairs[labels[a], labels[b]] += 1 / (m - 1)
    n = sum(pairs.values())
    if not n:
        return None
    marg = Counter()
    for (a, _), w in pairs.items():
        marg[a] += w
    d_obs = sum(w for (a, b), w in pairs.items() if a != b) / n
    d_exp = sum(marg[a] * marg[b] for a in marg for b in marg if a != b) / (n * (n - 1))
    return 1 - d_obs / d_exp if d_exp else 1.0


def run(extra=2):
    study = research.study()
    os.makedirs(RUNS, exist_ok=True)
    units, by_run = [], Counter()
    for pid in study['participants']:
        person = participant.load(pid, tz_of(pid), study.get('sources'))
        first = json.load(open(os.path.join(research.DATA, f'{pid}.judge.json'), encoding='utf-8'))
        runs = [first]
        for n in range(2, extra + 2):
            path = os.path.join(RUNS, f'{pid}.r{n}.json')
            if not os.path.exists(path):
                got = idea.judge(list(person.convs.items()), study, pid)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(got, f, ensure_ascii=False, indent=1)
                print(pid, f'run {n}: {len(got)} windows', flush=True)
            runs.append(json.load(open(path, encoding='utf-8')))
        for conv in person.convs:
            labels = [r[conv]['idea_from'] for r in runs if conv in r]
            units.append(labels)
            by_run[len(set(labels)) == 1] += 1
    r = {'runs': extra + 1, 'windows': len(units), 'all_runs_agree': by_run[True],
         'alpha': alpha(units), 'models': study['models'], 'prompt_version': idea.VERSION}
    with open(os.path.join(OUT, 'stability.json'), 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    print(f"{r['runs']} 遍、{r['windows']} 个窗口：每遍都一样的 {r['all_runs_agree']}，Krippendorff α = {r['alpha']:.3f}")
    return r


if __name__ == '__main__':
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
