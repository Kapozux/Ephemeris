"""多人盲标：研究者写编码手册，几个标注员各标各的同一批，先报人和人之间对得上多少，再报模型和人。

    ../venv/bin/python -m research.label make  [任务.json]   按任务抽题（固定种子，抽一次就定了），存进 data/label/<任务名>/
    ../venv/bin/python -m research.label serve [任务.json]   开 http://127.0.0.1:5067，每个标注员用 /?a=名字 进
    ../venv/bin/python -m research.label score [任务.json]   人和人 → 模型和人、每题用时、分歧清单
    ../venv/bin/python -m research.label next  [任务.json]   这一轮大家都标完、编码手册改好之后，开下一轮

任务文件（例：label.demo.json）写：用哪个研究方案、标窗口还是单条消息、每轮几题、标注员是谁、编码手册
（每题的问法、说明、选项；哪个选项算「弃权」不进一致率；要跟哪个指标的模型判断比）。默认 label.demo.json。

- 盲：页面只给原文和编码手册。模型的判断抽题时存进 model.json，标注页一律不读；标注员只看得到自己标的。
  分歧讨论要等这一轮所有人都标完才开，讨论时也不给模型的判断（不然定下来的「金标准」是照着模型改的）。
  各人标的存在 labels/<名字>.json：标注员在各自电脑上标，把这个文件交回来放进去就行。
- 轮：先标一小批 → score 看分歧 → 改编码手册（改 version）→ next 再标下一批（Dunivin 的循环）。
  每一轮锁住当时的编码手册版本：这一轮没标完就改了手册，serve 不开。
- 隐私：做法 B（原话不出参与者电脑）的研究不能拿去给别人标，make 直接拒。参与者拿掉的窗口、删掉的原话、
  退出的人一律不进题目（research.consent）。
- 用时：页面记每题停在前台的毫秒数，score 报中位数。
"""
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

import research
from research import agree, consent, measures, participant
from research.export import tz_of

TASK = os.path.join(research.HERE, 'label.demo.json')
PORT = 5067


def task(path=None):
    with open(path or TASK, encoding='utf-8') as f:
        return json.load(f)


def folder(t):
    return os.path.join(research.DATA, 'label', t['task'])


def _load(path, default):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _path(t, name):
    return os.path.join(folder(t), name)


def _labels_path(t, who):
    return os.path.join(folder(t), 'labels', f'{who}.json')


def _id(*parts):
    """题目 id：不带参与者编号，标注员看不出是谁的。"""
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]


# ---------- 抽题 ----------

def _units(t, study, pid, person):
    """这个人能出的题：[(题, 分层用的键)]。"""
    hidden = set(consent.get(pid)['hidden_evidence'])
    cap, chars = t.get('max_msgs'), t.get('max_chars', 600)
    show = lambda m: {'role': m.role, 'text': m.text[:chars]}  # noqa: E731
    out = []
    for conv, ms in person.convs.items():
        if t['unit'] == 'window':
            seg = ms[:cap] if cap else ms
            if len(seg) < 2 or {'human', 'assistant'} - {m.role for m in seg} or hidden & {m.id for m in seg}:
                continue
            out.append(({'id': _id(pid, conv), 'pid': pid, 'conv': conv, 'source': ms[0].source,
                         'msgs': [show(m) for m in seg], 'target': None}, (pid, ms[0].source)))
        else:
            k = t.get('context', 1)
            for i, m in enumerate(ms):
                seg = ms[max(0, i - k):i + k + 1]
                if not m.text.strip() or hidden & {x.id for x in seg}:
                    continue
                out.append(({'id': _id(pid, conv, m.id), 'pid': pid, 'conv': conv, 'msg': m.id, 'source': m.source,
                             'msgs': [show(x) for x in seg], 'target': seg.index(m)}, (pid, m.source, m.role)))
    return out


def _model(t, study, person, items):
    """要跟模型比的题：从指标插件的 judgments 拿模型的判断（有缓存就不再花钱）。{题 id: {题目 key: 值}}。"""
    out = defaultdict(dict)
    for q in t['codebook']['questions']:
        if not q.get('model'):
            continue
        name, fieldname = q['model'].split(':')
        got = measures.get(name).judgments(person, study)
        for it in items:
            j = got.get(it['conv'])
            if j and j.get('verified', True) and fieldname in j:
                out[it['id']][q['key']] = j[fieldname]
    return out


