"""「问」：一个聊天窗口，只凭证据卡（cards.py）回答，每句带出处，点了回到那天那条消息。2026-10-11，照 Verbatim 的 ask.answer。

    answer_deep(question) → 让 Claude（没有就 DeepSeek）自己查（look.py）再答。聊天页和 ask.py 只走这个（10-17 用户去掉了「快速」）
    answer(question)   → 只凭证据卡答；只在两样都没有（只有 Gemini 的电脑）时由 start_ask 退回来用
    问和答都记进 qa_chat（也进 messages，来源 ask，算进日记）
    threads() / current() / new_thread() / history(thread)   一段一段的对话

一轮问答：
  1. 改写（便宜模型）：结合上几轮，把问题改成能单独看懂的一句 + 关键词（中英都要）+ 能看出来的时间范围
  2. 挑卡：时间范围里的卡放得下就全给；不然两路找 —— 按字面（关键词按稀有程度打分）+ 按意思（卡的向量，cards.semantic），
     名次融合，取到预算为止；上一轮引用过的卡一起带上（追问「那后来呢」能接上）；两路都没有但给了时间范围，就沿时间均匀取。
     再带上这些卡最集中的几个窗口的小结（card_convs.summary），只作背景
  3. 回答（默认模型）：只用给的卡，句末标 [c12]；我说的和 AI 说的分清楚；卡里没有就直说没有
  4. 认出处：[c12] → 真的卡 id；不在给定卡里的删掉。cites 存卡的快照（卡重做了旧回答照样能看）
"""
import json
import math
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime

import cards
import days
import index as idx
import jobs
import llm
import localfiles
import memory
import store

BUDGET_CHARS = 45000          # 送给回答那一步的卡，最多这么多字（Claude Opus 写，一问等值约 $0.2；8 万字是 DeepSeek 时放宽的，Claude 要 $0.68）
RRF_K = 60                    # 两路名次融合的常数（RRF 的通用值）
SUMMARY_CHARS = 6000          # 回答时带上相关窗口的小结，最多这么多字
FOLLOW_UP = 30                # 追问时带上上一轮引用过的卡，最多这么多张
HISTORY_TURNS = 6

PLAN_PROMPT = """你在帮一个人查他自己和 AI 的聊天记录。今天是 {today}。
下面是之前几轮对话（可能为空）和他刚问的问题。请输出 JSON：
{{"q": "改写成不看上文也能懂的一句问题", "keywords": ["……"], "since": "YYYY-MM-DD 或 null", "until": "YYYY-MM-DD 或 null"}}
- keywords：8～16 个，用来在聊天记录里按字面搜。包括问题里的名词、它们的同义说法、中英文两种写法（比如「申请」「文书」「essay」「MIT」）。不要太泛的词（「我」「什么」「问题」）。
- since / until：问题里说了时间（「九月」「上周」「去年暑假」）才填，按今天推算；没说就 null。
- 只输出 JSON。

之前的对话：
{history}

刚问的：{question}
"""

# 怎么说（两种问法共用，2026-10-14：按日期一条条列太像行动日志，要更像聊天）
VOICE = """怎么说：
- 像一个把他所有聊天记录都看过、很了解他的朋友在跟他聊：用「你」称呼他，口语，一段一段地说，不要标题、不要编号、不要一行一个日期的列表（除非他明确要列表）。
- 第一句就直接回答他问的，用一两句大白话说出结论；然后挑最能说明问题的两三个时刻讲给他听，讲清楚前后是怎么变的，不用把每一次都列出来。
- 时间说得像人说话：「去年年底」「今年三四月那阵」「八月用上新系统以后」；只有日子本身重要时才说具体哪天。
- 可以引他自己的一两句原话，点到为止。分清「你说的 / 你想的」和「AI 当时说的」。
- 看出了他自己可能没注意到的规律、前后矛盾，可以直说一句，但不评价他好坏、不安慰、不说教，结尾不加「希望对你有帮助」「要不要我…」之类的话。
- 出处照样标，标在那句话的末尾，同一句的出处合在一起，别每几个字就标一次。
- 篇幅跟问题相称：简单的问题两三句就够，大问题也尽量别超过五六段。用中文，除非他用英文问。"""

ANSWER_PROMPT = """你在帮一个人回答关于他自己的问题。今天是 {today}。下面是从他和 AI 的聊天记录里做出来的证据卡，每张卡：
[编号] 日期 时间 谁说的 类型 · 话题 ·《哪个聊天窗口》：要点「原话」
「我」是提问的这个人，「AI」是当时跟他聊天的 AI。《手写日记》的卡出自他自己在 Notion 里写的日记，没有 AI 参与，是他当时最直接的想法。最后还有几个相关窗口的小结，只帮你看懂来龙去脉，不能当出处。

规则：
- 只根据这些卡回答，卡里没有的不要补，不要猜。卡不够回答就直说「卡里没找到」，并说找到了哪些相关的。
- 有依据的话后面标出处，格式 [c12] 或 [c3, c8]，只能用下面出现过的编号。

""" + VOICE + """

之前的对话：
{history}

问题：{question}

证据卡（按时间）：
{cards}

相关窗口的小结（只作背景）：
{summaries}
"""

CITE_RE = re.compile(r'\[(c\d+[^\[\]\n]{0,80})\]')     # [c3]、[c3, c8]、[c3-c6]，后面夹了别的字（[c5， 背景]）也认
_NUM_RE = re.compile(r'c?(\d+)(?:\s*[-–~]\s*c?(\d+))?')


def _like(k):
    k = k.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{k}%'


# 回答里的图（2026-10-17）：```html 代码块，聊天页放进沙盒 iframe 画（static/viz.js）。代码只给页面看：
# 进 messages（日记、搜索）、给模型看的前文、起标题时都换成「（图）」
VIZ_RE = re.compile(r'```html[^\n]*\n.*?(?:```|$)', re.S)


def no_viz(text):
    return VIZ_RE.sub('（图）', text or '')


def _history_text(turns):
    lines = []
    for t in turns:
        one = re.sub(r'\s+', ' ', re.sub(r'\[\[[^\]]+\]\]', '', no_viz(t['text'])))[:600]   # 出处记号不给模型看，不然它照抄卡 id
        lines.append(('问：' if t['role'] == 'user' else '答：') + one)
    return '\n'.join(lines) or '（无）'


def _range(plan):
    rng, args = '', []
    if plan.get('since'):
        rng += ' AND c.date >= ?'; args.append(plan['since'])
    if plan.get('until'):
        rng += ' AND c.date <= ?'; args.append(plan['until'])
    return rng, args


