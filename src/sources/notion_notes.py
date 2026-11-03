"""Notion 的笔记库（11-03 用户：「问」也要能搜 Notion 里别的笔记，「你去把 Notes database 拉过来就行」）。

    sync(full=False)   拉新的和改过的（每天早上跟手写日记一起）
    redact(text)       把密钥 / 密码换成「〔已隐藏〕」

**只给「问」查**：不进日记、不做卡、不进主题账本（跟手写日记不一样：手写日记 = 那天的日记）。
**密钥不进本地**（用户：「里面有 page 放 API、密码，什么都不能出现在 GitHub、公网上」）：存之前先 redact，
所以 chats.db、Drive 上的备份、送给模型的都只有打了码的那份；原文只在 Notion 里。
库的 data_source_id 在 .env 的 NOTION_NOTES_DS，不写进代码。图片不下载（只留「[图片]」）。
**只增不删**（规矩 6）：Notion 里没了的页只记 gone_at；一下少了一大片当成出故障，不标。
"""
import json
import re
import time
from datetime import datetime

import index as idx
import journal
import store
from sources import to_notion as tn

GONE_ALARM = 20
MASK = '〔已隐藏〕'

# 常见服务的密钥前缀（OpenAI / Anthropic / Notion / GitHub / Google / Slack / AWS / HuggingFace / Stripe / DeepSeek / OpenRouter …）
_KEYS = re.compile(r'(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_\-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|[pr]k_(?:live|test)_[A-Za-z0-9]{16,}'
                   r'|ntn_[A-Za-z0-9]{20,}|secret_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
                   r'|AIza[A-Za-z0-9_\-]{30,}|xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[A-Z0-9]{16}|hf_[A-Za-z0-9]{20,}'
                   r'|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})')
# 没前缀的长串：32 位以上、字母数字混着（十六进制的 key、各种 token）
_LONG = re.compile(r'(?<![A-Za-z0-9/._\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9/._\-])')
# 「密码：xxx」「API key = xxx」这种：冒号 / 等号后面那一截
_LABELED = re.compile(r'((?:密码|口令|密钥|秘钥|验证码|password|passwd|pwd|passcode|pin|api[ _-]?key|secret|token|private[ _-]?key|access[ _-]?key)'
                      r'\s*(?:[:：=]|是|为)\s*)(\S+)', re.I)
_PEM = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S)
_URL_CRED = re.compile(r'(\w+://[^\s/:@]+:)[^\s/@]+@')            # https://user:密码@host


def redact(text):
    """把看着像密钥 / 密码的换成 MASK。返回 (打了码的正文, 换了几处)。宁可多遮：遮错了去 Notion 看原文。"""
    n = 0

    def m(_):
        nonlocal n
        n += 1
        return MASK
    t = _PEM.sub(m, text or '')
    t = _URL_CRED.sub(lambda x: (m(x), x.group(1) + MASK + '@')[1], t)
    t = _KEYS.sub(m, t)
    t = _LABELED.sub(lambda x: x.group(0) if x.group(2) == MASK else (m(x), x.group(1) + MASK)[1], t)
    t = _LONG.sub(m, t)
    return t, n


def _prop(p, name):
    v = (p.get('properties') or {}).get(name) or {}
    t = v.get('type')
    if t == 'title':
        return ''.join(x.get('plain_text', '') for x in v.get('title') or [])
    if t == 'multi_select':
        return [x.get('name', '') for x in v.get('multi_select') or []]
    if t == 'select':
        return (v.get('select') or {}).get('name', '')
    if t == 'date':
        return ((v.get('date') or {}).get('start') or '')[:10]
    return None


def _title_prop(p):
    return next((k for k, v in (p.get('properties') or {}).items() if v.get('type') == 'title'), None)