def make(path=None):
    t = task(path)
    if os.path.exists(_path(t, 'items.json')):
        sys.exit(f"{_path(t, 'items.json')} 已经有了：重抽会让标过的对不上。真要重抽先把这个文件夹挪走。")
    study = research.study(os.path.join(research.HERE, t['study']))
    if study.get('privacy') != 'A':
        sys.exit('这个研究是做法 B：原话不出参与者的电脑，不能拿去给别人标。')
    rng = random.Random(t['seed'])
    strata, model = defaultdict(list), {}
    for pid in study['participants']:
        if consent.get(pid)['withdrawn']:
            continue
        person = participant.load(pid, tz_of(pid), study.get('sources'))
        units = _units(t, study, pid, person)
        for it, key in units:
            strata[key].append(it)
        model.update(_model(t, study, person, [it for it, _ in units]))
    for v in strata.values():
        rng.shuffle(v)
    picked, keys, want = [], sorted(strata), sum(t['rounds'])
    while len(picked) < want and any(strata.values()):          # 各层轮流抽：每个人、每个平台都有
        for k in keys:
            if strata[k] and len(picked) < want:
                picked.append(strata[k].pop())
    rng.shuffle(picked)
    start = 0
    for r, n in enumerate(t['rounds'], 1):
        for it in picked[start:start + n]:
            it['round'] = r
        start += n
    _save(_path(t, 'items.json'), {'task': t['task'], 'seed': t['seed'], 'made': datetime.now().isoformat(timespec='seconds'),
                                   'items': picked})
    _save(_path(t, 'model.json'), {i: model[i] for i in (it['id'] for it in picked) if i in model})
    _save(_path(t, 'state.json'), {'round': 1, 'codebook': {'1': t['codebook']['version']}})
    print(f"{len(picked)} 题（{'、'.join(f'第 {r} 轮 {n}' for r, n in enumerate(t['rounds'], 1))}）-> {folder(t)}")
    print('按平台：', dict(Counter(it['source'] for it in picked)))
    return picked


# ---------- 标注员那边 ----------

def labels(t, who):
    return _load(_labels_path(t, who), {})


def round_items(t, items, r):
    return [it for it in items if it['round'] == r]


def order(t, items, who):
    """每个标注员一个顺序（种子 + 名字），前后顺序不会让两个人犯一样的错。"""
    ids = [it['id'] for it in items]
    random.Random(f"{t['seed']}:{who}").shuffle(ids)
    return ids


def finished(t, items, r):
    """这一轮每个标注员都标完了没有。"""
    ids = {it['id'] for it in round_items(t, items, r)}
    return all(ids <= set(labels(t, a)) for a in t['annotators'])


def public(t):
    """给页面的编码手册：不带要跟哪个模型比。"""
    cb = t['codebook']
    return {'version': cb['version'], 'intro': cb.get('intro', ''),
            'questions': [{k: q[k] for k in ('key', 'text', 'help', 'options', 'when') if k in q} for q in cb['questions']]}


def show(it):
    """给标注员看的一题：原文、平台。不带参与者编号、窗口 id、模型的判断。"""
    return {k: it[k] for k in ('id', 'source', 'msgs', 'target')}


def needed(t, it):
    """这题要答哪几问（有 when 的问只在条件满足时出现，例：只对参与者说的消息问「是不是贴的」）。"""
    out = []
    for q in t['codebook']['questions']:
        w = q.get('when')
        if w and it['target'] is not None and it['msgs'][it['target']]['role'] != w.get('role'):
            continue
        out.append(q['key'])
    return out


def save_label(t, items, who, iid, answers, ms):
    it = next((x for x in items if x['id'] == iid), None)
    state = _load(_path(t, 'state.json'), {})
    if not it or who not in t['annotators'] or it['round'] != state['round']:
        raise ValueError('没有这题，或者不在这一轮')
    keys = needed(t, it)
    valid = {q['key']: {o[0] for o in q['options']} for q in t['codebook']['questions']}
    if any(answers.get(k) not in valid[k] for k in keys):
        raise ValueError('有题没答，或者答案不在选项里')
    mine = labels(t, who)
    mine[iid] = {**{k: answers[k] for k in keys}, 'ms': int(ms or 0), 'codebook': t['codebook']['version'],
                 'at': datetime.now().isoformat(timespec='seconds')}
    mine['_last'] = iid
    _save(_labels_path(t, who), mine)


def undo(t, who):
    mine = labels(t, who)
    if mine.get('_last'):
        mine.pop(mine.pop('_last'), None)
        _save(_labels_path(t, who), mine)


def state(t, items, who):
    st = _load(_path(t, 'state.json'), {})
    r = st['round']
    mine = labels(t, who)
    ids = order(t, round_items(t, items, r), who)
    by = {it['id']: it for it in items}
    todo = [i for i in ids if i not in mine]
    nxt = by[todo[0]] if todo else None
    return {'annotator': who, 'round': r, 'rounds': len(t['rounds']), 'codebook': public(t),
            'next': dict(show(nxt), needs=needed(t, nxt)) if nxt else None,
            'done': len(ids) - len(todo), 'total': len(ids), 'last': bool(mine.get('_last')),
            'discuss': finished(t, items, r)}


