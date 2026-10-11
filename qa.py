"""「问」：一个聊天窗口，只凭证据卡（cards.py）回答，每句带出处，点了回到那天那条消息。2026-10-11，照 Verbatim 的 ask.answer。

    answer(question)   → {answer, cites, cost}；问和答都记进 qa_chat
    history()          → 最近的对话
    clear()

一轮问答：
  1. 改写（便宜模型）：结合上几轮，把问题改成能单独看懂的一句 + 关键词（中英都要）+ 能看出来的时间范围
  2. 挑卡：时间范围内的卡；关键词按命中打分（卡片 trigram 索引，中文子串也搜得到），取到预算为止；
     上一轮引用过的卡一起带上（追问「那后来呢」能接上）；一张都没命中但给了时间范围，就沿时间均匀取
  3. 回答（默认模型）：只用给的卡，句末标 [c12]；我说的和 AI 说的分清楚；卡里没有就直说没有
  4. 认出处：[c12] → 真的卡 id；不在给定卡里的删掉。cites 存卡的快照（卡重做了旧回答照样能看）
"""
import json
import math
import re
from datetime import datetime

import days
import llm
import store

BUDGET_CHARS = 45000          # 送给回答那一步的卡，最多这么多字（约 4.5 万 token，一问一答约 $0.015）
FOLLOW_UP = 30                # 追问时带上上一轮引用过的卡，最多这么多张
HISTORY_TURNS = 6

PLAN_PROMPT = """你在帮一个人查他自己和 AI 的聊天记录。今天是 {today}。
下面是之前几轮对话（可能为空）和他刚问的问题。请输出 JSON：
{{"q": "改写成不看上文也能懂的一句问题", "keywords": ["……"], "since": "YYYY-MM-DD 或 null", "until": "YYYY-MM-DD 或 null"}}
- keywords：8～16 个，用来在聊天记录里按字面搜。包括问题里的名词、它们的同义说法、中英文两种写法（比如「申请」「文书」「essay」「CMU」）。不要太泛的词（「我」「什么」「问题」）。
- since / until：问题里说了时间（「九月」「上周」「去年暑假」）才填，按今天推算；没说就 null。
- 只输出 JSON。

之前的对话：
{history}

刚问的：{question}
"""

ANSWER_PROMPT = """你在帮一个人回答关于他自己的问题。下面是从他和 AI 的聊天记录里做出来的证据卡，每张卡：
[编号] 日期 谁说的 类型：要点「原话」
「我」是提问的这个人，「AI」是当时跟他聊天的 AI。

规则：
- 只根据这些卡回答，卡里没有的不要补，不要猜。卡不够回答就直说「卡里没找到」，并说找到了哪些相关的。
- 每一句有依据的话后面标出处，格式 [c12] 或 [c3, c8]，只能用下面出现过的编号。
- 分清楚是「你」自己说的、想的，还是 AI 当时建议的。用「你」称呼他。
- 有时间先后的，按时间讲清楚变化（以前怎样、后来怎样）。
- 直白，不评价他，不安慰，不说教，不加「希望对你有帮助」之类的话。用中文回答，除非他用英文问。
- 篇幅跟问题相称：简单的问题几句话就够。

之前的对话：
{history}

问题：{question}

证据卡（按时间）：
{cards}
"""

CITE_RE = re.compile(r'\[(c\d+(?:\s*[,，、]\s*c?\d+)*)\]')


def _like(k):
    k = k.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{k}%'


def _history_text(turns):
    lines = []
    for t in turns:
        one = re.sub(r'\s+', ' ', t['text'])[:600]
        lines.append(('问：' if t['role'] == 'user' else '答：') + one)
    return '\n'.join(lines) or '（无）'


