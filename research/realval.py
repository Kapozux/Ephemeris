"""真实数据验证：Ephemeris 的证据卡「原话逐字核对」在一个人的真实聊天上表现如何（只检验工具，不研究这个人）。

    ../venv/bin/python -m research.realval copy                 只读地把 chats.db 拷到 data/real/chats.db（之后全在拷贝上）
    ../venv/bin/python -m research.realval drops                现有做卡记录里的丢弃率，按平台 / 语言 / 季度 / 路线
    ../venv/bin/python -m research.realval remake claude|cheap  抽 N 个窗口（固定种子），用那条路线重新做卡，丢掉的卡留下来
    ../venv/bin/python -m research.realval report               汇总成只有数字的 md

丢掉的卡分四种（cards._verify 记的原因）：不是卡 / 空或太短 / 原话在好几条消息里但编号不对 / 哪条都找不到；
「哪条都找不到」的再分：模糊匹配（SequenceMatcher，最好的一段 ≥ FUZZY）找得到 = 改写（照 Bakharia 2025）；
原话跟窗口不是一种语言 = 多半是翻译；剩下的 = 找不到原文（改述还是编造，自动分不清，要人看）。
实测（10-28）相似度是两团：≥ 0.9 和 ≤ 0.75，中间没有，所以阈值不敏感。
重新做卡只用每个窗口的第一段，并且一段最多 REMAKE_CHARS 字（Ephemeris 平时是 cards.SEG_CHARS = 4 万），控制花费、
不撞 Claude 的 5 小时上限。两条路线用同一批窗口，可以配对比较。
抽样、卡片原文都只在 data/real/（不进仓库）；对外只给数字。
"""
import difflib
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import research

HERE = os.path.dirname(research.HERE)
REAL = os.path.join(research.DATA, 'real')
COPY = os.path.join(REAL, 'chats.db')
SEED = 20261028
N = 100
FUZZY = 0.9
REMAKE_CHARS = 12000
# 自检的「研究方案」：只写允许哪家（两条路线各一份，配对比较）。10-28 那次跑的时候还没有 route：
# claude 是本机 Opus 5.5；cheap 是 llm 的省钱路线，在百炼 / DeepSeek 官方 / OpenRouter 三家 DeepSeek 之间轮流
STUDIES = {'claude': {'study': 'self-validation', 'models': ['claude:claude-opus-5-5']},
           'cheap': {'study': 'self-validation', 'models': ['deepseek:deepseek-v4-flash']}}
EXCLUDE = ('ask',)            # Ephemeris 自己的问答；手写日记（journal- 开头）不是聊天，也不算
CJK = re.compile(r'[一-鿿]')
LATIN = re.compile(r'[A-Za-z]')


def copy():
    os.makedirs(REAL, exist_ok=True)
    src = sqlite3.connect(f"file:{os.path.join(HERE, 'chats.db')}?mode=ro", uri=True)
    dst = sqlite3.connect(COPY)
    src.backup(dst)
    dst.execute('DELETE FROM app_lock') if dst.execute("SELECT 1 FROM sqlite_master WHERE name='app_lock'").fetchone() else None
    dst.commit()
    print('->', COPY)


def _db():
    return sqlite3.connect(f'file:{COPY}?mode=ro', uri=True)


