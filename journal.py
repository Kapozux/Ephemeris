"""手写日记：把我自己在 Notion 里写的日期页读回本地。

AI 日记是从「找 AI 解决问题」的聊天里推出来的，天然偏问题、偏负面，也看不到不跟 AI 聊的生活。
手写日记是我自己写的另一条线：时间更早（最早 2022-11），也补得上没聊天的日子。

**只读。** Notion 那边一个字不动；本地单独一张表，不跟聊天消息混，也不改任何 AI 日记。

**去掉我们自己写的那段。** 有些日期页后面接着 Ephemeris 推上去的日记（divider → 正文 → 💬 callout），
直接读会把 AI 日记当成手写的读回来。认段复用 `to_notion.find_our_span`。

**增量。** 列全库时拿每页的 last_edited_time，跟本地比，没变的不拉正文。

**格式和图片（2026-10-03）。** 存的是 Markdown：标题、列表（有序的带编号）、待办、引用、粗体、链接；
图片当场下载到 journal_files/（Notion 的图片链接一小时就失效），正文里写成 ![说明](/journal-files/页/文件)。
喂模型、建搜索索引、数字数一律用 plain()：跟以前存的纯文字一模一样，所以加了格式也不会让任何东西重算。

**及时。** 早上 08:30 那一串全量对一遍；平时打开 / 切回 Ephemeris 时 refresh_recent() 只看最近改过的
那几页（一次查询，不调模型）—— 晚上在 Notion 写完，回来就能看到。

**消化过没有看正文，不看修改时间。** 主题账本（模型）只补正文真的变了的页：digested 记着上次消化时正文的指纹。
以前看 last_edited：Ephemeris 每天往你那页末尾推 AI 日记也会改它，第二天就把没变的手写重新抽一遍主题。
"""
import hashlib
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime

import store
import to_notion as tn

MAX_DEPTH = 3            # 嵌套块（toggle、列表子项）往下读几层
HERE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(HERE, 'journal_files')     # 日记里的图片（白名单 .gitignore 挡着，不进仓库；备份里有）
FILES_URL = '/journal-files/'
MAX_IMAGE = 30 * 1024 * 1024


# ------------------------------------------------------------------ Notion → 文本

def _rich(b):
    """块里的文字，带格式：粗体、删除线、行内代码、链接（斜体、下划线、颜色不要）。"""
    body = b.get(b.get('type'), {}) or {}
    out = []
    for x in body.get('rich_text') or []:
        t = x.get('plain_text', '')
        core = t.strip()
        if not core:
            out.append(t)
            continue
        lead, trail = t[:len(t) - len(t.lstrip())], t[len(t.rstrip()):]   # 空格放到标记外面，不然渲染不出来
        a = x.get('annotations') or {}
        if a.get('code'):
            core = f'`{core}`'
        if a.get('bold'):
            core = f'**{core}**'
        if a.get('strikethrough'):
            core = f'~~{core}~~'
        href = x.get('href') or ''
        if href.startswith(('http://', 'https://', 'mailto:')):
            core = f'[{core}]({href})'
        out.append(lead + core + trail)
    return ''.join(out)


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


def _line(b, num=0, image=None):
    """一个块 → 一行。num：有序列表的第几项；image(b)：把图片存到本地，返回能显示的地址。"""
    t, txt = b.get('type'), _rich(b)
    body = b.get(t, {}) or {}
    if t in ('heading_1', 'heading_2', 'heading_3'):
        return '#' * int(t[-1]) + ' ' + txt
    if t == 'numbered_list_item':
        return f'{num}. ' + txt
    if t == 'bulleted_list_item':
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
        url = image(b) if t == 'image' and image else None
        if url:
            return f'![{cap}]({url})'
        return f'[{ {"image": "图片", "video": "视频", "audio": "音频"}.get(t, "文件") }{"：" + cap if cap else ""}]'
    if t in ('bookmark', 'embed', 'link_preview'):
        return body.get('url', '')
    if t == 'child_page':
        return f'[子页面：{body.get("title", "")}]'
    if t == 'table_row':
        return ' | '.join(''.join(x.get('plain_text', '') for x in cell) for cell in body.get('cells') or [])
    return txt                      # paragraph、code 以及其它带 rich_text 的