# ---------- 分歧讨论（这一轮所有人标完才开）----------

def _abstain(t):
    return {q['key']: set(q.get('abstain', [])) for q in t['codebook']['questions']}


def disagreements(t, items, r):
    """这一轮里标注员没对上的题，每人的答案并排（不给模型的）。弃权也算没对上：要讨论。"""
    if not finished(t, items, r):
        return None
    every = {a: labels(t, a) for a in t['annotators']}
    cons = _load(_path(t, 'consensus.json'), {})
    out = []
    for it in round_items(t, items, r):
        for k in needed(t, it):
            got = {a: every[a][it['id']][k] for a in t['annotators']}
            if len(set(got.values())) > 1:
                out.append({'item': show(it), 'key': k, 'answers': got, 'agreed': cons.get(it['id'], {}).get(k)})
    return out


def agree_on(t, iid, key, value):
    """讨论后定下来的答案（金标准那一列）。"""
    cons = _load(_path(t, 'consensus.json'), {})
    cons.setdefault(iid, {})[key] = value
    _save(_path(t, 'consensus.json'), cons)


def create_app(path=None):
    from flask import Flask, abort, jsonify, request, send_file
    t = task(path)
    data = _load(_path(t, 'items.json'), None) or sys.exit('先跑 make')
    items = data['items']
    st = _load(_path(t, 'state.json'), {})
    if st['codebook'][str(st['round'])] != t['codebook']['version']:
        sys.exit(f"第 {st['round']} 轮用的编码手册是 {st['codebook'][str(st['round'])]}，任务文件里现在是 "
                 f"{t['codebook']['version']}：这一轮中途不能改手册。改回去，或者先标完这轮再 next。")
    app = Flask(__name__)

    @app.before_request
    def local_only():
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(403)
        if request.method == 'POST' and request.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
            abort(403)

    def who():
        a = request.args.get('a') or (request.get_json(silent=True) or {}).get('a')
        if a not in t['annotators']:
            abort(400)
        return a

    @app.get('/')
    def page():
        return send_file(os.path.join(os.path.dirname(__file__), 'label.html'))

    @app.get('/api/who')
    def annotators():
        return jsonify(task=t['task'], annotators=t['annotators'])

    @app.get('/api/state')
    def get_state():
        return jsonify(state(t, items, who()))

    @app.post('/api/label')
    def post_label():
        d = request.get_json() or {}
        try:
            save_label(t, items, who(), d.get('id'), d.get('answers') or {}, d.get('ms'))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)

    @app.post('/api/undo')
    def post_undo():
        undo(t, who())
        return jsonify(ok=True)

    @app.get('/api/discuss')
    def get_discuss():
        who()
        r = _load(_path(t, 'state.json'), {})['round']
        got = disagreements(t, items, r)
        if got is None:
            abort(409)
        return jsonify(round=r, codebook=public(t), items=got)

    @app.post('/api/agree')
    def post_agree():
        who()
        d = request.get_json() or {}
        q = next((q for q in t['codebook']['questions'] if q['key'] == d.get('key')), None)
        if not q or d.get('value') not in {o[0] for o in q['options']} or d.get('id') not in {it['id'] for it in items}:
            abort(400)
        agree_on(t, d['id'], d['key'], d['value'])
        return jsonify(ok=True)

    return app


def serve(path=None):
    t = task(path)
    app = create_app(path)
    print(f"标注任务 {t['task']}：http://127.0.0.1:{PORT}（标注员：{'、'.join(t['annotators'])}）")
    app.run('127.0.0.1', PORT)


def next_round(path=None):
    t = task(path)
    items = _load(_path(t, 'items.json'), {})['items']
    st = _load(_path(t, 'state.json'), {})
    r = st['round']
    if r >= len(t['rounds']):
        sys.exit('已经是最后一轮了')
    if not finished(t, items, r):
        sys.exit(f'第 {r} 轮还有人没标完')
    if st['codebook'][str(r)] == t['codebook']['version']:
        print(f"提醒：编码手册还是 {t['codebook']['version']}，没改过。")
    st['round'] = r + 1
    st['codebook'][str(r + 1)] = t['codebook']['version']
    _save(_path(t, 'state.json'), st)
    print(f"开第 {r + 1} 轮，编码手册 {t['codebook']['version']}")


# ---------- 算数：先人和人，再模型和人 ----------

def _time(rows):
    ms = [x['ms'] for x in rows if x.get('ms')]
    return {'n': len(ms), 'median_s': round(median(ms) / 1000, 1) if ms else None}


