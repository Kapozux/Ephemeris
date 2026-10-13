"""证据卡（2026-10-11，照 Verbatim 笔记本的路子；10-13 改成按窗口做）：把和 AI 的聊天切成一张张「要点 + 逐字原话」的卡，给「问」用。

    due(conv_ids)       哪些窗口要做：没做过 / 做过的那段变了（整窗重做）/ 后面来了新消息（只做新的）
    segments(cid)       → (mode, 段)：这一趟要做的消息，按 SEG_CHARS 切段；prompt(cid, seg) 拼一段的提示词（带前文、窗口小结）
    apply(cid, mode, segs, raws, …)  模型给的结果 → 核对原话 → 写进 cards / card_convs。API 和外面做好的（Claude workflow）都走它
    build_conv(cid)     用 llm 做一个窗口（自动做卡、「做上」按钮走这里）
    due_since(date)     最近有消息、要做的窗口（早上那一串自动做这些）
    make(job, todo)     做一批窗口，做完补向量；start / stop / status 是它的后台版（jobs：cards），前端轮询 status()
    estimate(todo)      做这些大概多少钱
    embed_missing / semantic   卡的向量：补上 / 按意思找（qa 用）

为什么按窗口不按天（10-13 用户）：按天切，一个聊了几天的窗口被切断，「就按刚才那个方案」的上文看不到；
现在一段一段按窗口送，前面带上同一个窗口的上几条当前文（只看不做卡）。卡上照样记那条消息的日期，按天看不受影响。

一张卡只说一件事：我想的 / 决定的 / 感受 / 计划 / 经历，或者 AI 的建议 / 解释 / 判断；带一个话题（p），同一件事跨窗口串得起来。
**原话必须逐字在那条消息里找得到**，找不到的卡直接丢（模型改写过的「原话」不算出处）；核对不花钱。
我发的长文（单条超过 LONG_CHARS 字）标出来，让模型自己分：我自己写的（文书、长段想法）照常做全；
贴进来的别人的文章 / 日志只记一两张「我贴了什么、为什么」。（10-13 之前是一律当粘贴不做卡，我自己写的文书也漏了。）
卡的 id =「消息 id·序号」。重做一条消息会先删掉它的旧卡；问答记录里存了卡的快照，旧回答照样能看。
10-13 之前按天做的卡（id 是「日期·序号」）在 cards_v1 里留了一份。
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
OUT_RATIO = 0.08                                   # 输出大约是输入的这么多（多了话题和小结）
LONG_CHARS = 2000                                  # 我发的单条超过这么多字：标成「长文」，让模型分是自己写的还是贴的
SEG_CHARS = 40000                                  # 一段送多少字
MSG_CHARS = 20000                                  # AI 单条回复太长时只看前这么多字
CTX_MSGS, CTX_CHARS = 8, 8000                      # 每段前面带同一窗口的上几条当前文
PARALLEL = 4                                       # 一个窗口的几段同时送几段
SEG_RETRIES = 2                                    # 没拿到的段，当场再送几轮
CONV_PARALLEL = int(idx.env('EPHEMERIS_CARDS_CONVS') or 12)   # 同时做几个窗口
KINDS = ('想法', '决定', '感受', '计划', '经历', '事实', '建议', '解释', '判断')
JOB = 'cards'

PROMPT = """下面是「我」和 AI 在聊天窗口「{title}」里的一段记录（{span}）。每条消息前有编号、是谁说的、日期时间。
{summary}{context}请把这段里值得以后回头查的要点做成「证据卡」，再用几句话概括这段在聊什么。输出一个 JSON 对象：
  {{"summary": "这段在聊什么", "cards": [{{"m": 消息编号, "k": 类型, "p": "话题", "t": "一句话要点", "q": "原话"}}]}}

要求：
- 一张卡只说一件事。
- **「我」说的优先，而且要做全**：我说过的每一处有内容的话都做卡——我在想什么、决定了什么、什么感受、
  打算做什么、经历了什么、具体的事实（分数、名字、数字、进度、和谁怎样了）。哪怕很短、很口语、带脏话，只要有内容就做。
