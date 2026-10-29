"""稳定性：同一批窗口、同一个提示词，「想法谁先提」再判几遍，看结果稳不稳（Krippendorff α，名义尺度）。

    ../venv/bin/python -m research.stability [遍数，默认再跑 2 遍]

第 1 遍就是 data/<pid>.judge.json；新跑的存 data/judge_runs/<pid>.r<n>.json（有了就跳过，不重复花钱）。
结果写进 data/out/stability.json，selfcheck / 演示页读它。模型照研究方案的 models（research.route）。
"""
import json
import os
import sys
from collections import Counter

import research
from research import participant
from research.agree import alpha
from research.export import OUT, tz_of
from research.measures import chat_ai_first_idea_ratio as idea

RUNS = os.path.join(research.DATA, 'judge_runs')
LABELS = ('user', 'ai', 'none')


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
