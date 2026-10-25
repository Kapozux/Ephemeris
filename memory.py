"""记忆：「问」先看的一份档案（10-25）。「设置 → 记忆」那一块。

照 claude.ai 的样子分两种：「你」（关于你 / 偏好）和一个个领域（申请、某门课、某个项目……）。
跟 claude.ai 不一样的地方（也是 Ephemeris 的长处）：每条事实带「截至哪天」和出处（证据卡），旧的不删、换下来折起来。

    topics(all=False)      → 主题列表（带事实）；all=False 只给「问」能看的（没关掉的）
    directory()            → 进 DEEP_PROMPT 的目录：每个主题一行名字 + 一句摘要
    page(name)             → look.py memory <名字> 读的全文
    refresh(job, force, name)  → 整理一遍：先定有哪些领域，再一个个从证据卡里更新（name：只整理这一页）
    due() / start(force, name) → 早上那一串每周跑一次（规矩 5：这周写过的不动、没新卡的不动）；手动按钮 force
    add / strike / switch  → 他自己补一条、划掉一条、某个主题不让「问」看

写记忆和「问」分开：「问」只读（look.py memory），不边答边改档案。
"""
import json
from datetime import date, datetime, timedelta

import days
import index as idx
import jobs
import llm
import qa
import store

JOB = 'memory'
ACTIVE_DAYS = 30           # 这么多天内有新卡的领域才进「问」的目录（老的留着，标不活跃）
FIRST_DAYS = 180           # 一个主题第一次整理，往前看这么多天的卡
BUDGET = 30000             # 整理一个主题时送进去的卡，最多这么多字
MAX_AREAS = 14
MAX_FACTS = 14             # 一个主题最多这么多条（官方也说要给每份记忆限长）

# 「你」那两个是固定的；领域每次整理时从主题账本和卡的话题里定
YOU = {
    '关于你': {'summary': '你是谁：学校、年级、在哪、家里、长期的目标', 'since': None,
            'keywords': ['我是', '我在', '学校', '年级', '高三', '家里', '爸', '妈', '城市', '目标', '专业', '计划']},
    '偏好': {'summary': '你希望 AI 怎么跟你说话、怎么帮你做事', 'since': None,
           'keywords': ['不要', '别', '我希望你', '你应该', '风格', '口吻', '中文', '简短', '直接', '列表', '格式', '先问我']},
}

AREAS_PROMPT = """下面是 {name} 最近 {days} 天在聊天里一直在推进的事：主题账本（他长期在想的事，带出现了几天、最近一次是哪天）和证据卡的话题（带张数）。
请定出他现在的「领域」：像一个了解他的朋友会给他记的几个文件夹，比如「大学申请」「数学论文」「Ephemeris 开发」「某段关系」。

- 最多 {most} 个，按最近投入多少排。同一件事的不同说法合成一个（「CA主文书」「Common App 主文书」「主文书」都归「大学申请」）。
- 已有的领域（下面列着）还对得上就沿用原来的名字，别换说法；对不上的不用列。
- 名字短（2～10 个字），用他自己的叫法。
- keywords：8～16 个，用来在证据卡里按字面搜这个领域：同义说法、中英文、人名、简称。
- 只输出 JSON：{{"areas": [{{"name": "……", "summary": "一句话说这个领域是什么", "keywords": ["……"]}}]}}

已有的领域：
{existing}

主题账本：
{themes}

证据卡的话题：
{topics}
"""