- AI 说的也要做：每一条有实质内容的 AI 回复，挑它最重要的 1～3 个建议、解释或判断各做一张
  （我后来可能照着做、或者回头查的）。套话、客套、复述我的话、长篇代码不做。
- k 只能是：{kinds}。
- p：这张卡属于哪件事，2～10 字的名词短语（比如「大学申请」「数学论文」「睡眠」「Ephemeris 开发」）。
  同一件事用同一个说法；别用「学习」「生活」「聊天」这种太泛的。
- t：直白的一句话，不超过 60 字，看 t 就能懂（指代要写明：别写「这个方案」，写「把 Purdue 放 EA 的方案」）。
  「我」说的用「我……」，AI 说的用「AI ……」开头。不评价、不修饰。
- q：从那条消息里**一字不差地抄**一段（10～80 字），能支撑这张卡。不能改字、不能拼接、不能省略。
- 标着「长文」的是我发的长消息：先分清是我自己写的（文书、草稿、长段想法），还是贴进来的别人的东西（文章、别人的评论、日志、报错、
  别的聊天导出）。自己写的照常做全；贴进来的只做 1～2 张，记「我贴了什么、为了什么」，q 抄我自己加的那句话或标题。
- 只给带编号的消息做卡。「前文」只是让你看懂上下文，不做卡。
- 打招呼、只有一两个字、代码本身不做卡。没什么可记的就少做。
- summary：2～4 句，写清楚这段在聊哪件事、我的处境 / 态度 / 决定，以及结论。具体，别写「讨论了一些问题」。
- 只输出 JSON 对象。

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


def _size(r):
    return min(r['chars'], MSG_CHARS) + 40


# ---------------------------------------------------------------- 哪些窗口要做

def _all_rows(conv_ids=None):
    """{窗口: [消息（不取正文）]}，按时间。一条 SQL，不取正文（全量正文 7000 多万字）。"""
    rows = store.q('SELECT conv_id, id, ts, chars, role FROM messages WHERE chars > 0 ORDER BY conv_id, ts, id')
    out = {}
    for r in rows:
        if conv_ids is None or r['conv_id'] in conv_ids:
            out.setdefault(r['conv_id'], []).append(r)
    return out


def _plan(rs, made):
    """一个窗口 → (mode, 这趟要做的消息)。mode：full | more | None（不用做）。"""
    if made and made['upto']:
        covered = [r for r in rs if (r['ts'], r['id']) <= (made['upto'], made['upto_id'] or '')]
        if _fp(covered) == made['fp']:
            rest = rs[len(covered):]
            return ('more', rest) if rest else (None, [])
    return 'full', rs


def due(conv_ids=None):
    """→ [(窗口 id, mode)]，老窗口在前。"""
    by = _all_rows(set(conv_ids) if conv_ids is not None else None)
    made = {r['conv_id']: r for r in store.q('SELECT conv_id, fp, upto, upto_id FROM card_convs')}
    out = []
    for cid, rs in by.items():
        mode, todo = _plan(rs, made.get(cid))
        if mode and todo:
            out.append((rs[0]['ts'], cid, mode))
    return [(cid, mode) for _, cid, mode in sorted(out)]


def _rows(ids):
    out = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out += store.q(f"""SELECT id, conv_id, ts, date, hhmm, role, text, chars FROM messages
                           WHERE id IN ({','.join('?' * len(part))})""", *part)
    return out


def segments(conv_id, mode=None, size=SEG_CHARS):
    """→ (mode, 段)：这一趟要做的消息（带正文），切成段（每段不超过 size 字）。
    mode='full' 强制整窗；别的都按库里现在的情况重新判断（排队时判断的可能已经过时了）。"""
    rs = store.q('SELECT conv_id, id, ts, chars, role FROM messages WHERE conv_id=? AND chars > 0 ORDER BY ts, id', conv_id)
    made = store.one('SELECT fp, upto, upto_id FROM card_convs WHERE conv_id=?', conv_id)
    m, todo = _plan(rs, made)
    if mode == 'full':
        m, todo = 'full', rs
    elif m is None:
        return None, []
    full = {r['id']: r for r in _rows([r['id'] for r in todo])}
    segs, cur, n = [], [], 0
    for r in todo:
        r = full[r['id']]
        if cur and n + _size(r) > size:
            segs.append(cur)
            cur, n = [], 0
        cur.append(r)
        n += _size(r)
    if cur:
        segs.append(cur)
    return m, segs


