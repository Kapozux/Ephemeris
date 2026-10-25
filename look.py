"""「问 Claude」的查资料工具（2026-10-13）：只读，Claude Code 只被允许跑这一个命令（qa.answer_deep）。人也能在终端直接用。

python look.py msgs  <关键词> [词…] [--since D] [--until D] [--who me|ai] [--old] [--n 30]   搜原始聊天（几个词 = 都要出现；默认最新的在前，--old 最早的在前）
python look.py cards <问题>   [--kw 词,词] [--since D] [--until D] [--n 40]           搜证据卡（字面 + 按意思，qa.rank）
python look.py read  <消息id> [--before 4] [--after 4] [--at 词] [--full]             读一条消息和它前后几条；长消息用 --at 跳到词出现的地方
python look.py conv  <窗口id>                                                          窗口小结 + 每条消息一行的目录
python look.py convs <词> [词…] [--n 25]                                              哪些窗口聊过（任一个词；标题、小结、正文），带每个窗口的字数
python look.py sum   <窗口id> [窗口id…]                                                 把这些窗口的字数加起来（你打的 / AI 写的）
python look.py journal <词> [--since D] [--until D]                                     搜他的手写日记
python look.py diaries [--since D] [--until D] [--full]                                 按日期读 Ephemeris 写的日记（一天一段）
python look.py schema                                                                  库里有哪些表、哪些列、各是什么
python look.py sql   "<SELECT …>" [--n 30]                                              自己写只读 SQL（统计、加总、分组、筛选；改不了库）
python look.py day   <YYYY-MM-DD>                                                      那天有哪些窗口、那天的日记、手写日记
python look.py stats [--since D] [--until D]                                           一共有多少：消息、字数、窗口、按来源、证据卡、日记、按月
python look.py files <文件夹> [--glob *.log] [--n 30]                                    电脑上的文件夹里有什么（新改的在前）；--glob 往下一层层找文件名
python look.py grep  <词> [词…] --in <文件或文件夹> [--n 30]                             在文件里搜（同一行里几个词都出现，不分大小写）
python look.py file  <文件> [--from 1] [--lines 200] [--at 词]                           按行读一个文件（文字、.docx、.pptx、有文字层的 PDF；moye:<id> 读墨页转好的）；--at 跳到词第一次出现的地方
python look.py skill [名字]                                                             读「问」的一个技能（skills/<名字>.md：某类问题的专门做法）；不给名字列出有哪些
python look.py memory [名字]                                                            读他的档案（memory.py：每周从证据卡整理的「关于你 / 偏好 / 各领域」）的一页；不给名字列出有哪些
python look.py moye  <词> [--n 10]                                                     搜墨页已经转好的 PDF（扫描件、课件、作业），墨页开着才行

出处记号：消息是 m:<id>，卡是 c:<id>，文件是 f:<路径>:<行号> 或 f:<路径>:<起>-<止>。回答时照抄就能被 Ephemeris 认回原话。
文件只能看白名单目录里的（localfiles：默认 ~/Documents、~/Downloads），只读；隐藏文件、密钥、数据库看不了。
标题写「〔小结〕…」的窗口原本没标题（Gemini 导出的 sess_xxxx），是从窗口小结取的头一句。
「我（N 字长文…）」：他发的超长消息，常是贴进来的：课堂笔记、网页、别人或别的 AI 写的、以前的聊天记录，也可能是他在别处写好的日记——读了再判断是不是他自己的话、说的是哪天的事。
搜原文 / 找窗口不看「问」自己的对话（来源 ask）：不然会引到自己以前的回答。
"""
import argparse
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import localfiles  # noqa: E402
import memory  # noqa: E402
import qa      # noqa: E402
import store   # noqa: E402

WHO = {'human': '我', 'assistant': 'AI'}


def _one_line(text, n):
    t = ' '.join((text or '').split())
    return t if len(t) <= n else t[:n] + '…'


_UNTITLED = None
LONG = 5000          # 他发的超过这么多字，多半是贴进来的
SHOWN = 4000         # read 默认给这条看的字数；命中在这之后要 --at