TOPIC_PROMPT = """你在给 {name} 维护一份档案里的「{topic}」这一页（{hint}）。今天是 {today}。
「问」回答他的问题前会先看这份档案，所以要写的是：现在的情况、推进到哪了、定下来的事、他在意的点。

下面是这一页现有的事实（带编号 f…，可能为空），和新的证据卡（带编号 c…，按时间排）。
请输出这一页更新后的样子，只输出 JSON：
{{"summary": "一两句话：这一页现在的总体情况", "facts": [{{"keep": "f3"}}, {{"text": "……", "as_of": "YYYY-MM-DD", "cites": ["c12", "c40"], "replaces": ["f2"]}}]}}

- facts 是更新后的完整列表，最多 {most} 条，重要的在前。每条一句话、具体（数字、学校、日期、人名、决定）。
- 还对的旧事实写 {{"keep": "f…"}}，别重写一遍。
- 新事实必须有出处：cites 写支持它的卡的编号（照抄 c…，只能用下面给了的）；as_of 是能说明这件事的最晚那张卡的日期。
- 新的推翻 / 更新了旧的（分数变了、决定改了、关系变了），新事实 replaces 写被换下的 f…。旧的不会被删，会折起来留着。
- 没写进去的旧事实也会折起来，所以只有确实不对了、不重要了才不写。标 hand 的是他自己写的，一律 keep。
- 只写卡里有的，不推测、不评价他、不给建议。分清是他说的还是 AI 说的：AI 的建议不是他的决定。
- 偶尔一次的小事不写；这一页的主题以外的不写。
{you_rule}
现有的事实：
{facts}

新的证据卡：
{cards}
"""
YOU_RULE = {
    '关于你': '- 这一页只写关于他这个人的长期事实（身份、学校、家里、住哪、长期目标），各个领域里的具体进展不写。\n',
    '偏好': '- 这一页只写他对 AI 的要求：怎么说话、怎么做事、不要什么（他明确说过的，最好是反复说过的），不写他对别的事的看法。\n',
}


# ---------------------------------------------------------------- 读

def _now():
    return datetime.now().isoformat(timespec='seconds')


def _facts(tid, old=False):
    rows = store.q(f"SELECT * FROM memory_facts WHERE topic_id=? AND old_by IS {'NOT' if old else ''} NULL ORDER BY pos, id", tid)
    for r in rows:
        r['cites'] = json.loads(r['cites'] or '[]')
    return rows


def _active_since():
    return (date.fromisoformat(days.today()) - timedelta(days=ACTIVE_DAYS)).isoformat()


def topics(all=False):
    since = _active_since()
    out = []
    for t in store.q("SELECT * FROM memory_topics ORDER BY kind DESC, last_seen DESC, id"):     # you 在 area 前
        if not all and t['off']:
            continue
        t['active'] = t['kind'] == 'you' or (t['last_seen'] or '') >= since
        t['facts'] = _facts(t['id'])
        if all:                                             # 设置页：带上换下来的，出处带上能跳回原话的位置
            t['old'] = _facts(t['id'], old=True)
            for f in t['facts'] + t['old']:
                f['src'] = _sources(f['cites'])
        t['keywords'] = json.loads(t['keywords'] or '[]')
        out.append(t)
    return out


def _sources(ids):
    out = []
    for cid in ids:
        c = store.one('SELECT id, date, conv_id, msg_id FROM cards WHERE id=?', cid)
        if c:
            out.append(c)
    return out


def directory():
    """进 DEEP_PROMPT：只放名字 + 一句摘要（全文它自己 look memory 读，平常的问题不多花字）。不活跃的领域不放。"""
    rows = [t for t in topics() if t['active'] and t['facts']]
    return '\n'.join(f"  · {t['name']}（截至 {(t['written_at'] or '')[:10]}）：{t['summary'] or ''}" for t in rows) or '  （还没整理过）'


def _cite_marks(cites):
    return ''.join(f'[[c:{c}]]' for c in cites)


def page(name):
    """look.py memory <名字>：一页全文，每条带截至哪天和出处记号（「问」照抄就能引）。没有 / 关掉了 → None。"""
    t = next((t for t in topics() if t['name'] == name), None)
    if not t:
        return None
    lines = [f"## {t['name']}（整理于 {(t['written_at'] or '')[:10]}）", t['summary'] or '']
    for f in t['facts']:
        who = '（他自己写的）' if f['by'] == 'hand' else ''
        lines.append(f"- {f['text']}（截至 {f['as_of'] or '?'}）{who}{_cite_marks(f['cites'])}")
    return '\n'.join(lines)