def score(path=None):
    t = task(path)
    items = _load(_path(t, 'items.json'), {})['items']
    model, cons = _load(_path(t, 'model.json'), {}), _load(_path(t, 'consensus.json'), {})
    every = {a: labels(t, a) for a in t['annotators']}
    skip = _abstain(t)
    out = {'task': t['task'], 'annotators': t['annotators'], 'rounds': []}
    for r in range(1, len(t['rounds']) + 1):
        its = round_items(t, items, r)
        got = {a: {it['id']: every[a][it['id']] for it in its if it['id'] in every[a]} for a in t['annotators']}
        rr = {'round': r, 'items': len(its), 'labelled': {a: len(v) for a, v in got.items()},
              'codebook': sorted({x['codebook'] for v in got.values() for x in v.values()}),
              'time': {a: _time(v.values()) for a, v in got.items()}, 'questions': {}}
        for q in t['codebook']['questions']:
            k = q['key']
            asked = [it for it in its if k in needed(t, it)]
            ans = {it['id']: {a: got[a][it['id']][k] for a in t['annotators']
                              if it['id'] in got[a] and got[a][it['id']].get(k) not in skip[k] | {None}} for it in asked}
            src = {it['id']: it['source'] for it in asked}
            hh = {'alpha': agree.alpha([list(v.values()) for v in ans.values()]),
                  'items_2plus': sum(len(v) >= 2 for v in ans.values()),
                  'abstained': {a: sum(got[a].get(it['id'], {}).get(k) in skip[k] for it in asked) for a in t['annotators']},
                  'pairs': {f'{a}~{b}': agree.pair([(v[a], v[b]) for v in ans.values() if a in v and b in v])
                            for i, a in enumerate(t['annotators']) for b in t['annotators'][i + 1:]},
                  'by_platform': {s: agree.alpha([list(v.values()) for i, v in ans.items() if src[i] == s])
                                  for s in sorted(set(src.values()))},
                  'dist': {a: dict(Counter(got[a][it['id']][k] for it in asked if it['id'] in got[a]))
                           for a in t['annotators']}}
            qq = {'human_human': hh}
            if q.get('model'):
                gold = {}
                for i, v in ans.items():                      # 讨论定下来的优先；没讨论的，标注员全对上了才算
                    c = cons.get(i, {}).get(k)
                    if c is not None and c not in skip[k]:
                        gold[i] = c
                    elif c is None and len(v) == len(t['annotators']) and len(set(v.values())) == 1:
                        gold[i] = next(iter(v.values()))
                mk = {i: model[i][k] for i in ans if k in model.get(i, {})}
                qq['model_vs_human'] = {
                    'baseline': hh['items_2plus'] > 0,
                    'vs_each': {a: agree.pair([(v[a], mk[i]) for i, v in ans.items() if a in v and i in mk])
                                for a in t['annotators']},
                    'vs_gold': agree.pair([(gold[i], mk[i]) for i in gold if i in mk]),
                    'gold_from': {'discussed': sum(1 for i in gold if i in cons and k in cons[i]),
                                  'unanimous': sum(1 for i in gold if not (i in cons and k in cons[i]))},
                    'by_platform': {s: agree.pair([(gold[i], mk[i]) for i in gold if i in mk and src[i] == s])
                                    for s in sorted(set(src.values()))}}
            qq['disagreements'] = sum(1 for it in asked
                                      if len({got[a].get(it['id'], {}).get(k) for a in t['annotators']}) > 1)
            rr['questions'][k] = qq
        out['rounds'].append(rr)
    _save(_path(t, 'score.json'), out)
    for rr in out['rounds']:
        print(f"第 {rr['round']} 轮（{rr['items']} 题，手册 {'/'.join(rr['codebook']) or '—'}）"
              f" 已标 {rr['labelled']}，每题中位用时 { {a: v['median_s'] for a, v in rr['time'].items()} } 秒")
        for k, qq in rr['questions'].items():
            hh = qq['human_human']
            a = '—' if hh['alpha'] is None else f"{hh['alpha']:.3f}"
            print(f"  {k}：人和人 α = {a}（{hh['items_2plus']} 题两人以上答了），"
                  f"分歧 {qq['disagreements']} 题")
            mv = qq.get('model_vs_human')
            if mv:
                g = mv['vs_gold']
                kp = '—' if g['kappa'] is None else f"{g['kappa']:.3f}"
                note = '' if mv['baseline'] else '（还没有人和人的一致率，这个数先别看）'
                print(f"      模型和金标准 κ = {kp}（{g['n']} 题）{note}")
    return out


if __name__ == '__main__':
    {'make': make, 'serve': serve, 'score': score, 'next': next_round}[sys.argv[1]](*sys.argv[2:3])