def _untitled():
    """没标题的窗口（Gemini 导出切出来的 sess_xxxx 等，title_fallback=1）→ 小结头一句（2026-10-18：38% 的窗口没标题）。"""
    global _UNTITLED
    if _UNTITLED is None:
        _UNTITLED = {}
        for r in store.q("""SELECT c.id, s.summary FROM conversations c JOIN card_convs s ON s.conv_id = c.id
                            WHERE c.title_fallback = 1 AND COALESCE(s.summary, '') <> ''"""):
            head = re.sub(r'^\s*\d{4}-\d{2}-\d{2}[^：:，,。]{0,12}[：:，,]\s*', '', r['summary'])
            head = re.split(r'[。；\n]', re.sub(r'^这段(聊天)?(是)?在(聊)?', '', head), maxsplit=1)[0]
            _UNTITLED[r['id']] = '〔小结〕' + _one_line(head, 34)
    return _UNTITLED


def _title(t, n=40, cid=None):
    if cid and cid in _untitled():
        return _untitled()[cid]
    return _one_line(t or '无标题', n)


def _who(r):
    if r['role'] == 'human' and (r['chars'] or 0) > LONG:
        return f"我（{r['chars']} 字长文，可能是贴进来的）"
    return WHO.get(r['role'], r['role'])


def _around(text, kw, width=70):
    t = ' '.join((text or '').split())
    i = t.lower().find(kw.lower())
    if i < 0:
        return _one_line(t, width * 2)
    a = max(0, i - width)
    return ('…' if a else '') + t[a:i + len(kw) + width] + ('…' if i + len(kw) + width < len(t) else '')


def _rng(a, col='m.date'):
    sql, args = '', []
    if a.since:
        sql += f' AND {col} >= ?'; args.append(a.since)
    if a.until:
        sql += f' AND {col} <= ?'; args.append(a.until)
    return sql, args


def msgs(a):
    kws = [w for w in a.words if w] or ['']
    kw = kws[0]                                       # 片段围着第一个词截
    like = ''.join(' AND m.text LIKE ?' for _ in kws)
    pats = [f'%{k}%' for k in kws]
    rng, args = _rng(a)
    who = {'me': " AND m.role='human'", 'ai': " AND m.role='assistant'"}.get(a.who or '', '')
    rows = store.q(f"""SELECT m.id, m.conv_id, m.date, m.hhmm, m.role, m.text, m.chars, c.title FROM messages m
                        JOIN conversations c ON c.id = m.conv_id
                       WHERE 1=1{like}{rng}{who} AND m.source <> 'ask' ORDER BY m.ts {'ASC' if a.old else 'DESC'} LIMIT ?""",
                   *pats, *args, a.n)
    total = store.one(f"SELECT COUNT(*) n FROM messages m WHERE 1=1{like}{rng}{who} AND m.source <> 'ask'", *pats, *args)['n']
    print(f'「{" + ".join(kws)}」原文命中 {total} 条，列出 {len(rows)} 条（{"最早" if a.old else "最新"}的在前；同一窗口连着的只写一次标题）')
    last = None
    for r in rows:
        if r['conv_id'] != last:
            print(f"《{_title(r['title'], cid=r['conv_id'])}》")
            last = r['conv_id']
        i = (r['text'] or '').lower().find(kw.lower())
        far = f"（在第 {i} 字处，共 {r['chars']} 字：read {r['id']} --at {kw}）" if i > SHOWN - 300 else ''
        print(f"  [m:{r['id']}] {r['date']} {r['hhmm']} {_who(r)} {_around(r['text'], kw)}{far}")


def cards_(a):
    q = ' '.join(a.words)
    plan = {'q': q, 'keywords': [k for k in (a.kw or '').split(',') if k] or q.split(), 'since': a.since, 'until': a.until}
    ids = qa.rank(plan, budget=a.n * 200)[:a.n]
    rows = sorted(filter(None, map(qa.card, ids)), key=lambda c: (c['date'], c['ts'] or ''))
    print(f'证据卡 {len(rows)} 张（按时间；同一窗口连着的只写一次标题）')
    last = None
    for c in rows:
        if c['conv_id'] != last:
            print(f"《{_title(c['title'], cid=c['conv_id'])}》")
            last = c['conv_id']
        print(f"  [c:{c['id']}] {c['date']} {c['hhmm'] or ''} {'我' if c['who'] == 'me' else 'AI'} {c['kind']} · "
              f"{c['topic'] or ''} {c['text']}「{c['quote']}」 (原话 m:{c['msg_id']})")