# ---------------------------------------------------------------- 写（模型）

def _upsert_topic(kind, name, summary, keywords):
    store.run("""INSERT INTO memory_topics (kind, name, summary, keywords, made_at) VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(name) DO UPDATE SET keywords=excluded.keywords""",
              kind, name, summary, json.dumps(keywords, ensure_ascii=False), _now())
    return store.one('SELECT * FROM memory_topics WHERE name=?', name)


def plan_areas():
    """定领域：最近 60 天的主题账本 + 卡的话题 → 一次模型调用。已有的名字沿用。"""
    today = days.today()
    since = (date.fromisoformat(today) - timedelta(days=60)).isoformat()
    themes = store.q("""SELECT t.name, t.desc, COUNT(DISTINCT d.date) n, MAX(d.date) last FROM themes t JOIN theme_days d ON d.theme_id = t.id
                        WHERE d.date >= ? GROUP BY t.id HAVING n >= 2 ORDER BY n DESC LIMIT 80""", since)
    tops = store.q("""SELECT topic, COUNT(*) n FROM cards WHERE date >= ? AND topic IS NOT NULL AND topic != ''
                      GROUP BY topic ORDER BY n DESC LIMIT 100""", since)
    existing = store.q("SELECT name, summary FROM memory_topics WHERE kind='area'")
    prompt = AREAS_PROMPT.format(
        name=idx.who(), days=60, most=MAX_AREAS,
        existing='\n'.join(f"- {e['name']}：{e['summary'] or ''}" for e in existing) or '（还没有）',
        themes='\n'.join(f"- {t['name']}（{t['n']} 天，最近 {t['last']}）{('：' + t['desc']) if t['desc'] else ''}" for t in themes) or '（无）',
        topics='\n'.join(f"- {t['topic']}：{t['n']}" for t in tops) or '（无）')
    out = llm.ask_json(prompt, ok=lambda o: isinstance(o.get('areas'), list), effort='medium', purpose='memory-areas')
    areas = [a for a in (out or {}).get('areas') or [] if isinstance(a, dict) and (a.get('name') or '').strip()][:MAX_AREAS]
    for a in areas:
        _upsert_topic('area', a['name'].strip()[:20], (a.get('summary') or '').strip(), [str(k) for k in a.get('keywords') or []][:16])
    return [a['name'].strip()[:20] for a in areas]


def _pick_cards(t, since):
    plan = {'q': f"{t['name']}：{t['summary'] or ''}", 'keywords': json.loads(t['keywords'] or '[]'), 'since': since}
    ids = qa.rank(plan, budget=BUDGET)
    if t['kind'] == 'you':                                  # 关于他自己 / 对 AI 的要求：只看他说的
        mine = {r['id'] for r in store.q("SELECT id FROM cards WHERE who='me'" + (' AND date >= ?' if since else ''), *([since] if since else []))}
        ids = [i for i in ids if i in mine]
    picked, size = [], 0
    for cid in ids:
        c = qa.card(cid)
        if not c:
            continue
        n = len(c['text']) + len(c['quote'] or '') + 60
        if size + n > BUDGET:
            break
        picked.append(c)
        size += n
    return sorted(picked, key=lambda c: (c['date'], c['ts'] or '', c['id']))


def _fact_line(i, f):
    return f"[f{i}] {f['text']}（截至 {f['as_of'] or '?'}）{' hand' if f['by'] == 'hand' else ''}"