def _keyword_rank(plan, rng, args, total):
    """按字面：每个关键词按稀有程度给分（BM25 的 idf 那一半）→ 卡 id，分高的在前。
    「想法」「变化」「九月」这种一抓几百张的词，以前会把预算塞满、真正讲 ED / EA 的卡反而挤不进来（10-11 实测）。
    超过四分之一的卡都有的词直接不算。"""
    kws = [k.strip() for k in plan.get('keywords') or [] if k and len(k.strip()) >= 2][:16]
    score = {}
    for k in kws:
        hits = store.q(f"""SELECT c.id, (c.text LIKE ? ESCAPE '\\' OR c.topic LIKE ? ESCAPE '\\') AS in_text FROM cards c
                            WHERE (c.text LIKE ? ESCAPE '\\' OR c.quote LIKE ? ESCAPE '\\' OR c.topic LIKE ? ESCAPE '\\'){rng}""",
                       _like(k), _like(k), _like(k), _like(k), _like(k), *args)
        if not hits or (total >= 40 and len(hits) > total / 4):     # 卡太少时比例没意义
            continue
        w = math.log(total / len(hits)) + 0.5
        for r in hits:
            score[r['id']] = score.get(r['id'], 0) + w * (1.5 if r['in_text'] else 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))]


def rank(plan, budget=BUDGET_CHARS):
    """按计划给卡排名次 → 卡 id 列表（最相关的在前）。select 和 look.py（「问 Claude」的查卡工具）共用。
    时间范围里的卡放得下（budget 字以内）就全给；放不下就两路找 —— 按字面（关键词）和按意思（向量），
    两路的名次融合（RRF，照 Verbatim ask.hybrid），谁都不单独说了算。"""
    rng, args = _range(plan)
    total = store.one(f'SELECT COUNT(*) n, COALESCE(SUM(LENGTH(c.text) + LENGTH(c.quote) + 60), 0) size '
                      f'FROM cards c WHERE 1=1{rng}', *args)
    if rng and total['size'] <= budget:
        return [r['id'] for r in store.q(f'SELECT c.id FROM cards c WHERE 1=1{rng}', *args)]
    kw = _keyword_rank(plan, rng, args, total['n'] or 1)
    allowed = {r['id'] for r in store.q(f'SELECT c.id FROM cards c WHERE 1=1{rng}', *args)} if rng else None
    sem = cards.semantic(plan.get('q') or ' '.join(plan.get('keywords') or []), allowed)
    fused = {}
    for ranked in (kw, sem):
        for n, i in enumerate(ranked):
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + n)
    ids = [i for i, _ in sorted(fused.items(), key=lambda x: -x[1])]
    if not ids and rng:                                  # 两路都没有、只说了时间：沿时间均匀取
        allr = [r['id'] for r in store.q(f'SELECT c.id FROM cards c WHERE 1=1{rng} ORDER BY c.date, c.rowid', *args)]
        ids = allr[::max(1, len(allr) // 400)]
    return ids


def card(cid):
    """一张卡，带窗口标题和那条消息的时间。"""
    return store.one(f"""SELECT c.*, {cards.TITLE} AS title, m.ts, m.hhmm FROM cards c LEFT JOIN conversations v ON v.id = c.conv_id
                         LEFT JOIN messages m ON m.id = c.msg_id WHERE c.id=?""", cid)


def select(plan, follow=()):
    """按计划挑卡（rank 的名次，取到预算为止）：返回按时间排好的卡。上一轮引用过的卡排最前（追问接得上）。"""
    ids = rank(plan)
    picked, size = [], 0
    for cid in list(follow) + [i for i in ids if i not in follow]:
        c = card(cid)
        if not c:
            continue
        n = len(c['text']) + len(c['quote']) + 60
        if size + n > BUDGET_CHARS:
            break
        picked.append(c)
        size += n
    return sorted(picked, key=lambda c: (c['date'], c['ts'] or '', c['id']))


def _summaries(picked, limit=SUMMARY_CHARS):
    """挑中的卡最多的那几个窗口的小结（只作背景，不能当出处）。"""
    count = {}
    for c in picked:
        count[c['conv_id']] = count.get(c['conv_id'], 0) + 1
    out, size = [], 0
    for cid, _ in sorted(count.items(), key=lambda x: -x[1]):
        r = store.one(f"SELECT s.summary, {cards.TITLE.replace('c.conv_id', 's.conv_id')} AS title FROM card_convs s "
                      'LEFT JOIN conversations v ON v.id = s.conv_id WHERE s.conv_id=?', cid)
        if not r or not r['summary']:
            continue
        line = f"《{r['title'] or '无标题'}》\n{r['summary'][-1500:]}"
        if size + len(line) > limit:
            break
        out.append(line)
        size += len(line)
    return '\n\n'.join(out) or '（无）'


def _card_line(i, c):
    who = '我' if c['who'] == 'me' else 'AI'
    where = ' · '.join(x for x in (c.get('topic'), f"《{c['title']}》" if c.get('title') else '') if x)
    return f"[c{i}] {c['date']} {c.get('hhmm') or ''} {who} {c['kind']}{' · ' + where if where else ''}：{c['text']}「{c['quote']}」"


def clean(text, alias):
    """[c3, c8] / [c3-c6] → 真的卡 id；不在 alias 里的删掉。返回 (换成 [[id]] 记号的正文, 用到的 id 按出现顺序)。"""
    used = []

    def rep(m):
        ids = []
        for a, b in _NUM_RE.findall(m.group(1)):
            lo, hi = int(a), int(b or a)
            for n in range(lo, min(hi, lo + 30) + 1):          # 范围最多认 30 张，别被「c1-c999」撑爆
                if n in alias and alias[n]['id'] not in ids:
                    ids.append(alias[n]['id'])
        for cid in ids:
            if cid not in used:
                used.append(cid)
        return ''.join(f'[[{i}]]' for i in ids)
    return CITE_RE.sub(rep, text or ''), used


# ---------------------------------------------------------------- 对话（2026-10-13）
# 「问」也当聊天记录 —— 一段一段存着能翻回去看、接着问（以前是一条记录、清空就没了），
# 而且也算进日记：每问一句、每答一句都同时写进 messages（来源 ask，一段对话一个窗口，标题是第一个问题），
# 日记、搜索、窗口页都看得到。做证据卡、找重复窗口、问 Claude 自己的搜原文（look.py）都跳过 ask：
# 「问」的回答本来就是从卡里写的，再做成卡就是卡的复述。
SOURCE = 'ask'


def threads(n=50):
    """以前的对话，置顶的在前、再按最近：id、标题、named（见 SCHEMA）、pinned、几句、最后一次问的时间。"""
    return store.q("""SELECT t.id, t.title, t.named, t.pinned, t.created, t.updated, COUNT(q.id) n FROM qa_threads t
                       LEFT JOIN qa_chat q ON q.thread_id = t.id GROUP BY t.id HAVING n > 0
                       ORDER BY t.pinned DESC, t.updated DESC, t.id DESC LIMIT ?""", n)


# ---------------------------------------------------------------- 标题、置顶（2026-10-16）
# 用户（看着左栏一排「1」「我啥时候做的证据卡」）：「这个的标题」—— 照 claude.ai：答完第一轮起个短标题，也能自己改、能置顶。
# 改的只是标题，问答一句不动（规矩 6）；窗口页上那个窗口（问：…）的标题跟着改。
TITLE_PROMPT = """下面是一段对话的开头。给它起一个简短的标题，像聊天软件左栏里那种：
- 中文，不超过 14 个字，说清楚聊的是什么事，不要标点结尾，不要引号
- 只输出标题本身

{turns}"""


def _set_title(thread, title, named):
    store.run('UPDATE qa_threads SET title=?, named=? WHERE id=?', title, named, thread)
    store.run('UPDATE conversations SET title=? WHERE id=?', f'问：{title}', f'{SOURCE}-{thread}')


def rename(thread, title):
    title = ' '.join((title or '').split())[:60]
    if not title:
        raise ValueError('标题是空的')
    _set_title(thread, title, 2)
    return title


def pin(thread, on=True):
    store.run('UPDATE qa_threads SET pinned=? WHERE id=?', 1 if on else 0, thread)


def autotitle(thread):
    """还没起过标题（named=0）的对话起一个：一次调用、effort low。用户改过的、起过的不再动。起不出来就留着第一个问题。"""
    r = store.one('SELECT named FROM qa_threads WHERE id=?', thread)
    turns = store.q('SELECT role, text FROM qa_chat WHERE thread_id=? AND replaced IS NULL ORDER BY id LIMIT 4', thread)   # 开头几句，不是最近几句
    if not r or r['named'] or not any(t['role'] != 'user' for t in turns):
        return None
    text = '\n\n'.join(('我：' if t['role'] == 'user' else '回答：') + re.sub(r'\[\[[^\]]*\]\]', '', no_viz(t['text']))[:600] for t in turns)
    out = llm.ask(TITLE_PROMPT.format(turns=text), effort='low', purpose='问·起标题')
    title = (out or '').strip().splitlines()[0] if (out or '').strip() else ''
    title = re.sub(r'</?[A-Za-z_][\w-]*[^>]*>', '', title).strip(' "\'“”「」《》#*。.！!？?')    # 10-25：起出过一个「<claude_wrapper>」
    if not title or len(title) > 30:
        return None
    if store.one('SELECT named FROM qa_threads WHERE id=?', thread)['named']:   # 起的这几秒里用户自己改了
        return None
    _set_title(thread, title, 1)
    return title


def autotitle_all():
    """老对话补标题（一段一次调用）。"""
    return {t['id']: autotitle(t['id']) for t in threads(200) if not t['named']}


def new_thread(when=None):
    """when：补录以前的对话时用原来的时间（datetime，带时区）。"""
    now = (when or datetime.now(days.LOCAL_TZ)).strftime('%Y-%m-%dT%H:%M:%S')
    return store.run('INSERT INTO qa_threads (title, created, updated) VALUES (?,?,?)', '', now, now)


def current():
    """正在用的那段对话：最近问过的那段；最新开的那段还没问过也算它。一段都没有就开一段。"""
    r = store.one('SELECT id FROM qa_threads ORDER BY updated DESC, id DESC LIMIT 1')
    return r['id'] if r else new_thread()


def history(n=60, thread=None, versions=False):
    """这段对话最近 n 句。versions=True 连旧版本（replaced）一起给（聊天页翻版本用）；不然只有现在这一版（给模型的前文）。"""
    rows = store.q(f"""SELECT id, ts, role, text, cites, trace, replaced FROM qa_chat
                       WHERE thread_id=? {'' if versions else 'AND replaced IS NULL'} ORDER BY id DESC LIMIT ?""",
                   thread or current(), n)[::-1]
    for r in rows:
        r['cites'] = json.loads(r['cites'] or '[]')
        r['trace'] = json.loads(r['trace']) if r['trace'] else None
    return rows


def clear():
    """「新对话」：开一段新的，以前的留着（能从「以前的对话」里翻回去）。"""
    return new_thread()


def _say(thread, role, text, cites=(), sid=None, trace=None, when=None):
    """记一句：进 qa_chat（界面、追问用），也进 messages（日记、搜索、窗口页用）。trace：深挖怎么查的（步骤、用时），只给界面看。
    when：补录以前的对话（10-18：10-16 在测试版 5056 上问的那几段）时用原来的时间。"""
    now = when or datetime.now(days.LOCAL_TZ)
    qid = store.run('INSERT INTO qa_chat (ts, role, text, cites, sid, thread_id, trace) VALUES (?,?,?,?,?,?,?)',
                    now.strftime('%Y-%m-%dT%H:%M:%S'), role, text, json.dumps(list(cites), ensure_ascii=False), sid, thread,
                    json.dumps(trace, ensure_ascii=False) if trace else None)
    if role == 'user':
        store.run("UPDATE qa_threads SET title = CASE WHEN title = '' THEN ? ELSE title END, updated=? WHERE id=?",
                  ' '.join(text.split())[:40], now.strftime('%Y-%m-%dT%H:%M:%S'), thread)   # 跟 new_thread 同一个格式（不带时区），才比得了先后
    _mirror(qid, thread, role, text, now)
    return qid


def _mirror(qid, thread, role, text, when):
    cid = f'{SOURCE}-{thread}'
    date, hhmm = days.bucket(when)
    plain = re.sub(r'\[\[[^\]]*\]\]', '', no_viz(text)).strip()   # 出处记号、图的代码不进聊天记录
    title = (store.one('SELECT title FROM qa_threads WHERE id=?', thread) or {}).get('title') or '问'
    with store.tx() as db:
        db.execute("""INSERT INTO conversations (id, source, title, title_fallback, first_import, last_import)
                      VALUES (?,?,?,0,'ask','ask') ON CONFLICT(id) DO UPDATE SET title=excluded.title""",
                   (cid, SOURCE, f'问：{title}'))
        # 不用 INSERT OR REPLACE：rowid 会变，全文索引对不上（CLAUDE.md 搜索那一行）
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                   (f'{SOURCE}-{qid}', cid, SOURCE, when.isoformat(), date, hhmm,
                    'human' if role == 'user' else 'assistant', plain, len(plain)))
        db.execute("""UPDATE conversations SET
            first_ts = (SELECT MIN(ts) FROM messages m WHERE m.conv_id = conversations.id),
            last_ts  = (SELECT MAX(ts) FROM messages m WHERE m.conv_id = conversations.id),
            n_msgs   = (SELECT COUNT(*) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human'),
            chars    = (SELECT COALESCE(SUM(chars),0) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human'),
            n_days   = (SELECT COUNT(DISTINCT date) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human')
            WHERE id = ?""", (cid,))


def answer(question, on_text=None, thread=None, model=None, effort='high', again=False):
    """model / effort：聊天页选的（None = 默认 Opus 5.5）。model 管这一次的所有调用，effort 只管写回答那步。
    again：重问没答完的那句（问题已经存着了，不再记一遍）。"""
    with llm.using(model):
        return _answer(question, on_text, thread, effort, again)


def unanswered(thread):
    """这段对话最后一句是问题、后面没有回答（问到一半服务重启了 / 出错了）→ 那句问题；不是 → None。"""
    r = store.one('SELECT role, text FROM qa_chat WHERE thread_id=? AND replaced IS NULL ORDER BY id DESC LIMIT 1', thread)
    return r['text'] if r and r['role'] == 'user' else None


def rewind(thread, edited=None):
    """重新跑（edited=None）/ 改了问题再跑（edited=新问题）：只对这段对话的最后一问（10-17，照 claude.ai）。
    把最后一问的回答（改问题时连问题本身）标成旧版本（replaced），不删 —— 界面上还翻得到，messages 里也还在（规矩 6）。
    → (这次要问的问题, again)：重新跑是 again（问题还用原来那句，不再记一遍）；改问题是新问一句。没有可重跑的 → None。"""
    q = store.one("SELECT id, text FROM qa_chat WHERE thread_id=? AND role='user' AND replaced IS NULL ORDER BY id DESC LIMIT 1", thread)
    if not q:
        return None
    ids = [r['id'] for r in store.q('SELECT id FROM qa_chat WHERE thread_id=? AND replaced IS NULL AND id > ?', thread, q['id'])]
    if edited:
        ids.append(q['id'])
    if ids:
        store.run(f"UPDATE qa_chat SET replaced=1 WHERE id IN ({','.join('?' * len(ids))})", *ids)
    return (edited, False) if edited else (q['text'], True)


def _turns(thread, again):
    """给模型看的前文。重问时最后那句就是这次的问题，不算前文。"""
    turns = history(HISTORY_TURNS * 2 + (1 if again else 0), thread)
    return turns[:-1] if again and turns and turns[-1]['role'] == 'user' else turns


def _answer(question, on_text, thread, effort, again=False):
    question = (question or '').strip()
    if not question:
        raise ValueError('问题是空的')
    thread = thread or current()
    turns = _turns(thread, again)
    htext = _history_text(turns)
    plan = llm.ask_json(PLAN_PROMPT.format(today=days.today(), history=htext, question=question), want=dict,
                         effort='low', purpose='qa-plan') or {}
    plan.setdefault('keywords', [question])
    follow = [c['id'] for t in turns[-1:] if t['role'] == 'assistant' for c in t['cites']][:FOLLOW_UP]
    picked = select(plan, follow)
    if not again:
        _say(thread, 'user', question)
    if not picked:
        text, cites = '卡里没找到跟这个有关的。可能是那段时间的卡还没做（看上面的「证据卡」那一行），或者换个说法再问。', []
    else:
        alias = {i: c for i, c in enumerate(picked, 1)}
        prompt = ANSWER_PROMPT.format(today=days.today(), history=htext, question=plan.get('q') or question,
                                      cards='\n'.join(_card_line(i, c) for i, c in alias.items()),
                                      summaries=_summaries(picked))
        buf = []
        try:
            raw = llm.ask_stream(prompt, lambda x: (buf.append(x), on_text(x)), effort=effort, purpose='qa') if on_text else \
                llm.ask(prompt, effort=effort, purpose='qa')
        except llm.Cancelled:                                       # 写到一半点了停止：留下写出来的那段
            text, used = clean(''.join(buf).strip(), alias)
            by_id = {c['id']: c for c in picked}
            cites = [{k: by_id[i][k] for k in ('id', 'date', 'who', 'kind', 'text', 'quote', 'conv_id', 'msg_id', 'title')} for i in used]
            _say(thread, 'assistant', text, cites)
            return {'answer': text, 'cites': cites, 'stopped': True, 'thread': thread}
        if not raw:
            raise RuntimeError('模型没回（看「窗口」栏里的模型状态）')
        text, used = clean(raw.strip(), alias)
        by_id = {c['id']: c for c in picked}
        cites = [{k: by_id[i][k] for k in ('id', 'date', 'who', 'kind', 'text', 'quote', 'conv_id', 'msg_id', 'title')} for i in used]
    _say(thread, 'assistant', text, cites)
    return {'answer': text, 'cites': cites, 'considered': len(picked), 'plan': plan, 'thread': thread}


# ---------------------------------------------------------------- 问 Claude（深挖，2026-10-13）
# 上面那套是写死的两路找卡 + 一次回答，快（十来秒）、便宜，但只看卡：卡漏掉的（比如「Pipeling」只做了 8 张卡）就答不出来，
# 问得宽还会塞进几百张沾边的卡、答成大杂烩（10-13）。
# 这里让 Claude Code 自己查：只放行 look.py（只读：搜原文 / 搜卡 / 读前后文 / 看某天），它决定先搜什么、读哪段，
# 够了再答，出处可以是卡（[[c:id]]）也可以是原话（[[m:id]]）。追问接着同一个会话（--resume），前面查到的东西走缓存。
# 一问一两分钟、等值几毛钱（订阅额度）。10-17 起是唯一的问法。
DEEP_TTL = 50 * 60                       # 追问在这么久以内就接着上一轮的会话（缓存 1 小时）
HERE = os.path.dirname(os.path.abspath(__file__))
LOOK = f'{sys.executable} {os.path.join(HERE, "look.py")}'
DEEP_PROMPT = """你在帮 {name} 回答一个关于他自己的问题。材料是他和 AI 的全部聊天记录（2025-07 起，几千个窗口）和从里面做出来的证据卡，另外能只读看他电脑上 {roots} 里的文件。今天是 {today}。

查资料只有一个办法：用 Bash 运行 `{look} <命令>`（别的命令都会被拒）。命令：
  msgs  <关键词> [词…] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--who me|ai] [--old] [--n 30]   搜原始聊天（几个词 = 都要出现），默认最新的在前；--old 最早的在前
  cards <问题> [--kw 词,词] [--since …] [--until …] [--n 40]                             搜证据卡（字面 + 按意思）
  read  <消息id> [--before 4] [--after 4] [--at 词] [--full]                            读一条消息和它前后几条；--at 跳到长消息里这个词出现的地方
  conv  <窗口id>                                                                         窗口小结 + 每条一行的目录
  convs <词> [词…] [--n 25]                                                              哪些窗口聊过（几个词任一出现），每个窗口带「你打的 / AI 写的」字数
  sum   <窗口id> [窗口id…]                                                               把挑出来的窗口字数加起来
  day   <YYYY-MM-DD>                                                                     那天有哪些窗口、那天的日记、手写日记
  stats [--since YYYY-MM-DD] [--until YYYY-MM-DD]                                       一共有多少：消息、字数、窗口、按来源、证据卡、日记、按月
  journal <词> [--since …] [--until …]                                                  搜他的手写日记
  diaries [--since …] [--until …] [--full]                                              按日期读 Ephemeris 写的日记，一天一段（看一段时间过得怎样很快）
  schema                                                                                 库里有哪些表、列、各是什么（messages / conversations / cards / diaries / journal / checkins / themes …）
  sql   "<一条 SELECT 或 WITH>" [--n 30]                                                 自己写只读 SQL：统计、加总、分组、按条件筛，能算准数（改不了库，最多 500 行，60 秒）
  files <文件夹> [--glob *.log] [--n 30]                                                 他电脑上的文件夹里有什么（新改的在前）；--glob 往下一层层找文件名
  grep  <词> [词…] --in <文件或文件夹> [--n 30]                                          在文件里搜（同一行几个词都出现，不分大小写）；文件夹越小越快
  file  <文件> [--from 1] [--lines 200] [--at 词]                                        按行读一个文件（一次最多 400 行）：文字文件、.docx、.pptx、有文字层的 PDF 都行；--at 跳到词第一次出现的地方
  moye  <词> [--n 10]                                                                    搜墨页（他的 PDF 转 Markdown 应用）已经转好的文档：扫描件、课件、作业；读全文用 file moye:<id>
  memory [名字]                                                                          读他的档案里的一页（下面「档案」那几页）；不给名字列出有哪些

他的档案（「记忆」：每周从证据卡整理一次，每条带截至哪天和出处）。问题沾到哪一页，先 `{look} memory <名字>` 读那一页，
现在的情况、定下来的事不用从头搜；但档案是整理过的二手话、可能过时：要原话、要准数、要「截至今天」的，照样去查原文核对，
档案和原文对不上以原文为准。引档案里的事照抄它带的 [[c:…]] 记号。没沾到的页别读。
{memory}

怎么查：
- 问总量、规模、一共多少、哪个月聊得最多 → stats（给的是准数，别拿关键词搜索的命中数去估）。
- 问「某一类事一共多少字 / 多少次 / 多少天 / 什么时候最多」（比如某个爱好、某个人、某门课）→ 自己算出准数，别拿他以前随口估的、
  或者别处算过的旧数当答案（那些可以拿来对照）：先用 convs 多换几个说法（--n 100）把候选窗口找全，看标题和小结逐个判断算不算，
  拿不准的用 conv 看一眼；再用 sum 把算进去的加起来（或者直接写 sql 按条件统计）。回答里说清楚算了哪几类窗口、排除了什么、
  「你打的」和「AI 写的」各多少（像某个爱好这种，字主要是 AI 写的）。
- 现成命令不够用，就先 schema 看表，再用 sql 自己查：按天 / 月分组、某段时间、某些窗口、某种卡、打卡和心情……都能算。
  sql 用双引号把整条括起来，里面的字符串用单引号；别用 ; 和 ||；统计时一般排除 source = 'ask'（那是「问」自己）。
- 具体的词、名字、「第一次 / 最早 / 什么时候开始」→ msgs（配 --old）；抽象的、跨很长时间的（状态、想法变化、怎么看某事）→ cards，再用 msgs 补。
- 同一件事换几种说法都搜：中英文、简称、别名（比如「证据卡 / evidence card」「立党 / Lidang」）。
- 关键的地方用 read 看前后文再下结论，别只看片段就猜。注意分清是他说的还是 AI 说的、是他贴进来的还是自己写的。
- 一条消息几千上万字的（贴进来的笔记、网页、旧聊天、写好的日记）：read 默认只给开头 4000 字，命中在后面时 msgs 会写「在第 N 字处」，
  用 read <id> --at <词> 直接跳过去，别用 --full（几万字塞进来很贵，太长还会被截掉）。标「我（N 字长文，可能是贴进来的）」的，读了再判断是不是他自己的话。
- 窗口标题写「〔小结〕…」的是原本没标题的窗口（Gemini 导出的），标题是从小结取的。
- 聊天里提到的文件（日志、文书、代码、导出的东西）要看原文、要原句、要核对数，就去文件里查：路径聊天里常有，先 msgs 找；
  路径带空格就用引号括起来。没有路径就 files 从上面那几个目录往下找（--glob 按文件名），再 grep / file --at 定位，别一次读整个大文件。
  PDF 是扫描件（file 会说没有文字层）就用 moye 搜墨页转好的那份；墨页里也没有就跟他说先在墨页里转，别想别的办法。
  文件是现在的样子，可能后来被改过、覆盖过：拿修改时间对一下是不是聊天里说的那一版，对不上要说。文件只读，看不了的（隐藏文件、密钥、数据库）就别再试。
- 每次只跑一条完整的命令（照上面的写法原样写全路径）：不要用 shell 变量、不要用 ; && | 把几条连起来——那样会被拒。
- 有的问题有专门的做法（技能），先 `{look} skill <名字>` 读全文再照着查（没对上就别读）：
{skills}
- 别怕查得多、想得久：宁可多搜几种说法、多读几段前后文，把事情弄准、弄全再答；要算数的问题更要把数算准。（10-17：思考不要限制）

回答：
- 只根据查到的内容。查不到就直说没查到，并说查了哪些。
- 有依据的话后面标出处：[[m:消息id]]、[[c:卡id]]，或者文件 [[f:路径:行号]] / [[f:路径:起-止]]，照抄工具输出里的记号（文件的行号按 file 输出左边的数），不要编。
- 最后只输出给他看的回答正文，不要讲你是怎么查的。

画图（可选）：
- 问的是趋势、按时间的变化、几样东西的比较、分布，而且你用 sql / stats 真算出了一组数（至少四五个点），可以在回答里放一张图；
  他说「画个图」就一定画。别的情况不画，一个回答最多一两张。图是帮他看明白的，文字照样要把结论说清楚，不能只甩一张图。
- 写法：单独一段，用 ```html 开头、``` 结尾的代码块，里面是一小段 HTML（不要 <html> <head>），宽度跟着容器走（100%）。
  两种画法任选：手写 <svg>（width="100%"，viewBox 宽写 640 左右、字号 12～13，缩放后字才不会太大）；或者 <canvas> 加 <script> 用全局的 Chart（Chart.js 4，已经加载好，
  默认颜色、字体、网格线都按页面调好了，别再设 animation）。
- 数据直接写进代码里，只能是你查出来的数，不许编、不许补；每个点的标签写清楚（月份、日期、名字）。
- 颜色：系列色按顺序用 EPH.colors[0]、EPH.colors[1]…（最多 6 个，不要轮换）；字和线用 CSS 变量 var(--text) / var(--muted) / var(--line)，
  别写死颜色（他的页面有深浅两种）。只有一组数据就不用图例，标题点明是什么。只用一个 y 轴。
- 图在沙盒里跑：不能联网、不能引外部的库 / 字体 / 图片；不要放链接、跳转、表单、src= 引的东西（有这些页面不画）。图的代码里不要标出处。

""" + VOICE + """

之前的对话：
{history}

问题：{question}
"""
# 没有 Claude 时（只有 DeepSeek 的电脑，2026-10-14）深挖走 llm.deepseek_agent：同一套 look.py 命令，包成一个叫 look 的工具
LOOK_CMDS = ('msgs', 'cards', 'read', 'conv', 'convs', 'sum', 'day', 'stats', 'journal', 'diaries', 'schema', 'sql', 'files', 'grep', 'file', 'moye', 'skill', 'memory')
LOOK_TOOL = {'name': 'look', 'description': '查他的聊天记录、证据卡、日记，也能只读看他电脑上的文件（files / grep / file）。command 填一条 look 命令（不带 look 本身），比如 '
             '「msgs 申请 --old」「cards 我那阵子状态怎么样 --since 2026-03-01」「sql "SELECT COUNT(*) FROM messages"」',
             'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': '一条命令'}},
                            'required': ['command']}}