def read(a):
    mid = a.words[0].removeprefix('m:')
    if mid.startswith('journal-'):                      # 手写日记做的卡：原话在那页日记里（cards.JOURNAL）
        j = store.one('SELECT date, title, text, chars FROM journal WHERE page_id=?', mid.removeprefix('journal-').split('/')[0])
        if not j:
            print('没有这页日记'); return
        cap = 100000 if a.full else 8000
        print(f"手写日记 {j['date']}《{j['title'] or ''}》（{j['chars']} 字）\n{(j['text'] or '')[:cap]}"
              f"{'…（后面略，--full 看全文）' if len(j['text'] or '') > cap else ''}")
        return
    m = store.one('SELECT conv_id, ts, id FROM messages WHERE id=?', mid)
    if not m:
        print('没有这条消息'); return
    t = _title(store.one('SELECT title FROM conversations WHERE id=?', m['conv_id'])['title'], 80, m['conv_id'])
    if a.at:
        return _read_at(mid, t, m['conv_id'], a.at)
    before = store.q('SELECT * FROM messages WHERE conv_id=? AND (ts < ? OR (ts = ? AND id < ?)) ORDER BY ts DESC, id DESC LIMIT ?',
                     m['conv_id'], m['ts'], m['ts'], m['id'], a.before)[::-1]
    after = store.q('SELECT * FROM messages WHERE conv_id=? AND (ts > ? OR (ts = ? AND id > ?)) ORDER BY ts, id LIMIT ?',
                    m['conv_id'], m['ts'], m['ts'], m['id'], a.after)
    this = store.one('SELECT * FROM messages WHERE id=?', mid)
    print(f"《{t}》（窗口 {m['conv_id']}）")
    for r in before + [this] + after:
        cap = 100000 if a.full else (SHOWN if r['id'] == mid else 800)
        mark = ' ◀ 这条' if r['id'] == mid else ''
        more = '…（后面略：要找里面某个词用 --at 词，--full 看全文）' if r['id'] == mid else '…（后面略）'
        print(f"\n[m:{r['id']}] {r['date']} {r['hhmm']} {_who(r)}{mark}（{r['chars']} 字）\n"
              f"{(r['text'] or '')[:cap]}{more if len(r['text'] or '') > cap else ''}")


def _read_at(mid, title, cid, word, width=800, most=5):
    """长消息（几万字的笔记、贴进来的聊天记录）里跳到 word 出现的地方，每处前后 width 字（10-18：四分之一的命中在第 4000 字之后，
    默认读不到；--full 一次几万字，还会被截断）。"""
    r = store.one('SELECT * FROM messages WHERE id=?', mid)
    text, low, w = r['text'] or '', (r['text'] or '').lower(), word.lower()
    hits, i = [], low.find(w)
    while i >= 0:
        hits.append(i)
        i = low.find(w, i + 1)
    print(f"《{title}》（窗口 {cid}）\n[m:{mid}] {r['date']} {r['hhmm']} {_who(r)}（{r['chars']} 字）「{word}」出现 {len(hits)} 处"
          + (f"，列前 {most} 处" if len(hits) > most else ''))
    shown_to = -1
    for i in hits:
        if i < shown_to or most == 0:
            continue
        a, b = max(0, i - width), min(len(text), i + len(word) + width)
        print(f"\n— 第 {i} 字处 —\n{'…' if a else ''}{text[a:b]}{'…' if b < len(text) else ''}")
        shown_to, most = b, most - 1


def conv(a):
    cid = a.words[0]
    c = store.one('SELECT title FROM conversations WHERE id=?', cid)
    if not c:
        print('没有这个窗口'); return
    s = store.one('SELECT summary FROM card_convs WHERE conv_id=?', cid)
    rows = store.q('SELECT id, date, hhmm, role, text, chars FROM messages WHERE conv_id=? AND chars > 0 ORDER BY ts, id', cid)
    print(f"《{_title(c['title'], 80, cid)}》 {len(rows)} 条，{rows[0]['date'] if rows else ''} → {rows[-1]['date'] if rows else ''}")
    if s and s['summary']:
        print('小结：\n' + s['summary'])
    print('\n目录：')
    for r in rows:
        print(f"[m:{r['id']}] {r['date']} {r['hhmm']} {_who(r)}（{r['chars']} 字）{_one_line(r['text'], 70)}")