def refresh_topic(name, full=False):
    """一个主题：现有事实 + 上次整理以后的新卡（full：手动重整，照第一次那样往前看 FIRST_DAYS 天） → 模型给出更新后的完整列表。换下来的、没写进去的旧事实标 old_by（不删，规矩 6）。"""
    t = store.one('SELECT * FROM memory_topics WHERE name=?', name)
    if not t:
        raise ValueError('没有这个主题')
    old = _facts(t['id'])
    since = (not full and (t['written_at'] or '')[:10]) or (None if t['kind'] == 'you' else
                                            (date.fromisoformat(days.today()) - timedelta(days=FIRST_DAYS)).isoformat())
    if since and old and not full:                                       # 接着上次：那天的卡也再看一眼（上次可能只做了一半）
        since = (date.fromisoformat(since) - timedelta(days=1)).isoformat()
    picked = _pick_cards(t, since)
    if not picked:
        store.run('UPDATE memory_topics SET written_at=? WHERE id=?', _now(), t['id'])
        return {'name': name, 'new': 0}
    falias = {f'f{i}': f for i, f in enumerate(old, 1)}
    calias = {f'c{i}': c for i, c in enumerate(picked, 1)}
    prompt = TOPIC_PROMPT.format(
        name=idx.who(), topic=name, hint=t['summary'] or '', today=days.today(), most=MAX_FACTS, you_rule=YOU_RULE.get(name, ''),
        facts='\n'.join(_fact_line(i, f) for i, f in enumerate(old, 1)) or '（还没有）',
        cards='\n'.join(qa._card_line(i, c) for i, c in enumerate(picked, 1)))
    out = llm.ask_json(prompt, ok=lambda o: isinstance(o.get('facts'), list), effort='medium', purpose='memory')
    if out is None:
        raise RuntimeError(f'「{name}」没整理出来')
    apply(t, out, falias, calias)
    return {'name': name, 'new': len(picked)}