def _meta(p):
    """查询结果里的一页 → 存的那些字段（标签这类全部属性都留，不认死列名：库的结构他随时会改）。"""
    tags, other = [], {}
    for name, v in (p.get('properties') or {}).items():
        t = v.get('type')
        if t == 'multi_select':
            tags += _prop(p, name)
            other[name] = _prop(p, name)
        elif t == 'select' and _prop(p, name):
            other[name] = _prop(p, name)
    dates = [_prop(p, k) for k, v in (p.get('properties') or {}).items() if v.get('type') == 'date']
    created = (p.get('created_time') or '')[:10]
    return {'page_id': p['id'], 'title': redact(_prop(p, _title_prop(p)) or '')[0] if _title_prop(p) else '',
            'date': next((d for d in dates if d), '') or created, 'created': created,
            'tags': json.dumps(other, ensure_ascii=False), 'last_edited': p.get('last_edited_time', '')}


def list_pages(ds=None):
    ds = ds or idx.env('NOTION_NOTES_DS')
    if not ds:
        raise RuntimeError('没设 NOTION_NOTES_DS（.env）')
    out, cur = [], None
    while True:
        body = {'page_size': 100, **({'start_cursor': cur} if cur else {})}
        res = tn._req('POST', f'https://api.notion.com/v1/data_sources/{ds}/query', body)
        out += [_meta(p) for p in res.get('results', []) if not p.get('in_trash')]
        if not res.get('has_more'):
            return out
        cur = res.get('next_cursor')


def page_text(page_id):
    blocks = journal._children(page_id)
    text = '\n'.join(journal._render(blocks))                      # 不给 image：图片只留「[图片]」，不下载
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _store(p, text):
    safe, n = redact(text)
    with store.tx() as db:
        db.execute("""INSERT INTO notes(page_id, title, date, created, tags, text, chars, masked, last_edited, fetched_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(page_id) DO UPDATE SET title=excluded.title, date=excluded.date, created=excluded.created,
                        tags=excluded.tags, text=excluded.text, chars=excluded.chars, masked=excluded.masked,
                        last_edited=excluded.last_edited, fetched_at=excluded.fetched_at, gone_at=NULL""",
                   (p['page_id'], p['title'], p['date'], p['created'], p['tags'], safe, len(journal.plain(safe)), n,
                    p['last_edited'], datetime.now().isoformat(timespec='seconds')))


def sync(full=False, say=print, limit=None):
    """拉新的和改过的（按 last_edited 比）。返回 {'pages', 'fetched', 'gone', 'failed', 'masked', 'warning'}。"""
    pages = list_pages()
    rows = store.q('SELECT page_id, last_edited, gone_at FROM notes')
    have = {r['page_id']: r['last_edited'] for r in rows}
    todo = [p for p in pages if full or have.get(p['page_id']) != p['last_edited']][:limit]
    gone = {r['page_id'] for r in rows if not r['gone_at']} - {p['page_id'] for p in pages}
    warning = None
    if len(gone) > GONE_ALARM and len(gone) > len(have) * 0.2:
        warning = f'Notion 笔记一下少了 {len(gone)} 页（本地 {len(have)} 页），像是权限或连接出了问题：没动本地'
        say(warning)
        gone = set()
    say(f'Notion 笔记：{len(pages)} 页，要拉 {len(todo)} 页，Notion 里没了 {len(gone)} 页（本地留着）')
    failed, masked = [], 0
    for i, p in enumerate(todo, 1):
        try:
            text = page_text(p['page_id'])
        except Exception as e:                                       # noqa: BLE001
            failed.append(f'{type(e).__name__}: {e}'[:200])
            continue
        _store(p, text)
        masked += redact(text)[1]
        if i % 50 == 0 or i == len(todo):
            say(f'  {i}/{len(todo)}')
        time.sleep(0.2)                                              # Notion 限流约 3 次/秒
    now = datetime.now().isoformat(timespec='seconds')
    listed = {p['page_id'] for p in pages}
    with store.tx() as db:
        db.executemany('UPDATE notes SET gone_at=? WHERE page_id=?', [(now, pid) for pid in gone])
        db.executemany('UPDATE notes SET gone_at=NULL WHERE page_id=?',
                       [(r['page_id'],) for r in rows if r['gone_at'] and r['page_id'] in listed])
    return {'pages': len(pages), 'fetched': len(todo) - len(failed), 'gone': len(gone), 'failed': failed,
            'masked': masked, 'warning': warning}


if __name__ == '__main__':
    import sys
    r = sync(full='--full' in sys.argv)
    print({k: (len(v) if isinstance(v, list) else v) for k, v in r.items()})