def _sizes(ids):
    """{窗口: 你打的字、AI 写的字、条数、首末日期}（不算「问」自己的）。"""
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in store.q(f"""SELECT conv_id, SUM(CASE WHEN role='human' THEN chars ELSE 0 END) me,
                                    SUM(CASE WHEN role='assistant' THEN chars ELSE 0 END) ai, COUNT(*) n, MIN(date) a, MAX(date) b
                               FROM messages WHERE conv_id IN ({','.join('?' * len(part))}) AND source <> 'ask' GROUP BY conv_id""", *part):
            out[r['conv_id']] = r
    return out


def convs(a):
    """几个词任一出现（标题、小结、原文）的窗口，命中多的在前；每个带字数，方便挑出某一类再用 sum 加起来。"""
    kws = [w for w in a.words if w]
    hit = ' + '.join(['(SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id AND m.text LIKE ?)'] * len(kws))
    where = ' OR '.join(['c.title LIKE ? OR s.summary LIKE ? OR EXISTS (SELECT 1 FROM messages m WHERE m.conv_id = c.id AND m.text LIKE ?)'] * len(kws))
    pats = [f'%{k}%' for k in kws]
    rows = store.q(f"""SELECT c.id, c.title, s.summary, ({hit}) hits FROM conversations c LEFT JOIN card_convs s ON s.conv_id = c.id
                        WHERE c.source <> 'ask' AND ({where}) ORDER BY hits DESC LIMIT ?""",
                   *pats, *[p for p in pats for _ in range(3)], min(a.n, 300))
    size = _sizes([r['id'] for r in rows])
    print(f'「{" / ".join(kws)}」出现在 {len(rows)} 个窗口（命中多的在前，最多 {min(a.n, 300)} 个；要更多加 --n 300）')
    for r in rows:
        z = size.get(r['id']) or {}
        print(f"{r['id']} 《{_title(r['title'], 60, r['id'])}》 {z.get('a')}→{z.get('b')} 你 {z.get('me') or 0} 字 · AI {z.get('ai') or 0} 字"
              f" 原文命中 {r['hits']} 处｜{_one_line(r['summary'], 160)}")


def sum_(a):
    """把挑出来的窗口加起来：每个一行，再给合计（你打的 / AI 写的分开）。"""
    ids = list(dict.fromkeys(a.words))
    size = _sizes(ids)
    titles = {r['id']: r['title'] for r in store.q(f"SELECT id, title FROM conversations WHERE id IN ({','.join('?' * len(ids))})", *ids)}
    me = ai = n = 0
    for i in ids:
        if i not in titles:
            print(f'没有这个窗口：{i}')
            continue
        z = size.get(i) or {}
        me, ai, n = me + (z.get('me') or 0), ai + (z.get('ai') or 0), n + 1
        print(f"{i} 《{_title(titles[i], 60, i)}》 {z.get('a')}→{z.get('b')} {z.get('n') or 0} 条：你 {z.get('me') or 0} 字 · AI {z.get('ai') or 0} 字")
    print(f'\n合计 {n} 个窗口：你 {me} 字 · AI {ai} 字 · 共 {me + ai} 字')


def day(a):
    d = a.words[0]
    print(f'== {d}')
    for r in store.q("""SELECT c.id, c.title, COUNT(*) n, SUM(m.role='human') mine, MIN(m.hhmm) a, MAX(m.hhmm) b
                          FROM messages m JOIN conversations c ON c.id = m.conv_id WHERE m.date = ?
                         GROUP BY c.id ORDER BY a""", d):
        print(f"{r['id']} 《{_title(r['title'], 60, r['id'])}》 {r['a']}–{r['b']} {r['n']} 条（我 {r['mine']}）")
    dia = store.one('SELECT headline, narrative, mood FROM diaries WHERE date=?', d)
    if dia:
        print(f"\n日记：{dia['headline']}\n{dia['narrative']}" + (f"\n心情：{dia['mood']}" if dia['mood'] else ''))
    for j in store.q("SELECT text FROM journal WHERE date=? AND (gone_at IS NULL OR gone_at = '')", d):
        print('\n手写日记：\n' + (j['text'] or '')[:3000])


