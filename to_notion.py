"""把 Ephemeris 的日记同步到 Notion 的 Notes Database。

约定跟着我自己的日记走，不另起一套：
  标题 = 裸日期 `2025-8-21`（**不补零**，跟库里手写的日记页一致）
  属性全部留空 —— 标签 / Core Function / Core Domain / 日期 都由 Notion AI 自己填，
  手写进去的值事后会被改掉。

两种写法：
  create  这天他没写过日记 -> 新建一页，icon 💬
  append  这天他自己写了   -> 在他那页末尾追加，**不碰他的正文和属性**

判别「哪些是 Ephemeris 推的」靠 icon 💬 + 裸日期标题，
外加本地 `notion_pages` 表。两者都认，表为准，icon 用来回填历史。

API 版本必须 2025-09-03：老的 2022-06-28 没有 data_sources 端点，
而库的 data_source_id 和 database_id 是两个不同的 id。
"""
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

import index as idx

NOTION_VERSION = '2025-09-03'
DATA_SOURCE_ID = idx.env('NOTION_DIARY_DS')        # 日记要进的那个 Notion 库的 data_source_id
TITLE_PROP = 'Notes'
ICON = '💬'

DATE_RE = re.compile(r'^\s*(20\d{2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})\s*$')


def token():
    """从环境或 ephemeris/.env 读。没有就返回 ''。"""
    return idx.env('NOTION_TOKEN')


# ------------------------------------------------------------------- REST

class NotionError(RuntimeError):
    pass


def _req(method, url, payload=None, retries=5):
    tok = token()
    if not tok:
        raise NotionError('没有 NOTION_TOKEN，先在 ephemeris/.env 里设上')
    data = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(retries):
        r = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {tok}',
            'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            # 429 / 5xx 退避重试，其余直接抛
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise NotionError(f'{e.code} {body}') from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise NotionError(f'连不上 Notion：{e.reason}') from None
        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            # Notion 长时间跑批会主动断连（RemoteDisconnected），它不是 URLError，
            # 只接上面两种会漏网 —— 2026-09-21 实测 replace 跑到第 9 天就这么崩的
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise NotionError(f'{type(e).__name__}: {e}') from None


def whoami():
    return _req('GET', 'https://api.notion.com/v1/users/me').get('name', '')


# --------------------------------------------------------------- 本地记账

def ensure_table():
    with idx.LOCK:
        idx.connect().execute("""
            CREATE TABLE IF NOT EXISTS notion_pages (
                date     TEXT PRIMARY KEY,
                page_id  TEXT NOT NULL,
                mode     TEXT NOT NULL,      -- create | append
                hash     TEXT NOT NULL,      -- 推上去那版内容的指纹
                pushed_at TEXT NOT NULL
            )""")
        idx.connect().commit()


def _record(date, page_id, mode, h):
    with idx.LOCK:
        c = idx.connect()
        c.execute('INSERT OR REPLACE INTO notion_pages VALUES (?,?,?,?,?)',
                  (date, page_id, mode, h, datetime.now().isoformat(timespec='seconds')))
        c.commit()


def recorded():
    """date -> {page_id, mode, hash}"""
    ensure_table()
    with idx.LOCK:
        return {r['date']: dict(r) for r in
                idx.connect().execute('SELECT * FROM notion_pages')}


# ------------------------------------------------------------ 日期与标题

def notion_title(date):
    """'2025-08-21' -> '2025-8-21'（不补零，跟他自己的习惯一致）"""
    y, m, d = date.split('-')
    return f'{int(y)}-{int(m)}-{int(d)}'


def parse_title(title):
    """'2025-8-21' / '2025/08/21' -> '2025-08-21'；不是日期就 None"""
    m = DATE_RE.match(title or '')
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f'{y:04d}-{mo:02d}-{d:02d}'


# --------------------------------------------------------------- 扫远端

_SCAN_CACHE = {'at': 0.0, 'data': None}
SCAN_TTL = 90        # 扫一遍要 12 次 API 调用 ~10s，UI 是 1.5s 轮询一次，必须缓存


