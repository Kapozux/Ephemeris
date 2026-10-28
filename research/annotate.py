"""真实数据验证第 2、3 项的本机标注工具（不是 Ephemeris，只给作者自己标）。

    ../venv/bin/python -m research.annotate sample    固定种子抽题：100 条消息（说话人 + 粘贴）、50 段对话（想法谁先提）
    ../venv/bin/python -m research.annotate judge     模型把那 50 段判两遍（盲标对照 + 模型自己稳不稳），省钱路线
    ../venv/bin/python -m research.annotate topics    每段一个中性话题（第 3 项跟题目一起显示）
    ../venv/bin/python -m research.annotate serve     开 http://127.0.0.1:5065 标；随时关，标过的都存着
    ../venv/bin/python -m research.annotate score     算数：准确率、精确率 / 召回率、一致率、Cohen's κ、Gwet's AC1

全在 realval 的只读拷贝上（data/real/chats.db）；题目和标注只存在 data/real/annotate/（不进仓库），对外只给数字。
第 3 项是盲标：页面只给对话原文和一个中性的话题（topics，「关于……」，让人和模型判的是同一个想法），
不给模型判的什么、也不标模型认为想法在哪句（那等于告诉答案）。
跟模型看的是同一段：窗口前 SEG_MSGS 条、每条前 600 字。其中 RETEST 段过 RETEST_DAYS 天后打乱顺序再标一次。
"""
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import research
import speech
from research import realval
from research.participant import Msg

DIR = os.path.join(realval.REAL, 'annotate')
ITEMS = os.path.join(DIR, 'items.json')
LABELS = os.path.join(DIR, 'labels.json')
JUDGE = os.path.join(DIR, 'judge.json')
TOPICS = os.path.join(DIR, 'topics.json')
BANNED = re.compile(r'是否|要不要|该不该|怎么|如何|哪|选|决定|建议|方案|我|你|AI|[？?]')   # 问句 / 提到谁：看得出模型判的是什么
SEED = 20261028
N_MSGS = 100
N_SEGS = 50
RETEST = 10
RETEST_DAYS = 3
SEG_MSGS = 30
LONG = 400                   # 长消息有意多抽：一半是超过这么多字或带代码块 / 多行的
PORT = 5065               # 5060、5061 是 SIP，Chrome 不让开（ERR_UNSAFE_PORT）
IDEA = {'user': '我先提的', 'ai': 'AI 先提的', 'unsure': '分不清', 'none': '这个话题上没定下什么'}


def _load(path, default):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else default


def _save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _round_robin(strata, n, rng):
    for v in strata.values():
        rng.shuffle(v)
    out, keys = [], sorted(strata)
    while len(out) < n and any(strata.values()):
        for k in keys:
            if strata[k] and len(out) < n:
                out.append(strata[k].pop())
    return out