def select(plan, follow=()):
    """按计划挑卡：返回按时间排好的卡（dict 列表）。"""
    since, until = plan.get('since'), plan.get('until')
    rng, args = '', []
    if since:
        rng += ' AND c.date >= ?'; args.append(since)
    if until:
        rng += ' AND c.date <= ?'; args.append(until)
    kws = [k.strip() for k in plan.get('keywords') or [] if k and len(k.strip()) >= 2][:16]
    total = store.one(f'SELECT COUNT(*) n FROM cards c WHERE 1=1{rng}', *args)['n'] or 1
    # 按词的稀有程度给分（BM25 的 idf 那一半）：「想法」「变化」「九月」这种一抓几百张的词，
    # 以前会把预算塞满、真正讲 ED / EA 的卡反而挤不进来（10-11 实测）。超过四分之一的卡都有的词直接不算
    score = {}
    for k in kws:
        hits = store.q(f"""SELECT c.id, (c.text LIKE ? ESCAPE '\\') AS in_text FROM cards c
                            WHERE (c.text LIKE ? ESCAPE '\\' OR c.quote LIKE ? ESCAPE '\\'){rng}""",
                       _like(k), _like(k), _like(k), *args)
        if not hits or (total >= 40 and len(hits) > total / 4):     # 卡太少时比例没意义
            continue
        w = math.log(total / len(hits)) + 0.5
        for r in hits:
            score[r['id']] = score.get(r['id'], 0) + w * (1.5 if r['in_text'] else 1)
    ids = [i for i, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))]
    if not ids and (since or until):                     # 只说了时间：沿时间均匀取
        allr = [r['id'] for r in store.q(f'SELECT c.id FROM cards c WHERE 1=1{rng} ORDER BY c.date, c.rowid', *args)]
        step = max(1, len(allr) // 400)
        ids = allr[::step]
    picked, size = [], 0
    for cid in list(follow) + [i for i in ids if i not in follow]:
        c = store.one('SELECT c.*, v.title FROM cards c LEFT JOIN conversations v ON v.id = c.conv_id WHERE c.id=?', cid)
        if not c:
            continue
        n = len(c['text']) + len(c['quote']) + 30
        if size + n > BUDGET_CHARS:
            break
        picked.append(c)
        size += n
    return sorted(picked, key=lambda c: (c['date'], int(c['id'].split('·')[-1])))


def _card_line(i, c):
    who = '我' if c['who'] == 'me' else 'AI'
    return f"[c{i}] {c['date']} {who} {c['kind']}：{c['text']}「{c['quote']}」"


def clean(text, alias):
    """[c3, c8] → 真的卡 id；不在 alias 里的删掉。返回 (换成 [[id]] 记号的正文, 用到的 id 按出现顺序)。"""
    used = []

    def rep(m):
        ids = []
        for part in re.split(r'\s*[,，、]\s*', m.group(1)):
            n = part.lstrip('c')
            if n.isdigit() and int(n) in alias:
                cid = alias[int(n)]['id']
                ids.append(cid)
                if cid not in used:
                    used.append(cid)
        return ''.join(f'[[{i}]]' for i in ids)
    return CITE_RE.sub(rep, text or ''), used


def history(n=60):
    rows = store.q('SELECT id, ts, role, text, cites FROM qa_chat ORDER BY id DESC LIMIT ?', n)[::-1]
    for r in rows:
        r['cites'] = json.loads(r['cites'] or '[]')
    return rows


def clear():
    store.run('DELETE FROM qa_chat')


def answer(question):
    question = (question or '').strip()
    if not question:
        raise ValueError('问题是空的')
    turns = history(HISTORY_TURNS * 2)
    htext = _history_text(turns)
    plan = llm.ask_json(PLAN_PROMPT.format(today=days.today(), history=htext, question=question), want=dict) or {}
    plan.setdefault('keywords', [question])
    follow = [c['id'] for t in turns[-1:] if t['role'] == 'assistant' for c in t['cites']][:FOLLOW_UP]
    picked = select(plan, follow)
    now = datetime.now().isoformat(timespec='seconds')
    store.run('INSERT INTO qa_chat (ts, role, text, cites) VALUES (?,?,?,?)', now, 'user', question, '[]')
    if not picked:
        text, cites = '卡里没找到跟这个有关的。可能是那段时间的卡还没做（看上面的「证据卡」那一行），或者换个说法再问。', []
    else:
        alias = {i: c for i, c in enumerate(picked, 1)}
        raw = llm.ask(ANSWER_PROMPT.format(history=htext, question=plan.get('q') or question,
                                           cards='\n'.join(_card_line(i, c) for i, c in alias.items())))
        if not raw:
            raise RuntimeError('模型没回（看「窗口」栏里的模型状态）')
        text, used = clean(raw.strip(), alias)
        by_id = {c['id']: c for c in picked}
        cites = [{k: by_id[i][k] for k in ('id', 'date', 'who', 'kind', 'text', 'quote', 'conv_id', 'msg_id', 'title')} for i in used]
    store.run('INSERT INTO qa_chat (ts, role, text, cites) VALUES (?,?,?,?)', now, 'assistant', text,
              json.dumps(cites, ensure_ascii=False))
    return {'answer': text, 'cites': cites, 'considered': len(picked), 'plan': plan}