def _render(blocks, depth=0, image=None):
    lines, num = [], 0
    for b in blocks:
        num = num + 1 if b.get('type') == 'numbered_list_item' else 0      # 有序列表：连着的才接着数
        s = _line(b, num, image)
        if s or b.get('type') == 'paragraph':
            lines.append('  ' * depth + s)
        if b.get('has_children') and depth < MAX_DEPTH and b.get('type') != 'child_page':
            lines += _render(_children(b['id']), depth + 1, image)
    return lines


def _save_image(page_id, b):
    """图片存到 journal_files/<页>/，返回本地地址；下载不了返回 None（正文里退回成「[图片]」）。
    文件名用图片地址的指纹：同一张图不重复下；换了图就是新文件。"""
    body = b.get('image') or {}
    src = (body.get(body.get('type') or '') or {}).get('url') or ''
    if not src.startswith(('http://', 'https://')):
        return None
    key = hashlib.sha1(urllib.parse.urlsplit(src).path.encode()).hexdigest()[:16]
    folder = os.path.join(FILES_DIR, page_id)
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):
        if f.startswith(key + '.'):
            return f'{FILES_URL}{page_id}/{f}'
    try:
        with urllib.request.urlopen(urllib.request.Request(src, headers={'User-Agent': 'Ephemeris'}), timeout=60) as r:
            data = r.read(MAX_IMAGE + 1)
            ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip()
    except Exception as e:                                   # noqa: BLE001
        print(f'[journal] 图片下载失败 {page_id[:8]}：{type(e).__name__}: {e}', flush=True)
        return None
    if len(data) > MAX_IMAGE:
        return None
    ext = os.path.splitext(urllib.parse.urlsplit(src).path)[1].lower()
    if not re.fullmatch(r'\.[a-z0-9]{2,5}', ext):
        ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp',
               'image/heic': '.heic', 'image/heif': '.heif', 'image/svg+xml': '.svg'}.get(ctype, '.jpg')
    path = os.path.join(folder, key + ext)
    with open(path, 'wb') as f:
        f.write(data)
    if ext in ('.heic', '.heif'):                            # 手机照片：浏览器显示不了，转成 jpg（macOS 自带 sips）
        jpg = os.path.join(folder, key + '.jpg')
        if subprocess.run(['sips', '-s', 'format', 'jpeg', path, '--out', jpg], capture_output=True).returncode == 0:
            os.remove(path)
            path = jpg
    return f'{FILES_URL}{page_id}/{os.path.basename(path)}'


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
    """一页 → (Markdown 正文, 去掉了几段 Ephemeris 的)。图片顺手下载下来。"""
    blocks, n = _strip_ours(_children(page_id))
    text = '\n'.join(_render(blocks, image=lambda b: _save_image(page_id, b)))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text, n


_IMG = re.compile(r'!\[([^\]\n]*)\]\((?:[^)\s]+)\)')
_LINK = re.compile(r'\[([^\]\n]+)\]\((?:https?://|mailto:)[^)\s]*\)')


def plain(text):
    """Markdown 正文 → 纯文字，跟 2026-10-03 以前存的一模一样（图片回到「[图片：说明]」、链接只留字、
    去掉粗体 / 删除线 / 代码的记号、有序列表回到「- 」）。喂模型、建索引、数字数都用它。"""
    t = _IMG.sub(lambda m: f'[图片：{m.group(1)}]' if m.group(1) else '[图片]', text or '')
    t = _LINK.sub(r'\1', t)
    t = re.sub(r'(\*\*|~~)(.+?)\1', r'\2', t)
    t = re.sub(r'`([^`\n]+)`', r'\1', t)
    return re.sub(r'^(\s*)\d+\. ', r'\1- ', t, flags=re.M)