def apply(t, out, falias, calias):
    """模型的输出 → 库。出处只认给了的卡（编的删掉），没出处的新事实不要；他自己写的（hand）一律留着。"""
    now = _now()
    made = []
    for item in out.get('facts') or []:
        if not isinstance(item, dict):
            continue
        if item.get('keep') in falias:
            made.append(('keep', falias[item['keep']]['id']))
            continue
        cites = [calias[c]['id'] for c in item.get('cites') or [] if c in calias]
        text = (item.get('text') or '').strip()
        if not text or not cites:
            continue
        dates = [calias[c]['date'] for c in item.get('cites') or [] if c in calias]
        as_of = item.get('as_of') if item.get('as_of') in dates else max(dates)
        made.append(('new', text, as_of, list(dict.fromkeys(cites)), [falias[r]['id'] for r in item.get('replaces') or [] if r in falias]))
    made = made[:MAX_FACTS]
    kept = {m[1] for m in made if m[0] == 'keep'}
    with store.tx() as db:
        pos = 0
        for m in made:
            pos += 1
            if m[0] == 'keep':
                db.execute('UPDATE memory_facts SET pos=? WHERE id=?', (pos, m[1]))
                continue
            cur = db.execute('INSERT INTO memory_facts (topic_id, text, as_of, cites, by, pos, made_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (t['id'], m[1], m[2], json.dumps(m[3]), 'ai', pos, now))
            for r in m[4]:
                if r not in kept:
                    db.execute('UPDATE memory_facts SET old_by=? WHERE id=? AND by != ?', (cur.lastrowid, r, 'hand'))
        for f in falias.values():                                   # 没写进去的：折起来（0 = 没被哪条换掉，只是不在了）
            if f['id'] not in kept and f['by'] != 'hand':
                db.execute('UPDATE memory_facts SET old_by=0 WHERE id=? AND old_by IS NULL', (f['id'],))
        latest = max((c['date'] for c in calias.values()), default=None)
        db.execute("UPDATE memory_topics SET summary=COALESCE(NULLIF(?, ''), summary), written_at=?, last_seen=MAX(COALESCE(last_seen, ''), ?) WHERE id=?",
                   ((out.get('summary') or '').strip(), now, latest or '', t['id']))


def _has_new(t):
    """上次整理以后有没有沾边的新卡（规矩 5：数据没变的不动）。"""
    if not t['written_at']:
        return True
    kws = json.loads(t['keywords'] or '[]')
    if not kws:
        return False
    like = ' OR '.join(['(text LIKE ? OR topic LIKE ?)'] * len(kws))
    args = [x for k in kws for x in (f'%{k}%', f'%{k}%')]
    return bool(store.one(f'SELECT 1 FROM cards WHERE date >= ? AND ({like}) LIMIT 1', t['written_at'][:10], *args))


def refresh(job=None, force=False, name=None):
    """整理一遍（name：只整理这一页）。force（手动按钮）：这周写过的也重写。
    不是 force 时：这周写过的不动；没新卡的只记一下「看过了」（written_at），这样一周只跑一回。"""
    def prog(stage, a=0, b=0):
        if job is not None:
            job.update(stage=stage, done=a, total=b)

    for n, d in YOU.items():
        if not store.one('SELECT 1 FROM memory_topics WHERE name=?', n):
            _upsert_topic('you', n, d['summary'], d['keywords'])
    if name:
        prog(f'整理「{name}」')
        return [refresh_topic(name, full=True)]
    planned = []
    last = store.one("SELECT MAX(written_at) at FROM memory_topics WHERE kind='area'")['at']
    if force or not days.written_this_week(last):
        prog('定领域')
        planned = plan_areas()
    since = _active_since()
    rows = [t for t in store.q("SELECT * FROM memory_topics WHERE off=0 ORDER BY kind DESC, last_seen DESC")
            if t['kind'] == 'you' or t['name'] in planned or (t['last_seen'] or '') >= since]
    done = []
    for i, t in enumerate(rows):
        if not force and days.written_this_week(t['written_at']):
            continue
        if not force and not _has_new(t):
            store.run('UPDATE memory_topics SET written_at=? WHERE id=?', _now(), t['id'])
            continue
        prog(f"整理「{t['name']}」", i, len(rows))
        try:
            done.append(refresh_topic(t['name'], full=force))
        except Exception as e:                               # noqa: BLE001  一个没整理出来不挡别的
            print(f"[memory] {t['name']}：{type(e).__name__}: {e}", flush=True)
    return done


def due():
    """早上那一串问一句：这周还没整理过（一页都没有 = 从没整理过）。"""
    return not days.written_this_week(store.one('SELECT MAX(written_at) at FROM memory_topics')['at'])


IDLE = {'stage': '', 'done': 0, 'total': 0}


def start(force=False, name=None):
    jobs.start(JOB, refresh, force, name, **IDLE)
    return status()


def status():
    return jobs.get(JOB, **IDLE)


# ---------------------------------------------------------------- 他自己改

def _topic(name):
    t = store.one('SELECT * FROM memory_topics WHERE name=?', name)
    if not t:
        raise ValueError('没有这个主题')
    return t


def add(name, text):
    """他自己补一条（by=hand：整理时模型必须留着）。"""
    text = (text or '').strip()
    if not text:
        raise ValueError('空的')
    t = _topic(name)
    pos = store.one('SELECT COALESCE(MAX(pos), 0) + 1 p FROM memory_facts WHERE topic_id=? AND old_by IS NULL', t['id'])['p']
    store.run('INSERT INTO memory_facts (topic_id, text, as_of, cites, by, pos, made_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
              t['id'], text[:300], days.today(), '[]', 'hand', pos, _now())


def strike(fact_id):
    """划掉一条：折进「以前的」（-1 = 他划掉的；不删）。"""
    if not store.one('SELECT 1 FROM memory_facts WHERE id=? AND old_by IS NULL', fact_id):
        raise ValueError('没有这一条')
    store.run('UPDATE memory_facts SET old_by=-1 WHERE id=?', fact_id)


def switch(name, on):
    """关掉的主题：「问」不看（目录里没有、look memory 读不到），也不再自动整理。"""
    store.run('UPDATE memory_topics SET off=? WHERE id=?', 0 if on else 1, _topic(name)['id'])