def stats(a):
    """总量（2026-10-14：问「你能搜集的数据规模」时，它只能拿「的」字搜一遍猜下限）。不算「问」自己的对话。"""
    rng, args = _rng(a)
    base = f"FROM messages m JOIN conversations c ON c.id = m.conv_id WHERE m.source <> 'ask' AND c.source <> 'ask'{rng}"
    t = store.one(f"""SELECT COUNT(*) n, SUM(m.role='human') me, SUM(m.role='assistant') ai,
                             SUM(CASE WHEN m.role='human' THEN m.chars ELSE 0 END) me_chars, SUM(m.chars) chars,
                             COUNT(DISTINCT m.conv_id) convs, COUNT(DISTINCT m.date) days, MIN(m.date) first, MAX(m.date) last {base}""", *args)
    scope = f"{a.since or '最早'} 到 {a.until or '现在'}" if (a.since or a.until) else '全部'
    print(f"== {scope}（不含「问」自己的对话）")
    print(f"时间：{t['first']} 到 {t['last']}，有聊天的日子 {t['days'] or 0} 天")
    print(f"消息 {t['n'] or 0} 条（你 {t['me'] or 0} · AI {t['ai'] or 0}），共 {t['chars'] or 0:,} 字（你打的 {t['me_chars'] or 0:,} 字，含你贴进来的别人的内容）")
    print(f"窗口 {t['convs'] or 0} 个")
    print('\n按来源：')
    for r in store.q(f"""SELECT c.source, COUNT(DISTINCT m.conv_id) convs, COUNT(*) n, SUM(m.role='human') me {base}
                          GROUP BY c.source ORDER BY n DESC""", *args):
        print(f"  {r['source']}：{r['convs']} 个窗口，{r['n']} 条（你 {r['me']}）")
    crng, cargs = _rng(a, 'date')
    k = store.one(f"SELECT COUNT(*) n, COUNT(DISTINCT conv_id) convs, SUM(who='me') me FROM cards WHERE 1=1{crng}", *cargs)
    print(f"\n证据卡 {k['n'] or 0} 张（来自 {k['convs'] or 0} 个窗口；你说的 {k['me'] or 0} 张）")
    kinds = store.q(f"SELECT kind, COUNT(*) n FROM cards WHERE 1=1{crng} GROUP BY kind ORDER BY n DESC LIMIT 8", *cargs)
    if kinds:
        print('  按种类：' + '、'.join(f"{r['kind']} {r['n']}" for r in kinds))
    d = store.one(f"SELECT COUNT(*) n FROM diaries WHERE 1=1{crng}", *cargs)
    j = store.one(f"SELECT COUNT(*) n, SUM(chars) chars FROM journal WHERE (gone_at IS NULL OR gone_at = ''){crng}", *cargs)
    print(f"日记（Ephemeris 写的）{d['n'] or 0} 篇；手写日记 {j['n'] or 0} 篇，{j['chars'] or 0:,} 字")
    print('\n按月（消息条数 · 你的）：')
    for r in store.q(f"SELECT substr(m.date, 1, 7) mo, COUNT(*) n, SUM(m.role='human') me {base} GROUP BY mo ORDER BY mo", *args):
        print(f"  {r['mo']}  {r['n']} · {r['me']}")


# ---------------------------------------------------------------- 自己写 SQL（10-14）
# 现成命令不够（统计、加总、分组、按条件筛）就让它自己查。只读：库按只读打开，再加一道授权检查，只放行读；输出有上限、查询有时限。
_NOTES = {
    'messages': '每条消息。role = human（他）/ assistant（AI）；source = claude / gemini / chatgpt / cc / cursor / codex / ask（ask 是「问」自己，统计时一般排除）；date 按他的一天（凌晨算前一天）；chars 字数',
    'conversations': '窗口（一段对话）。title 标题；source 同上',
    'cards': '证据卡：从原话抽的要点。who = me / ai；kind 种类；topic 话题；quote 原话；msg_id 出自哪条消息',
    'card_convs': '每个窗口做卡的情况，summary 是这个窗口的小结（按天分段）',
    'diaries': 'Ephemeris 每天写的日记：headline 一句话、narrative 正文、mood、topics',
    'journal': '他自己手写的日记（Notion 拉回来的），gone_at 有值的是 Notion 上删了的',
    'checkins': '打卡：word 心情词、valence 好坏（正好负坏）、note 备注',
    'mood_scores': '每天过得怎么样的打分（valence）',
    'themes': '主题账本：长期在想的事；theme_days 是它出现在哪些天',
    'qa_chat': '「问」的对话记录',
    'memory_topics': '档案（记忆）的一页一页：kind = you / area，off = 1 的不给你看（别查它们的内容）',
    'memory_facts': '档案里的事实：old_by 有值的是换下来的旧版本；by = hand 是他自己写的',
}
_SKIP = ('fts', 'dup', 'backup', 'sync', 'sqlite_', 'cc_files', 'imports', 'lab', 'card_vecs', 'cards_v1', 'flashback', 'daily_runs', 'claude_usage', 'notion_pages')