def run_look(args):
    """工具 look：跑一条 look.py 命令，返回它打出来的字。参数用列表传（不经 shell），Windows 上路径带空格也不怕。"""
    cmd = str((args or {}).get('command') or '').strip()
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    while parts and parts[0] not in LOOK_CMDS:              # 它把「python look.py」也写上了：去掉
        parts.pop(0)
    if not parts:
        return '没认出命令。能用的：' + ' / '.join(LOOK_CMDS)
    r = subprocess.run([sys.executable, os.path.join(HERE, 'look.py')] + parts, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=90, cwd=HERE, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    return (r.stdout or '') + (('\n' + r.stderr[-2000:]) if r.returncode else '') or '（没有输出）'


# 技能（10-22：做一个像 Claude skills 的东西给「问」）：skills/<名字>.md，第一行是什么时候用，后面是做法。
# 提示词里只放第一行，对上了它自己 look skill 读全文；平常的问题不多花一个字。加一个技能 = 加一个文件。
SKILLS_DIR = os.path.join(HERE, 'skills')


def skills():
    """→ [(名字, 什么时候用)]"""
    out = []
    for f in sorted(os.listdir(SKILLS_DIR)) if os.path.isdir(SKILLS_DIR) else []:
        if f.endswith('.md'):
            with open(os.path.join(SKILLS_DIR, f), encoding='utf-8') as fh:
                out.append((f[:-3], fh.readline().strip()))
    return out


def skill_text(name):
    """技能全文（去掉第一行）；没有这个技能 → None。"""
    if name not in [n for n, _ in skills()]:
        return None
    with open(os.path.join(SKILLS_DIR, name + '.md'), encoding='utf-8') as fh:
        return fh.read().split('\n', 1)[1].strip()


def _skills_text():
    return '\n'.join(f'  · {n}：{d}' for n, d in skills()) or '  （现在没有）'


def _roots():
    return '、'.join(localfiles.show(r) for r in localfiles.roots())


def deep_prompt_deepseek(**kw):
    """DEEP_PROMPT 的 DeepSeek 版：查资料的办法从「用 Bash 跑 look.py」换成「调工具 look」，别的规矩一样。"""
    return DEEP_PROMPT.format(look='look', **kw) \
        .replace('用 Bash 运行 `look <命令>`（别的命令都会被拒）', '调用工具 look，参数 command 填一条命令（比如 `msgs 申请 --old`）') \
        .replace('（照上面的写法原样写全路径）', '').replace('那样会被拒', '那样跑不了')


FOLLOW_PROMPT = """追问：{question}

（规矩跟刚才一样：追问也照样去查——问的是新的事、或者前面查到的不够，就接着用 look.py 查，别只凭前面的印象答；只根据查到的回答，标 [[m:…]] / [[c:…]]；最后只输出回答正文；还是像聊天那样说，别列流水账；画图也照刚才的规矩。）"""
RESUME_PROMPT = """刚才你查到一半（或者写回答写到一半）断了：网断了或者服务重启了，回答没送到。

前面查到的都还在，接着用；还缺的再查，别从头查一遍。规矩跟刚才一样：只根据查到的回答，标 [[m:…]] / [[c:…]]；最后只输出回答正文（完整的一份，不是只写剩下的）；还是像聊天那样说，别列流水账；画图也照刚才的规矩。"""
CITE_ANY = re.compile(r'\[\[\s*(?:([mc]):([^\]\s]+)|(f):([^\]]+?))\s*\]\]')     # 文件路径里可能有空格


def _deep_cites(text):
    """回答里的 [[m:id]] / [[c:id]] → 出处快照（id 不存在的记号删掉）。返回 (正文, cites)。正文里写出来的 <thinking> 段去掉。"""
    text = no_thinking(text).strip()
    cites, seen = [], {}

    def rep(m):
        kind, ref = m.group(1) or m.group(3), (m.group(2) or m.group(4)).strip()
        key = f'{kind}:{ref}'
        if key not in seen:
            seen[key] = _snapshot(kind, ref)
            if seen[key]:
                cites.append(seen[key])
        return f'[[{key}]]' if seen[key] else ''
    return CITE_ANY.sub(rep, text or ''), cites


def _snapshot(kind, ref):
    if kind == 'f':
        return _file_snapshot(ref)
    if kind == 'c':
        c = card(ref)
        return c and {'id': f'c:{ref}', **{k: c[k] for k in ('date', 'who', 'kind', 'text', 'quote', 'conv_id', 'msg_id', 'title')}}
    m = store.one("""SELECT m.id, m.date, m.role, m.text, m.conv_id, c.title FROM messages m
                      LEFT JOIN conversations c ON c.id = m.conv_id WHERE m.id=?""", ref)
    if not m:
        return None
    t = ' '.join((m['text'] or '').split())
    return {'id': f'm:{ref}', 'date': m['date'], 'who': 'me' if m['role'] == 'human' else 'ai', 'kind': '原话',
            'text': t[:60] + ('…' if len(t) > 60 else ''), 'quote': t[:240] + ('…' if len(t) > 240 else ''),
            'conv_id': m['conv_id'], 'msg_id': m['id'], 'title': m['title']}


def _file_snapshot(ref):
    """f:路径:行号 / f:路径:起-止 → 那几行的快照（文件以后改了、删了，回答照样看得到当时引的）。"""
    m = re.match(r'(.+):(\d+)(?:-(\d+))?$', ref)
    if not m:
        return None
    a = int(m.group(2))
    b = max(a, min(int(m.group(3) or a), a + 40))
    try:
        rows, _ = localfiles.lines(m.group(1), a, b - a + 1)
        name, when = localfiles.where(m.group(1))
    except (localfiles.Denied, OSError, ValueError):
        return None
    if not rows:
        return None
    t = '\n'.join(line for _, line in rows).strip()
    return {'id': f'f:{ref}', 'date': when[:10], 'who': 'file', 'kind': '文件',
            'text': f"第 {a} 行" if a == b else f"第 {a}-{b} 行", 'quote': t[:600] + ('…' if len(t) > 600 else ''),
            'conv_id': None, 'msg_id': None, 'title': name}


STEP_LABEL = {'msgs': '搜原文', 'cards': '翻证据卡', 'read': '读原话前后', 'conv': '看一个窗口', 'convs': '找聊过的窗口',
              'sum': '加字数', 'day': '看那天', 'stats': '看总量', 'journal': '翻手写日记', 'diaries': '读日记',
              'schema': '看库里有哪些表', 'sql': '自己算', 'files': '看文件夹', 'grep': '在文件里搜', 'file': '读文件', 'moye': '搜墨页', 'skill': '读做法', 'memory': '看档案'}
_TABLE_NAME = {'messages': '聊天记录', 'conversations': '窗口', 'cards': '证据卡', 'diaries': '日记', 'journal': '手写日记',
               'checkins': '打卡', 'themes': '主题', 'card_convs': '窗口小结'}


def _flags(args):
    """look 命令的参数 → (正文词, 给人看的条件)。--n / --before 这类只管多少的不说。"""
    words, notes, i = [], [], 0
    while i < len(args):
        a = args[i]
        val = args[i + 1] if i + 1 < len(args) else ''
        if a in ('--since', '--until'):
            notes.append(f"{val}{'起' if a == '--since' else '止'}")
            i += 2
        elif a == '--who':
            notes.append('只看你说的' if val == 'me' else '只看 AI 说的')
            i += 2
        elif a == '--kw':
            words.append(val.replace(',', ' '))
            i += 2
        elif a == '--old':
            notes.append('从最早的看')
            i += 1
        elif a in ('--n', '--before', '--after'):
            i += 2
        elif a.startswith('--'):
            i += 1
        else:
            words.append(a)
            i += 1
    return ' '.join(words), notes


def _title_of(ref, msg):
    """窗口 / 消息 id → 窗口标题 + 日期（id 本身人看不懂）。"""
    r = store.one("""SELECT c.title, m.date FROM messages m JOIN conversations c ON c.id = m.conv_id WHERE m.id=?""", ref) if msg \
        else store.one('SELECT title, substr(first_ts, 1, 10) date FROM conversations WHERE id=?', ref)
    if not r:
        return ''
    return f"《{r['title'] or '无标题'}》" + (f" {r['date']}" if r['date'] else '')


_STRAY_TAG = re.compile(r'</?[A-Za-z_][\w-]*>')
# 10-25：它偶尔把思考当正文写（text 块里一段 <thinking>…</thinking>，没收尾的也算到末尾）。真正的 thinking 块 llm._claude_events 本来就不收
_THINKING = re.compile(r'<thinking>.*?(?:</thinking>|$)|</thinking>', re.S)


def no_thinking(text):
    """去掉正文里的 <thinking>…</thinking> 整段（中途的话、正在写的回答、最终回答都过一遍）。"""
    return _THINKING.sub('', text or '')


def _step(kind, text):
    """Claude 的一步 → 给人看的一步：{k: 命令（界面按它挑图标）, t: 在干什么, d: 细节}。认不出来的返回 None。"""
    if kind != 'tool':
        return None
    cmd = text.split('look.py', 1)[-1].strip()
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    if not parts:
        return None
    verb, args = parts[0], parts[1:]
    if verb == 'sql':                                 # SQL 原文人看不懂：只说在算，带上它查的是哪些表
        tables = list(dict.fromkeys(re.findall(r'\b(?:FROM|JOIN)\s+(\w+)', ' '.join(args), re.I)))
        return {'k': 'sql', 't': '自己算', 'd': '、'.join(_TABLE_NAME.get(t, t) for t in tables[:3]) or '统计'}
    if verb == 'sum':
        return {'k': 'sum', 't': '加字数', 'd': f'{len([a for a in args if not a.startswith("--")])} 个窗口'}
    words, notes = _flags(args)
    if verb == 'read' and words:
        words = _title_of(words.split()[0], True) or words
    elif verb == 'conv' and words:
        words = _title_of(words.split()[0], False) or words
    return {'k': verb if verb in STEP_LABEL else 'look', 't': STEP_LABEL.get(verb, '查'),
            'd': ' · '.join([w for w in [words[:80]] if w] + notes)}


def answer_deep(question, progress=None, on_text=None, thread=None, model=None, effort='max', again=False, fresh=False):
    """fresh：不接上一轮的 Claude 会话（重新跑 / 改问题时：上一轮会话里有被换掉的那版回答）。"""
    with llm.using(model):
        return _answer_deep(question, progress, on_text, thread, effort, again, fresh)


def _answer_deep(question, progress, on_text, thread, effort, again=False, fresh=False):
    """让 Claude 自己查着回答。progress(一行文字) 报进度；on_text(一小段字) 是正在写的回答，on_text(None) = 这段作废、重新开始
    （它查一步说一句再查，每段话开头都会重置，最后留下的就是回答）。追问（DEEP_TTL 以内）接着上一轮的会话。"""
    question = (question or '').strip()
    if not question:
        raise ValueError('问题是空的')
    if not llm.claude_on() and not llm.cheap_on():
        raise RuntimeError('深挖要 Claude 或 DeepSeek，现在两样都用不了')
    thread = thread or current()
    last = store.one("SELECT sid, ts FROM qa_chat WHERE role='assistant' AND thread_id=? AND replaced IS NULL ORDER BY id DESC LIMIT 1", thread)
    sid = last['sid'] if not fresh and last and last['sid'] and \
        time.time() - datetime.fromisoformat(last['ts']).timestamp() < DEEP_TTL else None
    turns = _turns(thread, again)
    # 问题那一行的 sid = 这次查到一半的会话（一开始就记上，答完清掉）。重问时还在 = 上次断了（网断了 / 服务重启了，10-21）：
    # 接着那个会话写，查过的不用再查一遍（那次查了 30 分钟、129 步，写回答时断网）
    if again:
        q = store.one("SELECT id, sid FROM qa_chat WHERE thread_id=? AND role='user' AND replaced IS NULL ORDER BY id DESC LIMIT 1", thread) or {}
        qid, lost = q.get('id'), q.get('sid')
    else:
        qid, lost = _say(thread, 'user', question), None
    buf, steps, held, t0 = [], [], [], time.time()

    def add(step):
        steps.append(step)
        if progress:
            progress(step)

    # 它查的时候说的话（「我先找 TEDx 那段」）也进步骤：一段话写完先压着，后面又跑了命令才算「中途的话」；
    # 压到最后都没跑命令的那段就是回答本身，不进步骤（照 claude.ai：几步之间夹着它说的话）
    def say(kind, text):
        if kind == 'text':
            held[:] = [no_thinking(text)]
        elif kind == 'tool':
            said = re.sub(r'\s+', ' ', CITE_ANY.sub('', held.pop())).strip() if held else ''
            if said and not _STRAY_TAG.fullmatch(said):              # 10-22：它偶尔吐一个「</reasoning_effort>」这种标签当一段话
                add({'k': 'say', 't': said[:600], 'd': ''})
            step = _step(kind, text)
            if step:
                add(step)
        elif kind == 'message':
            buf.clear()
            if on_text:
                on_text(None)
        elif kind == 'delta':
            buf.append(text)
            if on_text:
                on_text(text)
        elif kind == 'session' and qid:
            store.run('UPDATE qa_chat SET sid=? WHERE id=?', text, qid)

    def done(body, cites, stopped=False, sid=None):
        """存下回答，连同这次怎么查的（步骤 + 用时，聊天页回答上面那行「查了 N 步 · 用时」点开看）。"""
        trace = {'steps': steps, 'secs': round(time.time() - t0)}
        _say(thread, 'assistant', body, cites, sid, trace)
        if qid:                                                     # 答上了：之后「重新跑」该从头查，别接这个会话
            store.run('UPDATE qa_chat SET sid=NULL WHERE id=?', qid)
        return {'answer': body, 'cites': cites, 'deep': True, 'thread': thread, 'trace': trace, **({'stopped': True} if stopped else {})}

    if not llm.claude_on():                                         # 只有 DeepSeek：它自己调 look 工具查，没有会话可接（追问靠前面的对话记录）
        prompt = deep_prompt_deepseek(name=idx.who(), today=days.today(), roots=_roots(), skills=_skills_text(), memory=memory.directory(), history=_history_text(turns), question=question)
        try:
            text = llm.deepseek_agent(prompt, LOOK_TOOL, run_look, on_event=say, purpose='qa-deep')
        except llm.Cancelled:
            return done(*_deep_cites(''.join(buf).strip()), stopped=True)
        return done(*_deep_cites(text))
    fresh = DEEP_PROMPT.format(name=idx.who(), today=days.today(), roots=_roots(), skills=_skills_text(), memory=memory.directory(), look=LOOK, history=_history_text(turns), question=question)
    first = RESUME_PROMPT if lost else FOLLOW_PROMPT.format(question=question) if sid else fresh
    try:
        try:
            text, new_sid = llm.claude_agent(first, LOOK, session=lost or sid, on_event=say, purpose='qa-deep', effort=effort)
        except llm.Cancelled:
            raise
        except RuntimeError:
            if not (lost or sid):
                raise
            text, new_sid = llm.claude_agent(fresh, LOOK, on_event=say, purpose='qa-deep', effort=effort)    # 会话接不上：从头问
    except llm.Cancelled:                                           # 写到一半点了停止：留下写出来的那段
        return done(*_deep_cites(''.join(buf).strip()), stopped=True)
    return done(*_deep_cites(text), sid=new_sid)


def _job(thread):
    """每段对话一个位子（10-22）：同一段同一时间只答一个，不同段各答各的。"""
    return f'qa:{thread}'


def can_dig():
    """能不能让模型自己查（深挖）：有 Claude 或 DeepSeek。都没有只能凭卡答。"""
    return llm.claude_on() or llm.cheap_on()


def start_ask(question, thread=None, model=None, effort=None, again=False, fresh=False):
    """后台问（一两分钟；只有 Gemini 时凭卡答，十来秒）。前端 / ask.py 轮询 live()：steps 是查找进度，partial 是正在写的回答，
    result 是写完、出处认好的最终版；问答照样记进 history()。每段对话同一时间只问一个，不同对话可以同时问。"""
    thread = thread or current()

    deep = can_dig()

    def run(job, q, deep):
        job['tid'] = threading.get_ident()
        llm.uncancel()
        def text(piece):                                          # raw 是原样攒的，partial 给人看：去掉写出来的 <thinking> 段
            job['raw'] = '' if piece is None else job['raw'] + piece
            job['partial'] = no_thinking(job['raw'])
        pick = {'model': model, 'effort': effort or 'max', 'again': again}          # 问默认 Max（10-17：思考不要限制）
        job['result'] = answer_deep(q, progress=job['steps'].append, on_text=text, thread=thread, fresh=fresh, **pick) if deep \
            else answer(q, on_text=text, thread=thread, **pick)
        # 第一轮答完起标题：另开一条线，不拖着这次问答收尾
        threading.Thread(target=autotitle, args=(thread,), daemon=True).start()
    return jobs.start(_job(thread), run, question, deep, steps=[], raw='', partial='', result=None, question=question, deep=deep,
                      thread=thread, model=model or (llm.choices() or ((llm.CLAUDE_MODEL, ''),))[0][0], effort=effort or 'max')


def stop(thread=None):
    """停下这段对话正在问的那一个（聊天页的停止按钮）：写出来的那段留在对话里。没在问返回 False。"""
    st = jobs.state(_job(thread or current()))
    if not st.get('running') or not st.get('tid'):
        return False
    llm.cancel(st['tid'])
    return True


def busy():
    """正在答的那几段对话（聊天页左边列表上的小圆点）。"""
    return sorted(int(n[3:]) for n in jobs.names('qa:') if jobs.running(n))


def live(thread=None):
    thread = thread or current()
    return jobs.get(_job(thread), steps=[], partial='', result=None, question=None, deep=False, thread=thread, model=None, effort=None)