def windows(db):
    """做过卡的聊天窗口 → {id: {source, lang, quarter, route, n, dropped}}。"""
    out = {}
    q = (f"SELECT c.conv_id, v.source, c.n, c.dropped, c.src FROM card_convs c JOIN conversations v ON v.id = c.conv_id "
         f"WHERE v.source NOT IN ({','.join('?' * len(EXCLUDE))}) AND c.conv_id NOT LIKE 'journal-%'")
    for cid, source, n, dropped, route in db.execute(q, EXCLUDE):
        out[cid] = {'source': source, 'n': n or 0, 'dropped': dropped or 0, 'route': route}
    cjk, latin, first = Counter(), Counter(), {}
    for cid, text, date in db.execute('SELECT conv_id, text, date FROM messages WHERE text IS NOT NULL ORDER BY ts'):
        if cid in out:
            cjk[cid] += len(CJK.findall(text))
            latin[cid] += len(LATIN.findall(text))
            first.setdefault(cid, date)
    for cid, w in out.items():
        w['lang'] = 'zh' if cjk[cid] >= latin[cid] / 4 else 'en'      # 英文按字母数，汉字一个顶一个词：粗分
        d = first.get(cid) or '0000-00'
        w['quarter'] = f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}" if d[5:7].isdigit() else '?'
    return out


def drops():
    ws = windows(_db())
    rows = {}
    for key in ('route', 'source', 'lang', 'quarter'):
        agg = defaultdict(Counter)
        for w in ws.values():
            agg[w[key]]['windows'] += 1
            agg[w[key]]['kept'] += w['n']
            agg[w[key]]['dropped'] += w['dropped']
        rows[key] = {k: dict(v, rate=v['dropped'] / (v['kept'] + v['dropped']) if v['kept'] + v['dropped'] else None)
                     for k, v in sorted(agg.items())}
    tot = Counter()
    for w in ws.values():
        tot['windows'] += 1
        tot['kept'] += w['n']
        tot['dropped'] += w['dropped']
    rows['all'] = dict(tot, rate=tot['dropped'] / (tot['kept'] + tot['dropped']))
    with open(os.path.join(REAL, 'drops.json'), 'w') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(json.dumps(rows['all'], ensure_ascii=False))
    for key in ('route', 'source', 'lang', 'quarter'):
        for k, v in rows[key].items():
            print(f"  {key}={k}: {v['windows']} 窗口，留 {v['kept']}，丢 {v['dropped']}，丢弃率 {v['rate']:.4f}")
    return rows


def sample(ws, n=N, seed=SEED):
    """按（平台, 季度）分层，轮流从每层抽，直到 n 个：每个平台、每个季度都覆盖到。"""
    rng = random.Random(seed)
    strata = defaultdict(list)
    for cid in sorted(ws):
        strata[ws[cid]['source'], ws[cid]['quarter']].append(cid)
    for v in strata.values():
        rng.shuffle(v)
    keys = sorted(strata)
    out = []
    while len(out) < n and any(strata.values()):
        for k in keys:
            if strata[k] and len(out) < n:
                out.append(strata[k].pop())
    return out


def partial(q, text):
    """原话跟消息里最像的那一段有多像（0–1），照 fuzzywuzzy 的 partial_ratio。"""
    if not q:
        return 0.0
    if len(q) >= len(text):
        return difflib.SequenceMatcher(None, q, text, autojunk=False).ratio()
    best = 0.0
    for i, j, size in difflib.SequenceMatcher(None, q, text, autojunk=False).get_matching_blocks():
        if not size:
            continue
        start = max(0, j - i)
        best = max(best, difflib.SequenceMatcher(None, q, text[start:start + len(q)], autojunk=False).ratio())
    return best


def _one(cid, route):
    import cards
    import llm
    from research import route as rt
    mode, segs = cards.segments(cid, 'full', size=REMAKE_CHARS)
    if not segs:
        return cid, {'error': 'no segments'}
    seg = segs[0]
    p = cards.prompt(cid, seg)
    try:
        raw = llm.parse_json(rt.call(STUDIES[route], p, 'research-realval', effort='medium', json_mode=route != 'claude'), None)
    except Exception as e:                                   # noqa: BLE001  一个窗口失败只记这个窗口
        return cid, {'error': f'{type(e).__name__}: {e}'[:200]}
    got = cards.unpack(raw)
    if got is None:
        return cid, {'error': 'unparseable'}
    log = []
    good, _ = cards._verify(seg, got[0], log)
    texts = [cards._norm(r['text']) for r in seg]
    for d in log:
        if d['why'] == 'not_found':
            nq = cards._norm(str(d['card'].get('q') or ''))
            d['fuzzy'] = max((partial(nq, t) for t in texts), default=0.0)
    return cid, {'raw': len(got[0]), 'kept': len(good), 'drops': log, 'msgs': len(seg)}