def schema(a):
    for t in [r['name'] for r in store.q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        if any(x in t for x in _SKIP):
            continue
        cols = ', '.join(r['name'] for r in store.q(f"SELECT name FROM pragma_table_info('{t}')"))
        n = store.one(f'SELECT COUNT(*) n FROM "{t}"')['n']
        print(f"{t}（{n} 行）：{cols}" + (f"\n    {_NOTES[t]}" if t in _NOTES else ''))


def _readonly_auth(action, *_):
    ok = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, 'SQLITE_RECURSIVE', 33))
    return sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY


def sql(a):
    q = ' '.join(a.words).strip().rstrip(';')
    cap = max(1, min(a.n, 500))
    db = sqlite3.connect(f'file:{store.DB_PATH}?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only = ON')
        db.set_authorizer(_readonly_auth)
        deadline = time.time() + 60
        db.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
        try:
            cur = db.execute(q)
            rows = cur.fetchmany(cap + 1)
        except (sqlite3.DatabaseError, sqlite3.Warning, sqlite3.ProgrammingError) as e:
            msg = str(e)
            if 'not authorized' in msg or 'one statement' in msg or 'readonly' in msg.lower():
                print(f'只能查（一条 SELECT / WITH），不能改库：{msg}')
            elif 'interrupted' in msg:
                print('查了 60 秒没查完，换个更省的写法（少用 LIKE 扫全文、先按日期 / 窗口缩小范围）')
            else:
                print(f'SQL 出错：{msg}')
            return
        print('\t'.join(d[0] for d in cur.description or []))
        for r in rows[:cap]:
            print('\t'.join(_one_line(str(v), 300) if v is not None else '' for v in r))
        if len(rows) > cap:
            print(f'…只列前 {cap} 行（加 --n 最多 500，或者用 GROUP BY / COUNT 先汇总）')
    finally:
        db.close()


def journal_(a):
    kw = ' '.join(a.words)
    rng, args = _rng(a, 'date')
    rows = store.q(f"""SELECT date, text FROM journal WHERE text LIKE ? AND (gone_at IS NULL OR gone_at = ''){rng}
                       ORDER BY date DESC LIMIT ?""", f'%{kw}%', *args, a.n)
    print(f'手写日记里「{kw}」出现在 {len(rows)} 篇（最新的在前）')
    for r in rows:
        print(f"[{r['date']}] {_around(r['text'], kw)}")


def diaries_(a):
    rng, args = _rng(a, 'date')
    rows = store.q(f'SELECT date, headline, narrative, mood FROM diaries WHERE 1=1{rng} ORDER BY date LIMIT ?', *args, min(a.n, 200))
    for r in rows:
        body = r['narrative'] or ''
        print(f"== {r['date']} {r['headline'] or ''}" + (f"（{r['mood']}）" if r['mood'] else '') + '\n' + (body if a.full else _one_line(body, 400)))


def _size(n):
    return f'{n / 1048576:.1f}MB' if n >= 1048576 else f'{n / 1024:.0f}KB' if n >= 1024 else f'{n}B'


def files_(a):
    if not a.words:
        print('要给文件夹：' + '、'.join(localfiles.show(r) for r in localfiles.roots()))
        return
    try:
        rows = localfiles.listing(' '.join(a.words), a.glob, a.n)
    except (localfiles.Denied, OSError) as e:
        print(e)
        return
    for p, is_dir, size, mtime in rows:
        when = time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))
        print(f"  {localfiles.show(p)}{'/' if is_dir else ''}  {'' if is_dir else _size(size) + '  '}{when}")
    if not rows:
        print('（空的，或者没有对得上的）')