def digest(text):
    """正文的指纹（按纯文字算：只是格式变了不算变）。"""
    return hashlib.sha1(plain(text).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ 同步

def _ours(p):
    """查询结果里的一页 → {page_id, date, title, last_edited}；不是日期页、或是 Ephemeris 建的（💬）返回 None。"""
    t = (p.get('properties', {}).get(tn.TITLE_PROP) or {}).get('title') or []
    title = ''.join(x.get('plain_text', '') for x in t)
    date = tn.parse_title(title)
    if not date or (p.get('icon') or {}).get('emoji') == tn.ICON:
        return None
    return {'page_id': p['id'], 'date': date, 'title': title, 'last_edited': p.get('last_edited_time', '')}


def list_pages():
    """全库里我自己写的日期页：[{page_id, date, title, last_edited}]。"""
    out, cur = [], None
    while True:
        body = {'page_size': 100}
        if cur:
            body['start_cursor'] = cur
        res = tn._req('POST', f'https://api.notion.com/v1/data_sources/{tn.DATA_SOURCE_ID}/query', body)
        out += [x for x in map(_ours, res.get('results', [])) if x]
        if not res.get('has_more'):
            return out
        cur = res.get('next_cursor')


def _store(p, text, n):
    """存一页。digested 不动：新页是空的（要消化），老页留着上次消化时的指纹 —— 正文真变了才对不上。"""
    now = datetime.now().isoformat(timespec='seconds')
    flat = plain(text)
    with store.tx() as db:
        db.execute("""INSERT INTO journal(page_id, date, title, text, chars, stripped, last_edited, fetched_at)
                      VALUES (?,?,?,?,?,?,?,?)
                      ON CONFLICT(page_id) DO UPDATE SET date=excluded.date, title=excluded.title, text=excluded.text,
                        chars=excluded.chars, stripped=excluded.stripped, last_edited=excluded.last_edited,
                        fetched_at=excluded.fetched_at""",
                   (p['page_id'], p['date'], p['title'], text, len(flat), n, p['last_edited'], now))
        db.execute('DELETE FROM journal_fts WHERE page_id=?', (p['page_id'],))
        if flat:
            db.execute('INSERT INTO journal_fts(text, date, page_id) VALUES (?,?,?)', (flat, p['date'], p['page_id']))


def sync(full=False, say=print):
    """拉新的和改过的，删掉 Notion 里已经没了的。
    返回 {'pages', 'fetched', 'removed', 'failed', 'dates', 'gone'}：dates 这次拉过的、gone 在 Notion 里删掉了的日期。
    要不要重新记进账本看 dirty_dates()（正文变没变），不看这里。"""
    pages = list_pages()
    have = {r['page_id']: r['last_edited'] for r in store.q('SELECT page_id, last_edited FROM journal')}
    todo = [p for p in pages if full or have.get(p['page_id']) != p['last_edited']]
    gone = set(have) - {p['page_id'] for p in pages}
    gone_dates = {r['date'] for r in store.q('SELECT page_id, date FROM journal') if r['page_id'] in gone}
    changed = set()
    say(f'手写日记：Notion 里 {len(pages)} 页，要拉 {len(todo)} 页，要删 {len(gone)} 页')

    failed = []
    for i, p in enumerate(todo, 1):
        try:
            text, n = page_text(p['page_id'])
        except Exception as e:                               # noqa: BLE001
            failed.append((p['date'], f'{type(e).__name__}: {e}'[:200]))
            say(f"  {i}/{len(todo)} {p['date']} 失败：{failed[-1][1]}")
            continue
        _store(p, text, n)
        changed.add(p['date'])
        if i % 20 == 0 or i == len(todo):
            say(f'  {i}/{len(todo)}')
        time.sleep(0.2)                                      # Notion 限流约 3 次/秒

    if gone:
        with store.LOCK:
            db = store.connect()
            for pid in gone:
                db.execute('DELETE FROM journal WHERE page_id=?', (pid,))
                db.execute('DELETE FROM journal_fts WHERE page_id=?', (pid,))
            db.commit()
    return {'pages': len(pages), 'fetched': len(todo) - len(failed),
            'removed': len(gone), 'failed': failed, 'dates': sorted(changed), 'gone': sorted(gone_dates)}


_recent = {'at': 0.0}


def refresh_recent(n=20, min_gap=120):
    """平时打开 / 切回 Ephemeris 时调：只看 Notion 里最近改过的 n 页，有新的就拉下来。
    一次查询、不调模型；两分钟内最多一次。返回 {'changed': [日期]}（或 skipped / disabled）。"""
    if not tn.token() or not tn.DATA_SOURCE_ID:
        return {'disabled': True}
    if time.time() - _recent['at'] < min_gap:
        return {'skipped': True}
    _recent['at'] = time.time()
    res = tn._req('POST', f'https://api.notion.com/v1/data_sources/{tn.DATA_SOURCE_ID}/query',
                  {'page_size': n, 'sorts': [{'timestamp': 'last_edited_time', 'direction': 'descending'}]})
    have = {r['page_id']: r['last_edited'] for r in store.q('SELECT page_id, last_edited FROM journal')}
    changed = []
    for p in filter(None, map(_ours, res.get('results', []))):
        if have.get(p['page_id']) == p['last_edited']:
            continue
        text, k = page_text(p['page_id'])
        _store(p, text, k)
        changed.append(p['date'])
    return {'changed': sorted(set(changed))}


# ------------------------------------------------------------------ 消化（记进主题账本）

def dirty_dates():
    """正文跟上次消化时不一样的日期（新写的、改过的）。早上那一串据此补主题账本。"""
    return sorted({r['date'] for r in store.q('SELECT date, text, digested FROM journal')
                   if r['digested'] != digest(r['text'] or '')})


def mark_digested(dates):
    """这几天记进账本了：按现在的正文记一笔指纹。"""
    if not dates:
        return
    rows = store.q('SELECT page_id, text FROM journal WHERE date IN (%s)' % ','.join('?' * len(dates)), *dates)
    with store.tx() as db:
        for r in rows:
            db.execute('UPDATE journal SET digested=? WHERE page_id=?', (digest(r['text'] or ''), r['page_id']))


# ------------------------------------------------------------------ 读

def text_for(date, limit=None):
    """那天手写的全文，纯文字（几页按标题排，空行隔开）。喂模型用。"""
    return '\n\n'.join(plain(j['text']) for j in for_date(date) if j['text'])[:limit]


def for_date(date):
    """那天的几页：text 是 Markdown（日记页上显示用），chars 是纯文字的字数。"""
    rows = store.q('SELECT page_id, title, text, chars, stripped FROM journal WHERE date=? ORDER BY title', date)
    for r in rows:
        r['url'] = 'https://www.notion.so/' + r['page_id'].replace('-', '')
    return rows


def dates():
    """{date: 字数}，只算有正文的。"""
    return {r['date']: r['c'] for r in
            store.q('SELECT date, SUM(chars) c FROM journal WHERE chars > 0 GROUP BY date')}


def search(kw, limit=50):
    safe = '"' + kw.replace('"', '""') + '"'
    return store.q("""SELECT date, page_id, snippet(journal_fts, 0, '<mark>', '</mark>', '…', 20) snip
                   FROM journal_fts WHERE journal_fts MATCH ? ORDER BY date DESC LIMIT ?""", safe, limit)


if __name__ == '__main__':
    import sys
    r = sync(full='--full' in sys.argv, say=lambda m: print(m, flush=True))
    print('DONE', {k: (v if k != 'failed' else len(v)) for k, v in r.items()})
    for d, why in r['failed']:
        print('  失败', d, why)