def remake(route, n=N):
    os.environ['DIARY_DB'] = COPY          # cards 读消息、llm 记花费都在拷贝上
    os.environ['EPHEMERIS_NO_USAGE'] = '1'
    import store
    assert os.path.abspath(store.DB_PATH) == os.path.abspath(COPY), store.DB_PATH
    ws = windows(_db())
    picked = sample(ws, n)
    with open(os.path.join(REAL, 'sample.json'), 'w') as f:
        json.dump({'seed': SEED, 'n': n, 'windows': picked}, f, indent=1)
    path = os.path.join(REAL, f'remake_{route}.json')
    done = json.load(open(path)) if os.path.exists(path) else {}
    todo = [c for c in picked if c not in done or 'error' in done[c]]
    with ThreadPoolExecutor(4 if route == 'claude' else 8) as pool:
        for i, (cid, r) in enumerate(pool.map(lambda c: _one(c, route), todo), 1):
            done[cid] = dict(r, **{k: ws[cid][k] for k in ('source', 'lang', 'quarter')})
            if i % 10 == 0 or i == len(todo):
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(done, f, ensure_ascii=False, indent=1)
                print(route, f'{i}/{len(todo)}', flush=True)
    errs = sum('error' in done[c] for c in picked)
    print(f'{route}: {len(picked) - errs} 个窗口做好，{errs} 个失败 -> {path}')


def lang(text):
    return 'zh' if len(CJK.findall(text)) >= len(LATIN.findall(text)) / 4 else 'en'


def classify(r):
    """丢掉的卡 → rewritten 改了措辞（模糊匹配 ≥ FUZZY）/ lang_mismatch 原话跟窗口不是一种语言（多半是模型翻译了）/
    unmatched 同语言也找不到（改述还是编造，自动分不清，要人看）/ 其余照 cards._verify 的原因。"""
    c = Counter()
    for d in r.get('drops', []):
        if d['why'] != 'not_found':
            c[d['why']] += 1
        elif d.get('fuzzy', 0) >= FUZZY:
            c['rewritten'] += 1
        elif lang(str(d['card'].get('q') or '')) != r['lang']:
            c['lang_mismatch'] += 1
        else:
            c['unmatched'] += 1
    return c


def summarize(route):
    path = os.path.join(REAL, f'remake_{route}.json')
    if not os.path.exists(path):
        return None
    picked = json.load(open(os.path.join(REAL, 'sample.json')))['windows']
    done = json.load(open(path))
    rs = [done[c] for c in picked if c in done and 'error' not in done[c]]
    out = {'windows': len(rs), 'failed': len(picked) - len(rs)}
    groups = {'all': lambda r: 'all', 'source': lambda r: r['source'], 'lang': lambda r: r['lang'],
              'quarter': lambda r: r['quarter']}
    for name, key in groups.items():
        agg = defaultdict(Counter)
        for r in rs:
            g = agg[key(r)]
            g['windows'] += 1
            g['raw'] += r['raw']
            g['kept'] += r['kept']
            g.update(classify(r))
        out[name] = {k: dict(v) for k, v in sorted(agg.items())}
    return out


def report():
    d = json.load(open(os.path.join(REAL, 'drops.json')))
    s = {r: summarize(r) for r in ('claude', 'cheap')}
    with open(os.path.join(REAL, 'remake_summary.json'), 'w') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    return d, s


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'copy':
        copy()
    elif cmd == 'drops':
        drops()
    elif cmd == 'remake':
        remake(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else N)
    elif cmd == 'report':
        print(json.dumps(report()[1], ensure_ascii=False, indent=1))