def grep_(a):
    if not a.words or not a.into:
        print('用法：grep <词> [词…] --in <文件或文件夹>')
        return
    try:
        hits, cut = localfiles.grep(a.words, a.into, a.n)
    except (localfiles.Denied, OSError) as e:
        print(e)
        return
    for p, i, line in hits:
        print(f"  [[f:{localfiles.show(p)}:{i}]] {_around(line, a.words[0], 120)}")
    print(f"{len(hits)} 处" + ('（没搜完：到条数或时间上限了，换个更小的文件夹或加 --n）' if cut else ''))


def file_(a):
    if not a.words:
        print('用法：file <文件> [--from 1] [--lines 200] [--at 词]')
        return
    path = ' '.join(a.words)
    try:
        start = a.start
        if a.at:
            at = localfiles.find_line(path, a.at, start)
            if not at:
                print(f'从第 {start} 行往后没有「{a.at}」')
                return
            start = max(1, at - 20)
        rows, total = localfiles.lines(path, start, a.lines)
    except (localfiles.Denied, OSError) as e:
        print(e)
        return
    name, when = localfiles.where(path)
    ref = path if path.startswith(localfiles.MOYE) else name
    print(f'== {name}（{total} 行，改于 {when}）引用写 [[f:{ref}:行号]]')
    for i, line in rows:
        print(f'{i:>6}| {line}')
    if rows and rows[-1][0] < total:
        print(f'…… 后面还有 {total - rows[-1][0]} 行（--from {rows[-1][0] + 1}）')


def moye_(a):
    if not a.words:
        print('用法：moye <词>')
        return
    try:
        docs = localfiles.moye_search(' '.join(a.words), min(a.n, 20))
    except localfiles.Denied as e:
        print(e)
        return
    for d in docs:
        print(f"== moye:{d['id']}  {d['filename']}" + (f" · {d['title']}" if d['title'] else '') + f" · 转于 {d['date']} · 命中 {d['count']} 处")
        for h in d['hits']:
            print(f"    第 {h['page']} 页：{_one_line(h['text'], 160)}")
    print(f"{len(docs)} 份" + ('（读全文：file moye:<id> --at 词）' if docs else ''))


def skill_(a):
    name = a.words[0] if a.words else ''
    text = qa.skill_text(name) if name else None
    if text:
        print(text)
        return
    print((f'没有「{name}」这个技能。' if name else '') + '有这些：')
    for n, d in qa.skills():
        print(f'  {n}：{d}')


def memory_(a):
    name = ' '.join(a.words)
    text = memory.page(name) if name else None
    if text:
        print(text)
        return
    print((f'档案里没有「{name}」这一页。' if name else '') + '有这几页：')
    for t in memory.topics():
        print(f"  {t['name']}{'' if t['active'] else '（最近 30 天没动静）'}：{t['summary'] or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(prog='look.py', description='只读查 Ephemeris 的聊天记录和证据卡')
    p.add_argument('cmd', choices=['msgs', 'cards', 'read', 'conv', 'convs', 'day', 'stats', 'sum', 'sql', 'schema', 'journal', 'diaries',
                                     'files', 'grep', 'file', 'moye', 'skill', 'memory'])
    p.add_argument('words', nargs='*')
    p.add_argument('--since'); p.add_argument('--until')
    p.add_argument('--who', choices=['me', 'ai']); p.add_argument('--old', action='store_true')
    p.add_argument('--kw'); p.add_argument('--n', type=int, default=30)
    p.add_argument('--before', type=int, default=4); p.add_argument('--after', type=int, default=4)
    p.add_argument('--full', action='store_true'); p.add_argument('--at')
    p.add_argument('--glob'); p.add_argument('--in', dest='into')
    p.add_argument('--from', dest='start', type=int, default=1); p.add_argument('--lines', type=int, default=200)
    a = p.parse_args(argv)
    {'msgs': msgs, 'cards': cards_, 'read': read, 'conv': conv, 'convs': convs, 'day': day, 'stats': stats, 'sum': sum_, 'sql': sql, 'schema': schema,
     'journal': journal_, 'diaries': diaries_, 'files': files_, 'grep': grep_, 'file': file_, 'moye': moye_, 'skill': skill_, 'memory': memory_}[a.cmd](a)


if __name__ == '__main__':
    main()
