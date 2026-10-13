"""证据卡（2026-10-11，照 Verbatim 笔记本的路子）：把每天和 AI 的聊天切成一张张「要点 + 逐字原话」的卡，给「问」用。

    build_day(date)     做一天的卡（那天的消息分块送模型，逐张核对原话，存进 cards）
    due(dates)          哪些天还没做、或者做完之后又来了新消息
    estimate(dates)     做这些天大概多少钱
    start(dates)        后台做（jobs：cards），前端轮询 status()
    status()            做了多少天多少张、还差多少天、正在做的进度

一张卡只说一件事：我想的 / 决定的 / 感受 / 计划 / 经历，或者 AI 的建议 / 解释 / 判断。
**原话必须逐字在那条消息里找得到**，找不到的卡直接丢（模型改写过的「原话」不算出处）；核对不花钱。
不做卡的：我粘贴进来的长文（单条超过 PASTE_CHARS 字，多半是文章、日志、别的聊天导出）。
模型：默认走 llm 的省钱路由（DeepSeek V4 Flash，2026-10-12 起）；涉政的那几块、或者没有 DeepSeek 的路时用 gemini-3.1-flash-lite。
卡的 id =「日期·序号」。某天来了新消息（同步晚到）会整天重做，序号会变；问答记录里存了卡的快照，旧回答照样能看。
"""
import hashlib
import json
import re
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime

import index as idx
import jobs
import llm
import store

MODEL = 'gemini-3.1-flash-lite'                    # 只在不走 DeepSeek 时用（涉政、或者没有 DeepSeek 的路）
OUT_RATIO = 0.06                                   # 卡片输出大约是输入的 6%（9 月试跑后按实际改）
PASTE_CHARS = 2000                                 # 我发的单条超过这么多字：当粘贴，不做卡
CHUNK_CHARS = 40000                                # 一次送多少字
MSG_CHARS = 12000                                  # AI 单条回复太长时只看前这么多字
PARALLEL = 4                                       # 一天的几块同时送几块
DAY_PARALLEL = int(idx.env('EPHEMERIS_CARDS_DAYS') or 6)   # 同时做几天（乘 PARALLEL 就是最多几个请求在飞）
KINDS = ('想法', '决定', '感受', '计划', '经历', '事实', '建议', '解释', '判断')
JOB = 'cards'

PROMPT = """下面是 {date} 这一天「我」和 AI 的聊天记录，每条消息前面有编号和是谁说的。
请把其中值得以后回头查的要点做成「证据卡」，输出 JSON 数组，每张卡：
  {{"m": 消息编号, "k": 类型, "t": "一句话要点", "q": "原话"}}

要求：
- 一张卡只说一件事。
- **「我」说的优先，而且要做全**：我说过的每一处有内容的话都做卡——我在想什么、决定了什么、什么感受、
  打算做什么、经历了什么、具体的事实（分数、名字、数字、进度、和谁怎样了）。哪怕很短、很口语、带脏话，只要有内容就做。
- AI 说的也要做：每一条有实质内容的 AI 回复，挑它最重要的 1～3 个建议、解释或判断各做一张
  （我后来可能照着做、或者回头查的）。套话、客套、复述我的话、长篇代码不做。
- k 只能是：{kinds}。
- t：直白的一句话，不超过 50 字。「我」说的用「我……」，AI 说的用「AI ……」开头。不评价、不修饰。
- q：从那条消息里**一字不差地抄**一段（10～80 字），能支撑这张卡。不能改字、不能拼接、不能省略。
- 打招呼、只有一两个字、纯粘贴的文章、代码本身不做卡。宁少勿滥：没什么可记的就少做。
- 只输出 JSON 数组。

聊天记录：
{body}
"""


def _norm(s):
    """比原话用：去掉空白和 Markdown 记号（模型抄的时候会把 **粗体** 的星号丢掉），全角半角统一。
    只是不看格式，字一个都不能差。"""
    s = unicodedata.normalize('NFKC', s or '')
    s = s.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    return re.sub(r'[\s*_`#>]+', '', s)


def _fp(rows):
    return hashlib.sha1('|'.join(f"{r['id']}:{r['chars']}" for r in rows).encode()).hexdigest()[:16]