# ---------------------------------------------------------------- 一段的提示词

def _line(r, n=None):
    who = '我' if r['role'] == 'human' else 'AI'
    when = f"{r['date']} {r['hhmm']}"
    if not n:
        return f"[前文] {who} {when}：{r['text'][:2000]}"
    long = f"（长文 {r['chars']} 字）" if r['role'] == 'human' and r['chars'] > LONG_CHARS else ''
    return f"[{n}] {who} {when}{long}：{r['text'][:MSG_CHARS]}" + ('…（后面略）' if r['chars'] > MSG_CHARS else '')


def numbered(seg):
    """段里的消息按编号顺序（编号从 1 起）。"""
    return list(seg)


def prompt(conv_id, seg):
    c = store.one('SELECT title FROM conversations WHERE id=?', conv_id) or {}
    ctx = store.q("""SELECT id, conv_id, ts, date, hhmm, role, text, chars FROM messages
                      WHERE conv_id=? AND chars > 0 AND (ts < ? OR (ts = ? AND id < ?)) ORDER BY ts DESC, id DESC LIMIT ?""",
                  conv_id, seg[0]['ts'], seg[0]['ts'], seg[0]['id'], CTX_MSGS)[::-1]
    lines, n = [], 0
    for r in reversed(ctx):                             # 从紧挨着的往前取，超了就不要更早的
        line = _line(r)
        if n + len(line) > CTX_CHARS and lines:
            break
        lines.insert(0, line)
        n += len(line)
    summ = (store.one('SELECT summary FROM card_convs WHERE conv_id=?', conv_id) or {}).get('summary') or ''
    nums = {r['id']: i for i, r in enumerate(numbered(seg), 1)}
    days = sorted({r['date'] for r in seg})
    return PROMPT.format(
        title=c.get('title') or '无标题', span=days[0] if len(days) == 1 else f'{days[0]} → {days[-1]}',
        summary=f"这个窗口前面聊过的（只作背景）：{summ[-1500:]}\n\n" if summ and ctx else '',
        context=('前文（同一个窗口紧挨着的上几条，只看不做卡）：\n' + '\n'.join(lines) + '\n\n') if lines else '',
        kinds=' / '.join(KINDS), body='\n'.join(_line(r, nums.get(r['id'])) for r in seg))


# ---------------------------------------------------------------- 核对 + 写

def _verify(seg, raw):
    """模型给的卡 → 核对过的卡。原话找不到、编号不对的丢掉。返回 (卡, 丢了几张)。"""
    pool = numbered(seg)
    good, dropped = [], 0
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):                      # 模型偶尔吐一串数字之类的（10-12 全量做卡时把整个任务弄崩过）
            dropped += 1
            continue
        try:
            m = pool[int(c.get('m')) - 1] if int(c.get('m')) >= 1 else None
        except (TypeError, ValueError, IndexError):
            m = None
        q, t, k = (str(c.get('q') or '')).strip(), (str(c.get('t') or '')).strip(), (str(c.get('k') or '')).strip()
        nq = _norm(q)
        if not q or not t or len(nq) < 4:
            dropped += 1
            continue
        if m is None or nq not in _norm(m['text']):
            # 原话是对的、编号写错了：这一段里只有一条消息有这句 → 改指那条（照 Verbatim 代码卡「原文唯一但行号错就改行号」）
            others = [x for x in pool if nq in _norm(x['text'])]
            if len(others) != 1:
                dropped += 1
                continue
            m = others[0]
        good.append({'msg': m, 'who': 'me' if m['role'] == 'human' else 'ai', 'kind': k if k in KINDS else '事实',
                     'topic': (str(c.get('p') or '')).strip()[:30], 'text': t[:160], 'quote': q[:240]})
    return good, dropped


def unpack(raw):
    """模型的输出：{summary, cards}，或者只有卡的数组。→ (卡数组, 小结)；不像样的 → None。"""
    if isinstance(raw, dict) and isinstance(raw.get('cards'), list):
        return raw['cards'], (str(raw.get('summary') or '')).strip()
    if isinstance(raw, list):
        return raw, ''
    return None