def scan_remote(progress=None, fresh=False):
    """遍历整个 data source，按日期归类。

    返回 {'ours': {date: page_id}, 'theirs': {date: [page_id, ...]}, 'total': n}
    ours   = Ephemeris 推的（icon 💬 且标题是裸日期）
    theirs = 他自己写的（标题是裸日期，icon 不是 💬）
    """
    if not fresh and _SCAN_CACHE['data'] and time.time() - _SCAN_CACHE['at'] < SCAN_TTL:
        return _SCAN_CACHE['data']
    ours, theirs, total, cur = {}, {}, 0, None
    while True:
        body = {'page_size': 100}
        if cur:
            body['start_cursor'] = cur
        res = _req('POST', f'https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query', body)
        for p in res.get('results', []):
            total += 1
            t = (p.get('properties', {}).get(TITLE_PROP) or {}).get('title') or []
            date = parse_title(''.join(x.get('plain_text', '') for x in t))
            if not date:
                continue
            if (p.get('icon') or {}).get('emoji') == ICON:
                ours[date] = p['id']
            else:
                theirs.setdefault(date, []).append(p['id'])
        if progress:
            progress(total)
        if not res.get('has_more'):
            break
        cur = res.get('next_cursor')
    data = {'ours': ours, 'theirs': theirs, 'total': total}
    _SCAN_CACHE.update(at=time.time(), data=data)
    return data


def invalidate_scan():
    _SCAN_CACHE.update(at=0.0, data=None)


# --------------------------------------------------------------- 日记取数

def load_diaries(conn=None, dates=None):
    conn = conn or idx.connect()
    sql = 'SELECT * FROM diaries'
    args = []
    if dates:
        sql += ' WHERE date IN (%s)' % ','.join('?' * len(dates))
        args = list(dates)
    out = []
    with idx.LOCK:
        rows = list(conn.execute(sql + ' ORDER BY date', args))
    for r in rows:
        d = dict(r)
        for k in ('topics', 'artifacts', 'open_loops'):
            try:
                d[k] = json.loads(d.get(k) or '[]')
            except (TypeError, ValueError):
                d[k] = []
        with idx.LOCK:
            st = conn.execute("""SELECT COUNT(DISTINCT conv_id) threads, COUNT(*) msgs,
                                        SUM(chars) chars, MIN(hhmm) a, MAX(hhmm) b
                                   FROM messages WHERE date=? AND role='human'""",
                              (d['date'],)).fetchone()
        d['stats'] = dict(st) if st else {}
        out.append(d)
    return out


# --------------------------------------------------------------- block 构造

def _rt(text):
    """单个 rich_text 上限 2000 字符，超了切开。"""
    out, s = [], text or ''
    while s:
        out.append({'type': 'text', 'text': {'content': s[:2000]}})
        s = s[2000:]
    return out or [{'type': 'text', 'text': {'content': ''}}]


def _para(t):
    return {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': _rt(t)}}


def _bullet(t):
    return {'object': 'block', 'type': 'bulleted_list_item',
            'bulleted_list_item': {'rich_text': _rt(t)}}


def _h3(t):
    return {'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': _rt(t)}}


def footer_text(d):
    s = d.get('stats') or {}
    foot = (f"{s.get('threads', 0)} 个对话窗口 · {s.get('msgs', 0)} 条消息 · "
            f"{(s.get('chars') or 0):,} 字 · {s.get('a', '')}–{s.get('b', '')}"
            + (f" · {d['mood']}" if d.get('mood') else ''))
    if d.get('topics'):
        foot += '\n' + ' · '.join(d['topics'])
    return foot


def build_blocks(d, append=False):
    """append=True 时前面加一条分隔线，跟他自己写的正文隔开。"""
    blocks = []
    if append:
        blocks.append({'object': 'block', 'type': 'divider', 'divider': {}})
    blocks += [_para(p.strip()) for p in (d.get('narrative') or '').split('\n\n') if p.strip()]
    if d.get('artifacts'):
        blocks.append(_h3('做出来的'))
        blocks += [_bullet(x) for x in d['artifacts']]
    if d.get('open_loops'):
        blocks.append(_h3('没做完的'))
        blocks += [_bullet(x) for x in d['open_loops']]
    blocks.append({'object': 'block', 'type': 'callout',
                   'callout': {'rich_text': _rt(footer_text(d)),
                               'icon': {'emoji': ICON}}})
    return blocks


