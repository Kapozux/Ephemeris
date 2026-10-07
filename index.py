"""聊天记录索引：窗口为中心，可增量导入。

窗口（conversation）的身份 = 首条人类消息的时间 + 前 200 字的指纹，
**不看标题、不看文件名、不看来自哪次导出**。所以：
  - 批量范围导出里的一个窗口，和 incognito 单独导出的同一个窗口，落到同一个 id
  - 同一个窗口后来又聊了、再导一次：新消息加上（INSERT OR REPLACE），旧的原样保留
  - 哪一天的消息集合变了，那天的日记作废，等下次补全

解析是确定性代码，模型一步都不掺和。

支持的输入（按内容嗅探，不看扩展名）：
  claude_md    浏览器脚本导出的 markdown（批量合并的，或单个 "# Conversation with Claude"）
  claude_json  export_claude.js 导出的 JSON
  gemini_md    gemini_split/sess_*.md（**我** (2025-11-29 11:25):）
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from html import unescape

import store
from days import LOCAL_TZ, bucket    # 一天怎么算：days.py
HERE = os.path.dirname(os.path.abspath(__file__))


def env(key, default=''):
    """先看环境变量，再看 ephemeris/.env。凭证、名字、Notion 库 id 都走这里，不写进代码。"""
    v = os.environ.get(key)
    if v:
        return v.strip()
    p = os.path.join(HERE, '.env')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + '='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    return default


def who():
    """prompt 里怎么称呼你。在 .env 里设 DIARY_NAME。"""
    return env('DIARY_NAME', '用户')
IMPORT_DIR = os.path.join(HERE, 'imports')

RECENT_DAYS = 3          # 导入新消息时，这么多天以内的日记作废重写；更早的不动（见 ingest）

MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}

def _h(s, n=20):
    return hashlib.sha1(s.encode()).hexdigest()[:n]


def conv_fingerprint(msgs):
    """窗口身份的**兜底**：第一条人类消息的时间 + 前 200 字。

    只在没有稳定 id 的时候用（Gemini 的 sess_*.md、incognito 的 transcript）。
    claude.ai 的 JSON 自带 uuid，走 uuid —— 指纹对时间戳精度敏感，
    md（到分钟）和 json（到毫秒）会算出两个不同的值。
    """
    for dt, role, text in msgs:
        if role == 'human':
            return _h(f'{dt.isoformat()}|{text[:200]}')
    dt, _, text = msgs[0]
    return _h(f'{dt.isoformat()}|{text[:200]}')


# ------------------------------------------------------------------ parsers
# 每个 parser yield dict(title, title_fallback, msgs=[(dt, role, text)])

_H_RE = re.compile(r'\n## (Human|Claude) \(([^)]*)\):\n')


def _claude_ts(s):
    m = re.match(r'(\w+) (\d+), (\d+),\s*(\d+):(\d+)\s*(AM|PM)', s.strip())
    if not m or m.group(1) not in MONTHS:
        return None
    hour = int(m.group(4)) % 12 + (12 if m.group(6) == 'PM' else 0)
    return datetime(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)),
                    hour, int(m.group(5)), tzinfo=LOCAL_TZ)


def parse_claude_md(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    lines = raw.split('\n')
    offs, pos = [], 0
    for ln in lines:
        offs.append(pos)
        pos += len(ln) + 1
    starts = []
    for i, ln in enumerate(lines):
        if ln.startswith('# ') and not ln.startswith('## '):
            # 批量导出里 '# 标题' 和第一条 '## Human (' 只隔一个空行；窗口开大会把正文里的 '# ' 也当成对话头
            for j in range(i + 1, min(i + 8, len(lines))):
                if lines[j].startswith('## Human ('):
                    starts.append((i, ln[2:].strip()))
                    break
    fallback = False
    if not starts and '## Human (' in raw:
        # 单个窗口的导出（incognito / 手动）：头部夹着导出元信息，整份文件就是一个窗口。
        # 标题优先取 "**xxx**" 那行（单窗口导出里紧跟着 "# Conversation with Claude"）
        m = re.search(r'^\*\*(.+?)\*\*\s*$', raw[:2000], re.M)
        title = m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0]
        fallback = not m
        starts = [(0, title)]
    for k, (li, title) in enumerate(starts):
        s = offs[li]
        e = offs[starts[k + 1][0]] if k + 1 < len(starts) else len(raw)
        parts = _H_RE.split(raw[s:e])
        msgs = []
        for idx in range(1, len(parts) - 1, 3):
            role, ts, body = parts[idx], parts[idx + 1], parts[idx + 2]
            dt = _claude_ts(ts)
            body = body.strip().rstrip('-').strip()
            body = re.sub(r'\n?\*\[attachment omitted:[^\]]*\]\*', '', body).strip()
            if dt and body:
                msgs.append((dt, 'human' if role == 'Human' else 'assistant', body))
        if msgs:
            yield {'title': title, 'title_fallback': fallback, 'msgs': msgs}


def parse_claude_json(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    convs = data.get('conversations', data) if isinstance(data, dict) else data
    for c in convs:
        msgs = []
        for m in c.get('messages', []):
            ts, text = m.get('created_at'), (m.get('text') or '').strip()
            if not (ts and text):
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            msgs.append((dt, m.get('role', 'human'), text))
        if msgs:
            # uuid 带出来当窗口身份 —— 指纹是给「没有稳定 id 的导出」用的兜底，
            # 有 uuid 还用指纹的话，同一个对话从 md 和 json 两条路进来会算出两个窗口
            # （2026-09-20 踩过：35 个对话各自变成两份，12366 条重复）
            yield {'uid': c.get('uuid') or c.get('id') or None,
                   'title': c.get('name') or 'Untitled',
                   'title_fallback': not c.get('name'), 'msgs': msgs}


_G_RE = re.compile(r'\*\*(我|Gemini|模型|回答)\*\* \((\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\):')


def parse_gemini_md(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    parts = _G_RE.split(raw)
    msgs = []
    for i in range(1, len(parts) - 6, 7):
        who, y, mo, dd, hh, mm, body = parts[i:i + 7]
        body = body.strip()
        if body:
            msgs.append((datetime(int(y), int(mo), int(dd), int(hh), int(mm), tzinfo=LOCAL_TZ),
                         'human' if who == '我' else 'assistant', body))
    if msgs:
        m = re.search(r'^#\s+(.+)$', raw[:500], re.M)
        yield {'title': m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0],
               'title_fallback': not m, 'msgs': msgs}


# --------------------------------------------------- Google Takeout（Gemini）

_TAKEOUT_USER_PREFIX = ('Prompted ', 'Added chat from link: ')
_TAG_RE = re.compile(r'<[^>]+>')


def _html_text(html):
    """Takeout 的回复是 HTML。不引第三方库，够用就行。"""
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', '', html or '')
    s = re.sub(r'(?i)<br\s*/?>', '\n', s)
    s = re.sub(r'(?i)</(p|div|li|h[1-6]|tr|blockquote|pre)>', '\n', s)
    s = _TAG_RE.sub('', s)
    s = unescape(s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()


def parse_gemini_takeout(path):
    """Google Takeout 里 MyActivity 的 Gemini Apps JSON。

    结构跟别的导出都不一样：顶层是一个**活动数组**，一条活动 ≈ 一轮对话 ——
      title         我发的（前缀 'Prompted ' 或 'Added chat from link: '）
      safeHtmlItem  Gemini 的回复（HTML）
      subtitles     Created Gemini Canvas 的产物（代码/HTML 大块）
      details[].url 里的 /app/<id> **就是会话 id**，靠它才能还原成窗口

    没有会话标题，所以拿第一条人类消息的开头当标题（title_fallback=True）。
    时间是 UTC，bucket() 会按 LOCAL_TZ 落到天。
    """
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        return

    convs = {}
    for a in data:
        if not isinstance(a, dict):
            continue
        t = a.get('time')
        if not t:
            continue
        try:
            dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
        except ValueError:
            continue

        cid = None
        for det in a.get('details') or []:
            m = re.search(r'/app/([0-9a-zA-Z_-]+)', det.get('url') or '')
            if m:
                cid = m.group(1)
                break
        if not cid:                       # 拿不到会话 id（Canvas 之类）：按天兜底
            cid = 'noid-' + bucket(dt)[0]

        title = (a.get('title') or '').strip()
        pair = []
        for pre in _TAKEOUT_USER_PREFIX:
            if title.startswith(pre):
                body = title[len(pre):].strip()
                # attachedFiles 里可能是 {'name': ...} 也可能直接是字符串
                names = [f.get('name') if isinstance(f, dict) else str(f)
                         for f in (a.get('attachedFiles') or [])]
                names = [n for n in names if n]
                if names:
                    body += '\n[附件：' + '、'.join(names[:5]) + ']'
                if body:
                    pair.append((dt, 'human', body))
                break

        reply = ''
        for it in a.get('safeHtmlItem') or []:
            reply = _html_text(it.get('html'))
            if reply:
                break
        if not reply and title.startswith('Created Gemini Canvas'):
            blob = next((x.get('name') for x in (a.get('subtitles') or []) if x.get('name')), '')
            if blob:
                reply = '[Canvas]\n' + blob
        if reply:
            # 回复跟提问同一时刻，差 1 毫秒只为让 ORDER BY ts 稳定（不影响 hhmm）
            pair.append((dt + timedelta(milliseconds=1), 'assistant', reply))

        if pair:
            convs.setdefault(cid, []).extend(pair)

    for cid, msgs in convs.items():
        msgs.sort(key=lambda x: x[0])
        first_human = next((m[2] for m in msgs if m[1] == 'human'), '')
        yield {'title': (first_human[:38].replace('\n', ' ') or 'Gemini ' + cid[:8]).strip(),
               'title_fallback': True, 'msgs': msgs}


PARSERS = {'claude_md': parse_claude_md, 'claude_json': parse_claude_json, 'gemini_md': parse_gemini_md,
           'gemini_takeout': parse_gemini_takeout}


def sniff(path):
    """按内容判断格式和来源。"""
    with open(path, 'rb') as f:
        head = f.read(4000).decode('utf-8', errors='replace')
    if head.lstrip().startswith('{') or head.lstrip().startswith('['):
        # Google Takeout 的 MyActivity 也是 JSON，但顶层是活动数组，跟 Claude 导出完全两回事
        if '"header"' in head and '"time"' in head and ('Gemini Apps' in head or 'Bard' in head):
            return 'gemini_takeout', 'gemini'
        return 'claude_json', 'claude'
    if '**我**' in head or re.search(r'\*\*(Gemini|模型)\*\* \(\d{4}-', head):
        return 'gemini_md', 'gemini'
    if '## Human (' in head or 'Claude Conversations Export' in head or 'Conversation with Claude' in head:
        return 'claude_md', 'claude'
    with open(path, encoding='utf-8', errors='replace') as f:
        body = f.read(200000)
    if '**我**' in body:
        return 'gemini_md', 'gemini'
    if '## Human (' in body:
        return 'claude_md', 'claude'
    raise ValueError('认不出格式：不是 Claude 导出的 markdown/JSON、Gemini 的 sess_*.md，也不是 Google Takeout 的 MyActivity JSON')


# ------------------------------------------------------------------ ingest

def ingest(conn, path, kind=None, source=None, label=None, invalidate=True):
    """把一个文件的窗口喂进库。返回摘要。幂等：重复导入不会产生重复消息。"""
    if not kind:
        kind, source = sniff(path)
    label = label or os.path.basename(path)
    now = datetime.now().isoformat(timespec='seconds')
    n_convs = n_new_convs = n_msgs = n_new_msgs = 0
    touched, fresh_all = set(), []

    for c in PARSERS[kind](path):
        msgs = sorted(c['msgs'], key=lambda x: x[0])
        # 有稳定 id（claude.ai 的 uuid）就用它；没有才退回指纹
        cid = c.get('uid') or conv_fingerprint(msgs)
        n_convs += 1
        existing = conn.execute('SELECT id, title, title_fallback FROM conversations WHERE id=?', (cid,)).fetchone()
        before = conn.execute('SELECT COUNT(*) FROM messages WHERE conv_id=?', (cid,)).fetchone()[0]

        rows = []
        for dt, role, text in msgs:
            date, hhmm = bucket(dt)
            rows.append((_h(f'{cid}|{dt.isoformat()}|{text[:300]}'), cid, source,
                         dt.isoformat(), date, hhmm, role, text, len(text)))
        # 只算真正新增的消息，和它们落在哪些天
        new_ids = [r[0] for r in rows]
        have = set()
        for i in range(0, len(new_ids), 900):
            chunk = new_ids[i:i + 900]
            have.update(r[0] for r in conn.execute(
                f'SELECT id FROM messages WHERE id IN ({",".join("?" * len(chunk))})', chunk))
        fresh = [r for r in rows if r[0] not in have]
        fresh_all += fresh
        touched.update(r[4] for r in fresh if r[6] == 'human')
        conn.executemany('INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?)', rows)
        n_msgs += len(rows)
        n_new_msgs += len(fresh)

        title, fb = c['title'], int(bool(c.get('title_fallback')))
        if existing:
            # 已有的窗口：标题只在「旧的是文件名兜底、新的是真标题」时才换
            if existing['title_fallback'] and not fb:
                conn.execute('UPDATE conversations SET title=?, title_fallback=0 WHERE id=?', (title, cid))
            conn.execute('UPDATE conversations SET last_import=? WHERE id=?', (label, cid))
        else:
            n_new_convs += 1
            conn.execute('''INSERT INTO conversations (id, source, title, title_fallback, first_import, last_import)
                            VALUES (?,?,?,?,?,?)''', (cid, source, title, fb, label, label))
        if before == 0 and not existing:
            pass

    # 从消息重算每个窗口的统计（也顺手修 reconcile 留下的坑）
    _recount(conn)
    invalidated = 0
    if touched and invalidate:
        # 最近几天（同步时晚到的消息）：日记作废，等补全重写。
        # 更早的（导入旧聊天）：日记是当时写的，不动，只记一笔「之后又补进了几条」，要不要重写用户自己点
        recent = (datetime.now(LOCAL_TZ).date() - timedelta(days=RECENT_DAYS)).isoformat()
        recent_rows = [d for d in touched if d >= recent]
        if recent_rows:
            ph = ",".join("?" * len(recent_rows))
            invalidated = conn.execute(f'DELETE FROM diaries WHERE date IN ({ph})', recent_rows).rowcount
        for d in touched - set(recent_rows):
            n = sum(1 for r in fresh_all if r[4] == d and r[6] == 'human')
            conn.execute('UPDATE diaries SET added_later = COALESCE(added_later, 0) + ? WHERE date=?', (n, d))
    conn.execute('''INSERT INTO imports (file, kind, source, imported_at, n_convs, n_convs_new, n_msgs, n_msgs_new,
                    days_touched, diaries_invalidated) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                 (label, kind, source, now, n_convs, n_new_convs, n_msgs, n_new_msgs, len(touched), invalidated))
    conn.commit()
    return {'file': label, 'kind': kind, 'source': source, 'n_convs': n_convs, 'n_convs_new': n_new_convs,
            'n_msgs': n_msgs, 'n_msgs_new': n_new_msgs, 'days_touched': sorted(touched),
            'diaries_invalidated': invalidated}


def _recount(conn):
    conn.execute('''UPDATE conversations SET
        first_ts = (SELECT MIN(ts) FROM messages m WHERE m.conv_id = conversations.id),
        last_ts  = (SELECT MAX(ts) FROM messages m WHERE m.conv_id = conversations.id),
        n_msgs   = (SELECT COUNT(*) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human'),
        chars    = (SELECT COALESCE(SUM(chars),0) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human'),
        n_days   = (SELECT COUNT(DISTINCT date) FROM messages m WHERE m.conv_id = conversations.id AND m.role='human')''')
    conn.execute('DELETE FROM conversations WHERE n_msgs = 0 OR n_msgs IS NULL')


def rebuild_fts(conn):
    conn.execute('DELETE FROM msg_fts')
    conn.execute("""INSERT INTO msg_fts(text, conv_title, date, msg_id)
                    SELECT m.text, c.title, m.date, m.id
                      FROM messages m JOIN conversations c ON c.id = m.conv_id
                     WHERE m.role = 'human'""")
    conn.commit()


def import_file(conn, path, keep_copy=True):
    """给 app / CLI 用：导入一个文件，存一份副本到 imports/，重建 FTS。"""
    kind, source = sniff(path)
    label = os.path.basename(path)
    if keep_copy:
        os.makedirs(IMPORT_DIR, exist_ok=True)
        dst = os.path.join(IMPORT_DIR, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{label}")
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy2(path, dst)
    summary = ingest(conn, path, kind, source, label)
    rebuild_fts(conn)
    return summary


# 以前这里有 reindex_all + SOURCES：清空 messages / conversations，再从几个写死的导出文件重建。
# 2026-10-07 删掉 —— claude.ai 每天同步进来的消息只在库里，一重建就全没了。库本身就是档案，只加不删。


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*', help='要导入的文件（只加不删；不给就只看一眼库里有多少）')
    args = ap.parse_args()
    conn = store.connect()
    for f in args.files:
        r = import_file(conn, f)
        print(f"{r['file'][:48]:<50} 窗口 {r['n_convs']}（新 {r['n_convs_new']}） 消息 {r['n_msgs']:,}（新 {r['n_msgs_new']:,}） "
              f"涉及 {len(r['days_touched'])} 天，作废日记 {r['diaries_invalidated']}")
    r = conn.execute("""SELECT COUNT(DISTINCT conv_id) c, COUNT(*) m, COUNT(DISTINCT date) d, SUM(chars) ch
                          FROM messages WHERE role='human'""").fetchone()
    print(f"\n索引：{r['c']} 个窗口 · {r['m']:,} 条我的消息 · {r['d']} 天 · {r['ch']:,} 字 · "
          f"日记 {conn.execute('SELECT COUNT(*) FROM diaries').fetchone()[0]} 篇 -> {store.DB_PATH}")