def apply(conv_id, mode, segs, raws, cost=0.0, src='api'):
    """一趟的结果写进库。raws[i] 是第 i 段模型的输出（None = 这段没拿到）。
    full：先删掉这个窗口所有的卡；每段写之前删掉这段消息的旧卡（重跑不会重复）。
    做到哪（upto）只推进到第一段失败之前，后面那些下次 due() 还会挑出来。"""
    now = datetime.now().isoformat(timespec='seconds')
    n = dropped = failed = 0
    first_bad = None
    summaries = []
    with store.tx() as db:
        if mode == 'full':
            db.execute('DELETE FROM cards WHERE conv_id=?', (conv_id,))
        for i, (seg, raw) in enumerate(zip(segs, raws)):
            got = unpack(raw)
            if got is None:
                failed += 1
                first_bad = i if first_bad is None else first_bad
                continue
            items, summ = got
            good, bad = _verify(seg, items)
            dropped += bad
            ids = [r['id'] for r in seg]
            db.execute(f"DELETE FROM cards WHERE msg_id IN ({','.join('?' * len(ids))})", ids)
            seq, rows = {}, []
            for c in good:
                m = c['msg']
                seq[m['id']] = seq.get(m['id'], 0) + 1
                rows.append((f"{m['id']}·{seq[m['id']]}", m['date'], conv_id, m['id'], c['who'], c['kind'], c['topic'],
                             c['text'], c['quote'], now))
            db.executemany('INSERT INTO cards (id, date, conv_id, msg_id, who, kind, topic, text, quote, made_at) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
            n += len(rows)
            if first_bad is None and summ:
                summaries.append(f"{seg[0]['date']}：{summ}")
        done = segs if first_bad is None else segs[:first_bad]
        old = db.execute('SELECT fp, upto, upto_id, summary FROM card_convs WHERE conv_id=?', (conv_id,)).fetchone()
        if done:
            last = done[-1][-1]
            covered = db.execute("""SELECT id, chars FROM messages WHERE conv_id=? AND chars > 0
                                     AND (ts < ? OR (ts = ? AND id <= ?)) ORDER BY ts, id""",
                                 (conv_id, last['ts'], last['ts'], last['id'])).fetchall()
            fp, upto, upto_id = _fp([{'id': a, 'chars': b} for a, b in covered]), last['ts'], last['id']
        elif mode == 'more' and old:
            fp, upto, upto_id = old[0], old[1], old[2]
        else:
            fp, upto, upto_id = '', '', ''
        summary = '\n'.join(([old[3]] if mode == 'more' and old and old[3] else []) + summaries)
        total = db.execute('SELECT COUNT(*) FROM cards WHERE conv_id=?', (conv_id,)).fetchone()[0]
        db.execute('INSERT OR REPLACE INTO card_convs (conv_id, fp, upto, upto_id, n, dropped, cost, summary, src, made_at) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?)', (conv_id, fp, upto, upto_id, total, dropped, round(cost, 5), summary, src, now))
    return {'n': n, 'dropped': dropped, 'cost': cost, 'failed': failed}


def build_conv(conv_id, mode=None):
    """用 llm 做一个窗口（这一趟该做的段）。"""
    mode, segs = segments(conv_id, mode)
    if not segs:
        return {'n': 0, 'dropped': 0, 'cost': 0.0, 'failed': 0}

    def one(seg):
        p = prompt(conv_id, seg)
        raw = llm.ask_json(p, want=None, tries=2, model=MODEL, fallback=False,      # DeepSeek 限流不回 Gemini
                           ok=lambda o: unpack(o) is not None)
        pin, pout = llm.prices()
        return raw if unpack(raw) is not None else None, len(p) * pin + len(json.dumps(raw or {}, ensure_ascii=False)) * pout

    # 一个窗口的几段一起送：每段带的前文是原话，用不着等上一段的结果
    # 没拿到的段当场再送两轮：长窗口几十段，一段偶尔超时 / 限流就算整窗没做全，下次从那段起后面全部重送（10-13 实测白花 $2）
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        results = list(pool.map(one, segs))
        for _ in range(SEG_RETRIES):
            bad = [i for i, (r, _) in enumerate(results) if r is None]
            if not bad:
                break
            for i, (r, c) in zip(bad, pool.map(one, [segs[i] for i in bad])):
                results[i] = (r, results[i][1] + c)
    return apply(conv_id, mode, segs, [r for r, _ in results], cost=sum(c for _, c in results), src='api')


# ---------------------------------------------------------------- 后台做

def due_since(date):
    """要做的窗口里，最近（date 以后）还有消息的那些。早上自动做卡只做这些；更早的（多半是导入了旧聊天）留给「做上」按钮，那里先报价。"""
    recent = {r['conv_id'] for r in store.q('SELECT DISTINCT conv_id FROM messages WHERE date >= ?', date)}
    return [(c, m) for c, m in due(recent)]


def estimate(todo):
    """todo：[(窗口, mode)] → 大概多少美元。"""
    by = _all_rows({c for c, _ in todo})
    made = {r['conv_id']: r for r in store.q('SELECT conv_id, fp, upto, upto_id FROM card_convs')}
    chars = 0
    for cid, mode in todo:
        rs = by.get(cid, [])
        rest = _plan(rs, made.get(cid))[1] if mode == 'more' else rs
        chars += sum(_size(r) for r in rest) + len(PROMPT) + CTX_CHARS
    pin, pout = llm.prices()
    return round(chars * (pin + pout * OUT_RATIO), 2)


def _safe(item):
    """一个窗口出错只算这个窗口没做全（下次 due() 还会挑出来），不让整个任务停掉。"""
    cid, mode = item
    try:
        return build_conv(cid, mode)
    except Exception as e:                                   # noqa: BLE001
        print(f'[cards] {cid} 失败：{type(e).__name__}: {e}', flush=True)
        return {'n': 0, 'dropped': 0, 'cost': 0.0, 'failed': 1}


def make(job, todo):
    """同时做 CONV_PARALLEL 个窗口；哪个做完就补下一个进来，不等最慢的那个。"""
    job['total'], job['done'], job['cost'], job['cards'], job['failed'] = len(todo), 0, 0.0, 0, 0
    todo, running, streak = list(todo), {}, 0
    with ThreadPoolExecutor(max_workers=CONV_PARALLEL) as pool:
        while todo or running:
            while todo and len(running) < CONV_PARALLEL and not job.get('stop'):
                item = todo.pop(0)
                running[pool.submit(_safe, item)] = item[0]
            if not running:
                break
            job['current'] = len(running)
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for f in finished:
                del running[f]
                r = f.result()
                job['done'] += 1
                job['cost'] = round(job['cost'] + r['cost'], 4)
                job['cards'] += r['n']
                job['failed'] += bool(r['failed'])
                streak = streak + 1 if r['failed'] else 0
            # 连着好几个窗口都没做成：多半是 DeepSeek 几条路都连不上 / 欠费了。停下，别空跑完剩下的
            if streak >= CONV_PARALLEL and len(todo) > 1:
                job['stop'] = True
                for f in running:
                    f.cancel()
                raise RuntimeError('DeepSeek 连着失败（欠费 / 连不上），先停了，没有改用 Gemini；修好再点「做上」接着做')
    job.update(stage='算向量', current=None)
    try:
        embed_missing(progress=lambda a, b: job.update(vec_done=a, vec_total=b))
    except Exception as e:                                   # noqa: BLE001  向量没算上只是少一路找法，下次再补
        print(f'[cards] 向量没算完：{type(e).__name__}: {e}', flush=True)
    return job['done']


def start(todo):
    jobs.start(JOB, make, list(todo), stop=False, done=0, total=len(todo), cost=0.0, cards=0, failed=0)
    return status()


def stop():
    jobs.state(JOB)['stop'] = True


def status():
    s = store.one('SELECT COUNT(*) n, COUNT(DISTINCT conv_id) convs, MIN(date) first, MAX(date) last FROM cards')
    cost = store.one('SELECT COALESCE(SUM(cost), 0) c FROM card_convs')['c']
    todo = due()
    return {**s, 'cost': round(cost, 2), 'todo': len(todo), 'todo_estimate': estimate(todo) if todo else 0,
            'job': jobs.get(JOB, done=0, total=0, cost=0.0, cards=0, current=None)}


# ---------------------------------------------------------------- 向量（qa 按意思找，2026-10-13，照 Verbatim ask.py）
# 只按字面找：问「那阵子我状态怎么样」，卡里写的是「我好累」「不想动」就找不到。每张卡一个向量，存 card_vecs；
# 卡的内容（话题 / 要点 / 原话）变了指纹对不上就重算那一张。全量约 4 万张，不到一美元（llm.embed：OpenRouter 上 Google 的模型）。
_vec_cache = {}


def _vec_text(c):
    return f"{c['topic'] or ''} | {c['text']} | {c['quote']} | {c['title'] or ''}"[:700]


def _vec_h(c):
    return hashlib.sha1(_vec_text(c).encode()).hexdigest()[:12]


def embed_todo():
    """还没向量、或者内容变了的卡。"""
    have = {r['id']: r['h'] for r in store.q('SELECT id, h FROM card_vecs WHERE model=?', llm.embed_model())}
    # 只给按窗口做过的卡算（10-13 之前按天做的老卡很快会被替掉，算了白花）
    rows = store.q('SELECT c.id, c.topic, c.text, c.quote, v.title FROM cards c LEFT JOIN conversations v ON v.id = c.conv_id '
                   'WHERE c.conv_id IN (SELECT conv_id FROM card_convs)')
    return [r for r in rows if have.get(r['id']) != _vec_h(r)]


def embed_missing(progress=None, parallel=4):
    """补向量；删掉卡已经没了的向量。返回补了几张。"""
    import numpy as np
    model = llm.embed_model()
    store.run('DELETE FROM card_vecs WHERE id NOT IN (SELECT id FROM cards) OR model <> ?', model)
    todo = embed_todo()
    batches = [todo[i:i + 100] for i in range(0, len(todo), 100)]
    done = [0]

    def one(batch):
        mat = llm.embed([_vec_text(c) for c in batch]).astype(np.float16)
        with store.tx() as db:
            db.executemany('INSERT OR REPLACE INTO card_vecs (id, h, model, vec) VALUES (?,?,?,?)',
                           [(c['id'], _vec_h(c), model, mat[i].tobytes()) for i, c in enumerate(batch)])
        done[0] += 1
        if progress:
            progress(done[0], len(batches))

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        list(pool.map(one, batches))
    return len(todo)


def _vectors():
    """(卡 id 列表, float32 矩阵)；按表的行数和最大 rowid 缓存，有新向量才重读。"""
    import numpy as np
    key = tuple(store.one('SELECT COUNT(*) n, MAX(rowid) m FROM card_vecs WHERE model=?', llm.embed_model()).values())
    if _vec_cache.get('key') != key:
        rows = store.q('SELECT id, vec FROM card_vecs WHERE model=?', llm.embed_model())
        mat = (np.frombuffer(b''.join(r['vec'] for r in rows), dtype=np.float16).reshape(len(rows), llm.EMBED_DIM)
               .astype(np.float32) if rows else np.zeros((0, llm.EMBED_DIM), np.float32))
        _vec_cache.update(key=key, ids=[r['id'] for r in rows], mat=mat)
    return _vec_cache['ids'], _vec_cache['mat']


def semantic(query, allowed=None, k=400):
    """按意思找：→ [卡 id]，最像的在前。allowed：只在这些卡里找（时间范围）。没向量 / 调不通返回 []。"""
    import numpy as np
    ids, mat = _vectors()
    if not ids:
        return []
    try:
        qv = llm.embed([query], task='RETRIEVAL_QUERY')[0]
    except Exception as e:                                   # noqa: BLE001  没 Gemini key / 断网：只按字面找
        print(f'[cards] 按意思找没成：{type(e).__name__}: {e}', flush=True)
        return []
    sims = mat @ qv
    if allowed is not None:
        mask = np.array([i in allowed for i in ids])
        sims = np.where(mask, sims, -2)
    top = np.argsort(-sims)[:k]
    return [ids[i] for i in top if sims[i] > -2]