def content_hash(d):
    """日记重跑过就会变，用来决定要不要重推。"""
    payload = json.dumps({k: d.get(k) for k in
                          ('narrative', 'artifacts', 'open_loops', 'topics', 'mood')},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ 写入

def _append_children(page_id, blocks):
    while blocks:
        _req('PATCH', f'https://api.notion.com/v1/blocks/{page_id}/children',
             {'children': blocks[:100]})
        blocks = blocks[100:]


def create_page(d):
    blocks = build_blocks(d)
    page = {
        'parent': {'type': 'data_source_id', 'data_source_id': DATA_SOURCE_ID},
        'icon': {'type': 'emoji', 'emoji': ICON},
        'properties': {TITLE_PROP: {'title': _rt(notion_title(d['date']))}},
        'children': blocks[:100],
    }
    pid = _req('POST', 'https://api.notion.com/v1/pages', page)['id']
    _append_children(pid, blocks[100:])
    return pid


def append_page(page_id, d):
    """追加到他自己写的那页末尾。不动属性、不动 icon、不删任何东西。"""
    _append_children(page_id, build_blocks(d, append=True))
    return page_id


def archive_page(page_id):
    """扔进 Notion 废纸篓。可恢复，不是硬删。只对 Ephemeris 自己建的页用。"""
    try:
        return _req('PATCH', f'https://api.notion.com/v1/pages/{page_id}',
                    {'in_trash': True})
    except NotionError:
        return _req('PATCH', f'https://api.notion.com/v1/pages/{page_id}',
                    {'archived': True})


def fix_duplicates(progress=None, dry=False):
    """修 2026-09-19 那次的错：有 28 天他自己已经写过日记，却被新建了一页。

    对每个这样的日期：把 Ephemeris 建的那页扔废纸篓，内容改为追加到他那页。
    只动 icon 是 💬 的页，绝不碰他自己写的。
    """
    ensure_table()
    remote = scan_remote(fresh=True)
    dupes = sorted(set(remote['ours']) & set(remote['theirs']))
    if dry:
        return {'dupes': dupes, 'total': len(dupes), 'ok': 0, 'failed': []}

    by_date = {d['date']: d for d in load_diaries(dates=dupes)}
    ok, failed = 0, []
    for i, date in enumerate(dupes, 1):
        d = by_date.get(date)
        if not d:
            continue
        try:
            append_page(remote['theirs'][date][0], d)          # 先写，写成了再删
            archive_page(remote['ours'][date])
            _record(date, remote['theirs'][date][0], 'append', content_hash(d))
            ok += 1
        except NotionError as e:
            failed.append({'date': date, 'error': str(e)})
        if progress:
            progress(i, len(dupes), date, 'fix')
        time.sleep(0.35)
    invalidate_scan()
    return {'total': len(dupes), 'ok': ok, 'failed': failed}


OUR_BLOCK_TYPES = {'paragraph', 'heading_3', 'bulleted_list_item', 'divider'}
FOOT_RE = re.compile(r'^\d+\s*个对话窗口\s*·')


def _all_blocks(page_id):
    out, cur = [], None
    while True:
        url = f'https://api.notion.com/v1/blocks/{page_id}/children?page_size=100'
        if cur:
            url += f'&start_cursor={cur}'
        r = _req('GET', url)
        out += r.get('results', [])
        if not r.get('has_more'):
            return out
        cur = r.get('next_cursor')


def _plain(b):
    t = (b.get(b.get('type'), {}) or {}).get('rich_text') or []
    return ''.join(x.get('plain_text', '') for x in t)


def find_our_span(blocks):
    """在他自己写的页面里，找出哪几块是 Ephemeris 追加的。

    追加的形状固定：divider → 正文段落 → [做出来的/没做完的] → 带 💬 的 callout。
    **从 callout 往回找最近的 divider**，不能从头找第一条 ——
    他自己的模板里也有 divider（实测有页面第 4 块就是模板自带的）。

    认不准就返回 None，宁可不动。
    """
    ends = [i for i, b in enumerate(blocks)
            if b.get('type') == 'callout'
            and (b['callout'].get('icon') or {}).get('emoji') == ICON
            and FOOT_RE.match(_plain(b).strip())]
    if not ends:
        # 没有 callout 的兜底：上一次写到一半断了（删了旧的、新的没写完），页面就是这个样子。
        # 只在「最后一条 divider 之后全是我们会产出的块型，且带 做出来的/没做完的 小标题」
        # 时才认 —— 这两个条件同时成立，基本不可能是他自己写的东西。
        last_div = next((i for i in range(len(blocks) - 1, -1, -1)
                         if blocks[i].get('type') == 'divider'), None)
        if last_div is None or last_div == len(blocks) - 1:
            return None
        tail = blocks[last_div + 1:]
        if any(b.get('type') not in OUR_BLOCK_TYPES for b in tail):
            return None
        if not any(b.get('type') == 'heading_3' and _plain(b).strip() in ('做出来的', '没做完的')
                   for b in tail):
            return None
        return [(last_div, len(blocks) - 1)]
    spans = []
    for e in ends:
        start = next((i for i in range(e - 1, -1, -1)
                      if blocks[i].get('type') == 'divider'), None)
        if start is None:
            return None
        # 中间只能是我们会产出的块型，混进别的就说明认错了
        if any(blocks[i].get('type') not in OUR_BLOCK_TYPES for i in range(start + 1, e)):
            return None
        spans.append((start, e))
    # 从后往前删，索引不会错位
    return sorted(spans, reverse=True)


def replace_append(page_id, d):
    """把他页面里属于 Ephemeris 的那段换成新版。他自己写的一个字不动。

    日记重跑之后要用这个，不能再 append 一次 —— 那样他页面上会叠两份。
    """
    blocks = _all_blocks(page_id)
    spans = find_our_span(blocks)
    if spans is None:
        raise NotionError('认不出哪段是 Ephemeris 写的，跳过（没动这页）')
    for start, end in spans:
        for i in range(end, start - 1, -1):
            _req('DELETE', f"https://api.notion.com/v1/blocks/{blocks[i]['id']}")
            time.sleep(0.2)
    _append_children(page_id, build_blocks(d, append=True))
    return page_id


def clear_ours(page_id):
    """重推前把 Ephemeris 自己建的那页清空（只对 create 出来的页用）。"""
    res = _req('GET', f'https://api.notion.com/v1/blocks/{page_id}/children?page_size=100')
    for b in res.get('results', []):
        try:
            _req('DELETE', f"https://api.notion.com/v1/blocks/{b['id']}")
        except NotionError:
            pass


# ------------------------------------------------------------------ 编排

def plan(remote=None, fresh=False):
    """算出每天该干什么。返回 {date: action}。

    action ∈ create / append / update / stale / skip

    **本地表优先于远端扫描**：扫描有 90s 缓存，刚推完的那几天还没进缓存，
    要是先看远端就会把已经推好的判成「没推」，白推一遍。

    replace = 那天的日记重跑过，而内容是追加在他自己写的页面里的。
    不能再 append（会叠两份），走 replace_append：只删掉属于 Ephemeris 的那几块再写新的。
    """
    ensure_table()
    remote = remote or scan_remote(fresh=fresh)
    seen = recorded()
    out = {}
    for d in load_diaries():
        date, h = d['date'], content_hash(d)
        rec = seen.get(date)
        if rec:
            if rec['hash'] == h:
                out[date] = 'skip'
            else:
                out[date] = 'update' if rec['mode'] == 'create' else 'replace'
        elif date in remote['ours']:
            out[date] = 'update'          # 我们建的，但本地没记账（历史遗留）
        elif date in remote['theirs']:
            out[date] = 'append'
        else:
            out[date] = 'create'
    return out, remote, seen


def sync(only=None, progress=None, limit=0, dry=False):
    """把待办的推上去。progress(done, total, date, action) 会被逐条调用。"""
    ensure_table()
    actions, remote, seen = plan(fresh=True)   # 开跑前拿最新的，别用缓存
    todo = [(dt, a) for dt, a in sorted(actions.items())
            if a != 'skip' and (not only or a in only)]
    if limit:
        todo = todo[:limit]
    if dry:
        return {'todo': todo, 'total': len(todo), 'ok': 0, 'failed': []}

    by_date = {d['date']: d for d in load_diaries(dates=[dt for dt, _ in todo])}
    ok, failed = 0, []
    for i, (date, action) in enumerate(todo, 1):
        d = by_date.get(date)
        if not d:
            continue
        try:
            if action == 'create':
                pid = create_page(d)
            elif action == 'append':
                pid = append_page(remote['theirs'][date][0], d)
            elif action == 'replace':
                pid = replace_append((seen.get(date) or {}).get('page_id')
                                     or remote['theirs'][date][0], d)
            else:  # update：我们自己建的页，清空重写
                pid = (seen.get(date) or {}).get('page_id') or remote['ours'][date]
                clear_ours(pid)
                _append_children(pid, build_blocks(d))
            _record(date, pid, 'create' if action in ('create', 'update') else 'append',
                    content_hash(d))
            ok += 1
        except NotionError as e:
            failed.append({'date': date, 'error': str(e)})
        if progress:
            progress(i, len(todo), date, action)
        time.sleep(0.35)          # Notion 限速 3 req/s，留余量
    invalidate_scan()
    return {'total': len(todo), 'ok': ok, 'failed': failed}


def status():
    """给 UI 用的一览。"""
    ensure_table()
    if not token():
        return {'connected': False}
    try:
        who = whoami()
        actions, remote, _ = plan()
    except NotionError as e:
        return {'connected': False, 'error': str(e)}
    n = {'create': 0, 'append': 0, 'update': 0, 'replace': 0, 'skip': 0}
    for a in actions.values():
        n[a] = n.get(a, 0) + 1
    return {'connected': True, 'integration': who,
            'remote_pages': remote['total'],
            'diaries': len(actions), **n}


# -------------------------------------------------------------------- CLI

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--dry', action='store_true', help='只看要干什么，不写')
    ap.add_argument('--only', help='逗号分隔：create,append,update')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--fix-dupes', action='store_true',
                    help='把误建的重复页扔废纸篓，内容改追加到他自己那页')
    args = ap.parse_args()

    if args.fix_dupes:
        def show_fix(i, total, date, _):
            print(f'  [{i}/{total}] {date}', flush=True)
        r = fix_duplicates(progress=None if args.dry else show_fix, dry=args.dry)
        if args.dry:
            print(f"{r['total']} 天重复：{r['dupes']}")
        else:
            print(f"\n修好 {r['ok']}/{r['total']}")
            for f in r['failed']:
                print(f"  失败 {f['date']}: {f['error']}")
        raise SystemExit

    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
        raise SystemExit

    only = args.only.split(',') if args.only else None
    if args.dry:
        r = sync(only=only, limit=args.limit, dry=True)
        cnt = {}
        for _, a in r['todo']:
            cnt[a] = cnt.get(a, 0) + 1
        print(f"待办 {r['total']} 天：{cnt}")
        for dt, a in r['todo'][:15]:
            print(f'   {a:7} {dt}')
        if r['total'] > 15:
            print(f'   … 还有 {r["total"] - 15} 天')
        raise SystemExit

    def show(i, total, date, action):
        print(f'  [{i}/{total}] {action:7} {date}', flush=True)

    r = sync(only=only, progress=show, limit=args.limit)
    print(f"\n完成 {r['ok']}/{r['total']}")
    for f in r['failed']:
        print(f"  失败 {f['date']}: {f['error']}")