# 做卡看哪些消息（跟 day_messages 同一个口径，只是不取正文）：我粘贴的长文不要、空消息不要
_KEEP = f"NOT (role = 'human' AND chars > {PASTE_CHARS}) AND chars > 0"


def _day_stats(dates=None):
    """{日期: (指纹, 估算送进去的字数)}，一条 SQL 算完，不取正文（全量正文 7000 多万字，逐天取太慢）。"""
    rows = store.q(f"""SELECT date, id, chars FROM messages WHERE {_KEEP} ORDER BY date, conv_id, ts""")
    out, cur = {}, {}
    for r in rows:
        cur.setdefault(r['date'], []).append(r)
    for d, rs in cur.items():
        if dates is None or d in dates:
            out[d] = (_fp(rs), sum(min(r['chars'], MSG_CHARS) + 40 for r in rs) + len(PROMPT))
    return out


def day_messages(date):
    """做卡要看的消息：那天所有窗口，按时间；我粘贴的长文不要。"""
    return store.q(f"""SELECT m.id, m.conv_id, m.role, m.hhmm, m.text, m.chars, c.title
                         FROM messages m JOIN conversations c ON c.id = m.conv_id
                        WHERE m.date = ? AND {_KEEP.replace('role', 'm.role').replace('chars', 'm.chars')}
                        ORDER BY m.conv_id, m.ts""", date)


def _chunks(rows):
    """按窗口顺序攒块，单块不超过 CHUNK_CHARS（消息已经按窗口排好，同一个窗口的挨在一起）。"""
    out, cur, size = [], [], 0
    for r in rows:
        n = min(len(r['text']), MSG_CHARS) + 40
        if cur and size + n > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        out.append(cur)
    return out


def _body(chunk):
    lines, conv = [], None
    for i, r in enumerate(chunk, 1):
        if r['conv_id'] != conv:
            conv = r['conv_id']
            lines.append(f"\n【窗口：{r['title'] or '无标题'}】")
        who = '我' if r['role'] == 'human' else 'AI'
        lines.append(f"[{i}] {who} {r['hhmm']}：{r['text'][:MSG_CHARS]}")
    return '\n'.join(lines)


def _verify(chunk, raw):
    """模型给的卡 → 核对过的卡。原话找不到、编号不对、类型不对的丢掉。返回 (卡, 丢了几张)。"""
    good, dropped = [], 0
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):                      # 模型偶尔吐一串数字之类的（10-12 全量做卡时把整个任务弄崩过）
            dropped += 1
            continue
        try:
            m = chunk[int(c.get('m')) - 1]
        except (TypeError, ValueError, IndexError):
            dropped += 1
            continue
        q, t, k = (c.get('q') or '').strip(), (c.get('t') or '').strip(), (c.get('k') or '').strip()
        nq = _norm(q)
        if not q or not t or len(nq) < 4:
            dropped += 1
            continue
        if nq not in _norm(m['text']):
            # 原话是对的、编号写错了：这一块里只有一条消息有这句 → 改指那条（照 Verbatim 代码卡「原文唯一但行号错就改行号」）
            others = [x for x in chunk if nq in _norm(x['text'])]
            if len(others) != 1:
                dropped += 1
                continue
            m = others[0]
        good.append({'conv_id': m['conv_id'], 'msg_id': m['id'], 'who': 'me' if m['role'] == 'human' else 'ai',
                     'kind': k if k in KINDS else '事实', 'text': t[:120], 'quote': q[:200]})
    return good, dropped