def sample():
    if os.path.exists(ITEMS):
        sys.exit(f'{ITEMS} 已经有了：重抽会让标过的对不上。真要重抽先把它挪走。')
    os.makedirs(DIR, exist_ok=True)
    rng = random.Random(SEED)
    db = realval._db()
    ws = realval.windows(db)
    rows = defaultdict(list)
    for mid, cid, source, ts, role, text in db.execute(
            'SELECT id, conv_id, source, ts, role, text FROM messages WHERE text IS NOT NULL ORDER BY ts, rowid'):
        if cid in ws:
            rows[cid].append({'id': mid, 'source': source, 'ts': ts, 'role': role, 'text': text})

    # 第 2 项：按（平台, 说话人, 长短）分层，人 / AI 各一半，每边长的一半
    strata = defaultdict(list)
    for cid, ms in rows.items():
        for i, m in enumerate(ms):
            if m['role'] in ('human', 'assistant') and m['text'].strip():
                long = len(m['text']) > LONG or not speech.typed(m['text'])
                strata[m['role'], long, m['source']].append((cid, i))
    msgs = []
    for role in ('human', 'assistant'):
        for long in (True, False):
            sub = {k: v for k, v in strata.items() if k[0] == role and k[1] == long}
            msgs += _round_robin(sub, N_MSGS // 4, rng)
    rng.shuffle(msgs)
    items = []
    for cid, i in msgs:
        ms = rows[cid]
        ctx = lambda j: {'role': ms[j]['role'], 'text': ms[j]['text'][:300]} if 0 <= j < len(ms) else None  # noqa: E731
        m = ms[i]
        items.append({'kind': 'msg', 'id': m['id'], 'conv': cid, 'source': m['source'], 'quarter': ws[cid]['quarter'],
                      'role': m['role'], 'text': m['text'], 'before': ctx(i - 1), 'after': ctx(i + 1),
                      'filter_pasted': not speech.typed(m['text']), 'chars': len(m['text'])})

    # 第 3 项：两边都说过话的窗口，按（平台, 季度）分层
    strata = defaultdict(list)
    for cid, ms in rows.items():
        seg = ms[:SEG_MSGS]
        if len(seg) >= 4 and {'human', 'assistant'} <= {m['role'] for m in seg}:
            strata[ws[cid]['source'], ws[cid]['quarter']].append(cid)
    segs = [{'kind': 'seg', 'id': cid, 'source': ws[cid]['source'], 'quarter': ws[cid]['quarter'],
             'msgs': [{'id': m['id'], 'role': m['role'], 'ts': m['ts'], 'text': m['text'][:600]} for m in rows[cid][:SEG_MSGS]]}
            for cid in _round_robin(strata, N_SEGS, rng)]
    retest = rng.sample([s['id'] for s in segs], RETEST)
    _save(ITEMS, {'seed': SEED, 'made': datetime.now().isoformat(timespec='seconds'), 'msgs': items, 'segs': segs,
                  'retest': retest})
    print(f'{len(items)} 条消息、{len(segs)} 段对话（{RETEST} 段之后重标）-> {ITEMS}')
    print('消息按平台：', dict(Counter(i['source'] for i in items)))
    print('对话按平台：', dict(Counter(s['source'] for s in segs)), '按季度：', dict(Counter(s['quarter'] for s in segs)))


def judge(runs=2):
    """模型判同样那 50 段（提示词跟研究版指标一样），判 runs 遍，存着不重判。"""
    from research.measures import chat_ai_first_idea_ratio as idea
    items = _load(ITEMS, None)
    got = _load(JUDGE, {})
    windows = [(s['id'], [Msg(m['id'], s['id'], s['source'], datetime.fromisoformat(m['ts']), m['role'], m['text'],
                              speech.typed(m['text'])) for m in s['msgs']]) for s in items['segs']]
    for r in range(1, runs + 1):
        key = f'r{r}'
        todo = [w for w in windows if w[0] not in got.get(key, {})]
        if todo:
            got.setdefault(key, {}).update(idea.judge(todo))
            _save(JUDGE, got)
        print(key, f"{len(got[key])}/{len(windows)} 段判好（prompt {idea.VERSION}）")


TOPIC_PROMPT = """Below is a chat between a person ("H") and an AI assistant ("A").
{focus}
Write a short, neutral topic label in Chinese, at most 16 characters: "关于" followed by a plain NOUN PHRASE naming the subject
(e.g. "关于学校", "关于论文结构", "关于数据库备份"), never the answer ("决定换学校") and never a question.
Do not use any of these: 是否 要不要 该不该 怎么 如何 哪 选 决定 建议 方案 我 你 AI ？ ?. It must not reveal who proposed
anything or whether anything was decided, and must not reuse either side's distinctive wording.

{transcript}

Output only JSON: {{"topic": "关于……"}}"""


def topics():
    """给每段一个中性的话题（第 3 项盲标时跟题目一起显示），让人和模型判的是同一个想法、又不泄题。
    模型判出想法的段：话题来自它判的那句（它在哪句、谁说的不给人看）；判成 none 的段：话题是这段主要在讨论什么。
    两种写法一样（都是「关于……」），看话题看不出模型判的是不是 none。"""
    research.ledger()
    import llm
    from research.measures.chat_ai_first_idea_ratio import _transcript
    items, r1 = _load(ITEMS, None), _load(JUDGE, {}).get('r1', {})
    got = _load(TOPICS, {})

    def one(s):
        msgs = [Msg(m['id'], s['id'], s['source'], None, m['role'], m['text'], True) for m in s['msgs']]
        j = r1.get(s['id']) or {}
        if j.get('idea_from') in ('user', 'ai') and j.get('quote'):
            k = next((i for i, m in enumerate(msgs) if m.id == j.get('msg')), None)
            focus = (f'An option for a decision is first proposed {"in message [" + str(k) + "]" if k is not None else ""}: '
                     f'"{j["quote"]}". Label the question that this option answers.')
        else:
            focus = 'Label the main question this chat is about.'
        d = llm.ask_json(TOPIC_PROMPT.format(focus=focus, transcript=_transcript(msgs)), want=dict, tries=2,
                         route='cheap', purpose='research-topic') or {}
        t = str(d.get('topic') or '').strip()
        return s['id'], t if t.startswith('关于') and not BANNED.search(t) else ''

    for _ in range(3):                                     # 不合格式的再要一次
        todo = [s for s in items['segs'] if not got.get(s['id']) or BANNED.search(got[s['id']])]
        with ThreadPoolExecutor(6) as pool:
            for cid, t in pool.map(one, todo):
                if t:
                    got[cid] = t
    _save(TOPICS, got)
    print(f"{len(got)}/{len(items['segs'])} 段有话题 -> {TOPICS}")


# ---------- 标注页 ----------

def _queue(items, labels):
    """该标的题，按顺序：消息 → 对话 → 到时间了的重标（打乱）。"""
    q = [('msg', i['id']) for i in items['msgs']] + [('seg', s['id']) for s in items['segs']]
    firsts = [labels['seg'].get(c, {}).get('at') for c in items['retest']]
    due = all(firsts) and datetime.now() >= max(datetime.fromisoformat(a) for a in firsts) + timedelta(days=RETEST_DAYS)
    re = list(items['retest'])
    random.Random(SEED + 1).shuffle(re)
    return q, [('retest', c) for c in re], due, firsts


def serve():
    from flask import Flask, abort, jsonify, request, send_file
    items = _load(ITEMS, None) or sys.exit('先跑 sample')
    topic = _load(TOPICS, {})
    if len(topic) < len(items['segs']):
        sys.exit('先跑 topics')
    by = {('msg', i['id']): i for i in items['msgs']} | {('seg', s['id']): s for s in items['segs']}
    app = Flask(__name__)

    @app.before_request
    def local_only():
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(403)
        if request.method == 'POST' and request.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
            abort(403)

    @app.get('/')
    def page():
        return send_file(os.path.join(os.path.dirname(__file__), 'annotate.html'))

    @app.get('/api/state')
    def state():
        labels = _load(LABELS, {'msg': {}, 'seg': {}, 'retest': {}})
        q, re, due, firsts = _queue(items, labels)
        todo = [k for k in q if k[1] not in labels[k[0]]] + ([k for k in re if k[1] not in labels['retest']] if due else [])
        nxt = None
        if todo:
            kind, iid = todo[0]
            it = dict(by['seg' if kind == 'retest' else kind, iid])
            it.pop('filter_pasted', None)                  # 盲标：不给过滤器的判断
            if kind != 'msg':
                it['topic'] = topic.get(iid)
            nxt = dict(it, kind=kind)
        waiting = None if due or len(labels['retest']) >= RETEST else (
            (max(datetime.fromisoformat(a) for a in firsts) + timedelta(days=RETEST_DAYS)).strftime('%m-%d %H:%M')
            if all(firsts) else 'after')
        done = len(labels['msg']) + len(labels['seg']) + len(labels['retest'])
        return jsonify(next=nxt, done=done, total=len(q) + len(re), waiting=waiting,
                       parts={'msg': [len(labels['msg']), len(items['msgs'])],
                              'seg': [len(labels['seg']), len(items['segs'])],
                              'retest': [len(labels['retest']), len(re)]},
                       last=labels.get('_last'), idea=IDEA)

    @app.post('/api/label')
    def label():
        d = request.get_json()
        kind, iid, ans = d.get('kind'), d.get('id'), d.get('answer') or {}
        if kind not in ('msg', 'seg', 'retest') or ('seg' if kind == 'retest' else kind, iid) not in by:
            abort(400)
        labels = _load(LABELS, {'msg': {}, 'seg': {}, 'retest': {}})
        labels[kind][iid] = dict(ans, at=datetime.now().isoformat(timespec='seconds'))
        labels['_last'] = [kind, iid]
        _save(LABELS, labels)
        return jsonify(ok=True)

    @app.post('/api/undo')
    def undo():
        labels = _load(LABELS, {'msg': {}, 'seg': {}, 'retest': {}})
        if labels.get('_last'):
            kind, iid = labels.pop('_last')
            labels[kind].pop(iid, None)
            _save(LABELS, labels)
        return jsonify(ok=True)

    print(f'标注工具（不是 Ephemeris）：http://127.0.0.1:{PORT}')
    app.run('127.0.0.1', PORT)


# ---------- 算数 ----------

def kappa(pairs):
    """Cohen's κ 和 Gwet's AC1（两个评分者、名义）。pairs：[(a, b)]。"""
    n = len(pairs)
    if not n:
        return None, None, None
    cats = sorted({x for p in pairs for x in p})
    po = sum(a == b for a, b in pairs) / n
    pa = Counter(a for a, _ in pairs)
    pb = Counter(b for _, b in pairs)
    pe = sum(pa[c] * pb[c] for c in cats) / n / n
    k = (po - pe) / (1 - pe) if pe < 1 else 1.0
    q = len(cats)
    pi = {c: (pa[c] + pb[c]) / (2 * n) for c in cats}
    pe_g = sum(p * (1 - p) for p in pi.values()) / (q - 1) if q > 1 else 0
    ac1 = (po - pe_g) / (1 - pe_g) if pe_g < 1 else 1.0
    return po, k, ac1


def _stat(pairs):
    po, k, ac1 = kappa(pairs)
    return {'n': len(pairs), 'agree': po, 'kappa': k, 'ac1': ac1}


def score():
    items, labels, got = _load(ITEMS, None), _load(LABELS, {'msg': {}, 'seg': {}, 'retest': {}}), _load(JUDGE, {})
    out = {'seed': SEED}
    # 第 2 项
    ms = [(i, labels['msg'][i['id']]) for i in items['msgs'] if i['id'] in labels['msg']]
    sp = [(i, l) for i, l in ms if l.get('speaker') in ('right', 'wrong')]
    out['speaker'] = {'labelled': len(ms), 'judged': len(sp), 'right': sum(l['speaker'] == 'right' for _, l in sp),
                      'unsure': len(ms) - len(sp),
                      'by_source': {s: [sum(l['speaker'] == 'right' for i, l in sp if i['source'] == s),
                                        sum(i['source'] == s for i, _ in sp)] for s in sorted({i['source'] for i, _ in sp})}}
    hp = [(i, l) for i, l in ms if i['role'] == 'human' and l.get('speaker') != 'wrong' and l.get('pasted') in ('yes', 'no')]
    tp = sum(i['filter_pasted'] and l['pasted'] == 'yes' for i, l in hp)
    fp = sum(i['filter_pasted'] and l['pasted'] == 'no' for i, l in hp)
    fn = sum(not i['filter_pasted'] and l['pasted'] == 'yes' for i, l in hp)
    out['paste'] = {'n': len(hp), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': len(hp) - tp - fp - fn,
                    'precision': tp / (tp + fp) if tp + fp else None, 'recall': tp / (tp + fn) if tp + fn else None}
    # 第 3 项
    segs = [s['id'] for s in items['segs']]
    human = {c: labels['seg'][c]['idea'] for c in segs if c in labels['seg']}
    r1, r2 = got.get('r1', {}), got.get('r2', {})
    firm = [c for c in human if human[c] != 'unsure' and c in r1]
    out['idea'] = {'labelled': len(human), 'unsure': sum(v == 'unsure' for v in human.values()),
                   'human_dist': dict(Counter(human.values())), 'model_dist': dict(Counter(r1[c]['idea_from'] for c in r1)),
                   'model_vs_human': _stat([(human[c], r1[c]['idea_from']) for c in firm]),
                   'model_vs_human_r2': _stat([(human[c], r2[c]['idea_from']) for c in firm if c in r2]),
                   'model_r1_vs_r2': _stat([(r1[c]['idea_from'], r2[c]['idea_from']) for c in segs if c in r1 and c in r2]),
                   'quote_verified': sum(r1[c]['verified'] for c in r1), 'confusion': dict(Counter(
                       f"人={human[c]} 模型={r1[c]['idea_from']}" for c in firm if human[c] != r1[c]['idea_from']))}
    re = [(labels['seg'][c]['idea'], labels['retest'][c]['idea']) for c in items['retest']
          if c in labels['seg'] and c in labels['retest']]
    out['idea']['human_retest'] = _stat(re)
    _save(os.path.join(DIR, 'score.json'), out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == '__main__':
    {'sample': sample, 'judge': judge, 'topics': topics, 'serve': serve, 'score': score}[sys.argv[1]]()
