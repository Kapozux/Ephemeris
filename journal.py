"""手写日记：把我自己在 Notion 里写的日期页读回本地。

AI 日记是从「找 AI 解决问题」的聊天里推出来的，天然偏问题、偏负面，也看不到不跟 AI 聊的生活。
手写日记是我自己写的另一条线：时间更早（最早 2022-11），也补得上没聊天的日子。

**只读。** Notion 那边一个字不动；本地单独一张表，不跟聊天消息混，也不改任何 AI 日记。

**去掉我们自己写的那段。** 有些日期页后面接着 Ephemeris 推上去的日记（divider → 正文 → 💬 callout），
直接读会把 AI 日记当成手写的读回来。认段复用 `to_notion.find_our_span`。

**增量。** 列全库时拿每页的 last_edited_time，跟本地比，没变的不拉正文。
"""
import re
import time
from datetime import datetime

import index as idx
import to_notion as tn

MAX_DEPTH = 3            # 嵌套块（toggle、列表子项）往下读几层


def ensure_tables():
    with idx.LOCK:
        db = idx.connect()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS journal (
          page_id     TEXT PRIMARY KEY,
          date        TEXT NOT NULL,
          title       TEXT,
          text        TEXT,
          chars       INTEGER,
          stripped    INTEGER DEFAULT 0,   -- 去掉了几段 Ephemeris 追加的内容
          last_edited TEXT,
          fetched_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS journal_date ON journal(date);
        CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
          text, date UNINDEXED, page_id UNINDEXED, tokenize='unicode61'
        );
        """)
        db.commit()


def _q(sql, *args):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, args).fetchall()]


# ------------------------------------------------------------------ Notion → 文本

def _rich(b):
    body = b.get(b.get('type'), {}) or {}
    return ''.join(x.get('plain_text', '') for x in body.get('rich_text') or [])


def _children(block_id):
    out, cur = [], None
    while True:
        url = f'https://api.notion.com/v1/blocks/{block_id}/children?page_size=100'
        if cur:
            url += f'&start_cursor={cur}'
        res = tn._req('GET', url)
        out += res.get('results', [])
        if not res.get('has_more'):
            return out
        cur = res.get('next_cursor')


def _line(b):
    t, txt = b.get('type'), _rich(b)
    body = b.get(t, {}) or {}
    if t in ('heading_1', 'heading_2', 'heading_3'):
        return '#' * int(t[-1]) + ' ' + txt
    if t in ('bulleted_list_item', 'numbered_list_item'):
        return '- ' + txt
    if t == 'to_do':
        return ('☑ ' if body.get('checked') else '☐ ') + txt
    if t == 'quote':
        return '> ' + txt
    if t == 'callout':
        emoji = (body.get('icon') or {}).get('emoji') or ''
        return (emoji + ' ' + txt).strip()
    if t == 'toggle':
        return '▸ ' + txt
    if t == 'divider':
        return '———'
    if t == 'equation':
        return body.get('expression', '')
    if t in ('image', 'video', 'file', 'pdf', 'audio'):
        cap = ''.join(x.get('plain_text', '') for x in body.get('caption') or [])
        return f'[{ {"image": "图片", "video": "视频", "audio": "音频"}.get(t, "文件") }{"：" + cap if cap else ""}]'
    if t in ('bookmark', 'embed', 'link_preview'):
        return body.get('url', '')
    if t == 'child_page':
        return f'[子页面：{body.get("title", "")}]'
    if t == 'table_row':
        return ' | '.join(''.join(x.get('plain_text', '') for x in cell) for cell in body.get('cells') or [])
    return txt                      # paragraph、code 以及其它带 rich_text 的


def _render(blocks, depth=0):
    lines = []
    for b in blocks:
        s = _line(b)
        if s or b.get('type') == 'paragraph':
            lines.append('  ' * depth + s)
        if b.get('has_children') and depth < MAX_DEPTH and b.get('type') != 'child_page':
            lines += _render(_children(b['id']), depth + 1)
    return lines


def _strip_ours(blocks):
    """去掉页面里 Ephemeris 追加的那几段。返回 (剩下的块, 去掉了几段)。"""
    has_ours = any(b.get('type') == 'callout'
                   and ((b.get('callout') or {}).get('icon') or {}).get('emoji') == tn.ICON
                   for b in blocks)
    spans = tn.find_our_span(blocks)
    if spans:
        keep = list(blocks)
        for s, e in spans:                     # find_our_span 已经按从后往前排好
            del keep[s:e + 1]
        return keep, len(spans)
    if has_ours:
        # 认不出完整形状但确实有我们的 callout：从它前面最近的 divider 起全部不要，宁可少读
        first = next(i for i, b in enumerate(blocks)
                     if b.get('type') == 'callout'
                     and ((b.get('callout') or {}).get('icon') or {}).get('emoji') == tn.ICON)
        cut = next((i for i in range(first, -1, -1) if blocks[i].get('type') == 'divider'), first)
        return blocks[:cut], 1
    return blocks, 0


def page_text(page_id):
    blocks, n = _strip_ours(_children(page_id))
    text = '\n'.join(_render(blocks))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text, n


# ------------------------------------------------------------------ 同步

def list_pages():
    """全库里我自己写的日期页：[{page_id, date, title, last_edited}]。Ephemeris 建的（💬）不算。"""
    out, cur = [], None
    while True:
        body = {'page_size': 100}
        if cur:
            body['start_cursor'] = cur
        res = tn._req('POST', f'https://api.notion.com/v1/data_sources/{tn.DATA_SOURCE_ID}/query', body)
        for p in res.get('results', []):
            t = (p.get('properties', {}).get(tn.TITLE_PROP) or {}).get('title') or []
            title = ''.join(x.get('plain_text', '') for x in t)
            date = tn.parse_title(title)
            if not date or (p.get('icon') or {}).get('emoji') == tn.ICON:
                continue
            out.append({'page_id': p['id'], 'date': date, 'title': title,
                        'last_edited': p.get('last_edited_time', '')})
        if not res.get('has_more'):
            return out
        cur = res.get('next_cursor')


def sync(full=False, say=print):
    """拉新的和改过的，删掉 Notion 里已经没了的。返回 {'pages', 'fetched', 'removed', 'failed'}。"""
    ensure_tables()
    pages = list_pages()
    have = {r['page_id']: r['last_edited'] for r in _q('SELECT page_id, last_edited FROM journal')}
    todo = [p for p in pages if full or have.get(p['page_id']) != p['last_edited']]
    gone = set(have) - {p['page_id'] for p in pages}
    say(f'手写日记：Notion 里 {len(pages)} 页，要拉 {len(todo)} 页，要删 {len(gone)} 页')

    failed = []
    for i, p in enumerate(todo, 1):
        try:
            text, n = page_text(p['page_id'])
        except Exception as e:                               # noqa: BLE001
            failed.append((p['date'], f'{type(e).__name__}: {e}'[:200]))
            say(f"  {i}/{len(todo)} {p['date']} 失败：{failed[-1][1]}")
            continue
        now = datetime.now().isoformat(timespec='seconds')
        with idx.LOCK:
            db = idx.connect()
            db.execute('INSERT OR REPLACE INTO journal VALUES (?,?,?,?,?,?,?,?)',
                       (p['page_id'], p['date'], p['title'], text, len(text), n, p['last_edited'], now))
            db.execute('DELETE FROM journal_fts WHERE page_id=?', (p['page_id'],))
            if text:
                db.execute('INSERT INTO journal_fts(text, date, page_id) VALUES (?,?,?)',
                           (text, p['date'], p['page_id']))
            db.commit()
        if i % 20 == 0 or i == len(todo):
            say(f'  {i}/{len(todo)}')
        time.sleep(0.2)                                      # Notion 限流约 3 次/秒

    if gone:
        with idx.LOCK:
            db = idx.connect()
            for pid in gone:
                db.execute('DELETE FROM journal WHERE page_id=?', (pid,))
                db.execute('DELETE FROM journal_fts WHERE page_id=?', (pid,))
            db.commit()
    return {'pages': len(pages), 'fetched': len(todo) - len(failed),
            'removed': len(gone), 'failed': failed}


# ------------------------------------------------------------------ 读

def for_date(date):
    ensure_tables()
    rows = _q('SELECT page_id, title, text, chars, stripped FROM journal WHERE date=? ORDER BY title', date)
    for r in rows:
        r['url'] = 'https://www.notion.so/' + r['page_id'].replace('-', '')
    return rows


def dates():
    """{date: 字数}，只算有正文的。"""
    ensure_tables()
    return {r['date']: r['c'] for r in
            _q('SELECT date, SUM(chars) c FROM journal WHERE chars > 0 GROUP BY date')}


def search(kw, limit=50):
    ensure_tables()
    safe = '"' + kw.replace('"', '""') + '"'
    return _q("""SELECT date, page_id, snippet(journal_fts, 0, '<mark>', '</mark>', '…', 20) snip
                   FROM journal_fts WHERE journal_fts MATCH ? ORDER BY date DESC LIMIT ?""", safe, limit)


if __name__ == '__main__':
    import sys
    r = sync(full='--full' in sys.argv, say=lambda m: print(m, flush=True))
    print('DONE', {k: (v if k != 'failed' else len(v)) for k, v in r.items()})
    for d, why in r['failed']:
        print('  失败', d, why)