def build_day(date):
    """做一天的卡（整天重做）。返回 {n, dropped, cost}。没消息就记 0 张。"""
    rows = day_messages(date)

    def one(chunk):
        prompt = PROMPT.format(date=date, kinds=' / '.join(KINDS), body=_body(chunk))
        raw = llm.ask_json(prompt, want=list, tries=2, model=MODEL, fallback=False)   # None = 两次都没拿到；DeepSeek 限流不回 Gemini
        got, bad = _verify(chunk, raw or [])
        pin, pout = llm.prices()
        return got, bad, len(prompt) * pin + len(json.dumps(raw or [], ensure_ascii=False)) * pout, raw is None

    # 一天的几块一起送（一块 4 万字要一两分钟）；结果按块的顺序拼，卡的序号还是按时间
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        results = list(pool.map(one, _chunks(rows)))
    cards = [c for got, _, _, _ in results for c in got]
    dropped = sum(r[1] for r in results)
    cost = sum(r[2] for r in results)
    failed = sum(1 for r in results if r[3])
    now = datetime.now().isoformat(timespec='seconds')
    with store.tx() as db:
        db.execute('DELETE FROM cards WHERE date=?', (date,))
        db.executemany('INSERT INTO cards (id, date, conv_id, msg_id, who, kind, text, quote, made_at) VALUES (?,?,?,?,?,?,?,?,?)',
                       [(f'{date}·{i}', date, c['conv_id'], c['msg_id'], c['who'], c['kind'], c['text'], c['quote'], now)
                        for i, c in enumerate(cards, 1)])
        # 有块没拿到（断线、限流、JSON 坏了）：这天不算做完（指纹记空），下次 due() 还会把它挑出来整天重做
        db.execute('INSERT OR REPLACE INTO card_days (date, fp, n, dropped, cost, made_at) VALUES (?,?,?,?,?,?)',
                   (date, '' if failed else _fp(rows), len(cards), dropped, round(cost, 5), now))
    return {'n': len(cards), 'dropped': dropped, 'cost': cost, 'failed': failed}


def due(dates=None):
    """还没做卡的天，和做完之后又来了消息的天（指纹对不上）。"""
    stats = _day_stats(set(dates) if dates is not None else None)
    made = {r['date']: r['fp'] for r in store.q('SELECT date, fp FROM card_days')}
    wanted = sorted(stats) if dates is None else [d for d in dates if d in stats]
    return [d for d in wanted if made.get(d) != stats[d][0]]


def estimate(dates):
    stats = _day_stats(set(dates))
    pin, pout = llm.prices()
    return round(sum(stats[d][1] for d in dates if d in stats) * (pin + pout * OUT_RATIO), 2)


def _safe_day(date):
    """一天出错只算这一天没做全（下次 due() 还会挑出来），不让整个任务停掉。"""
    try:
        return build_day(date)
    except Exception as e:                                   # noqa: BLE001
        print(f'[cards] {date} 失败：{type(e).__name__}: {e}', flush=True)
        return {'n': 0, 'dropped': 0, 'cost': 0.0, 'failed': 1}


def _run(job, dates):
    """同时做 DAY_PARALLEL 天（每天里面再分 PARALLEL 块同时送）；哪天做完就补下一天进来，不等最慢的那天。"""
    job['total'], job['done'], job['cost'], job['cards'], job['failed_days'] = len(dates), 0, 0.0, 0, 0
    todo, running, streak = list(dates), {}, 0
    with ThreadPoolExecutor(max_workers=DAY_PARALLEL) as pool:
        while todo or running:
            while todo and len(running) < DAY_PARALLEL and not job.get('stop'):
                d = todo.pop(0)
                running[pool.submit(_safe_day, d)] = d
            if not running:
                break
            job['current'] = ' / '.join(sorted(running.values()))
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for f in finished:
                del running[f]
                r = f.result()
                job['done'] += 1
                job['cost'] = round(job['cost'] + r['cost'], 4)
                job['cards'] += r['n']
                job['failed_days'] += bool(r['failed'])
                streak = streak + 1 if r['failed'] else 0
            # 连着好几天都没做成：多半是 DeepSeek 两条路都连不上 / 欠费了。停下，别空跑完剩下几百天
            if streak >= DAY_PARALLEL and len(dates) > 1:
                job['stop'] = True
                for f in running:
                    f.cancel()
                raise RuntimeError('DeepSeek 连着失败（欠费 / 连不上），先停了，没有改用 Gemini；修好再点「做上」接着做')
    return job['done']


def start(dates):
    jobs.start(JOB, _run, list(dates), stop=False, done=0, total=len(dates), cost=0.0, cards=0, failed_days=0)
    return status()


def stop():
    jobs.state(JOB)['stop'] = True


def status():
    s = store.one('SELECT COUNT(*) days, MIN(date) first, MAX(date) last, COALESCE(SUM(n), 0) n, '
                  'COALESCE(SUM(dropped), 0) dropped, COALESCE(SUM(cost), 0) cost FROM card_days')
    todo = due()
    return {**s, 'cost': round(s['cost'], 2), 'todo': len(todo), 'todo_estimate': estimate(todo) if todo else 0,
            'job': jobs.get(JOB, done=0, total=0, cost=0.0, cards=0, current=None)}
