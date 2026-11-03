"""搜索（2026-10-07，照 Verbatim 资料库的做法）。

    messages(q, mine=True)  → 侧栏：哪天哪个窗口哪句话说过 q（mine=只搜我说的）
    conversations(q)        → 「窗口」栏：标题或正文里有 q 的窗口，各带命中几处和第一处的片段
    journal(q)              → 手写日记里的
    snippet(text, q)        → 「…前 20 字<mark>q</mark>后 40 字…」，正文先转义

为什么改：以前 msg_fts 用 unicode61 分词，一整句中文算一个词，「伤逝」「表白」一条都搜不到；
claude.ai 每天同步进来的消息也从没进过索引。现在 msg_fts 是 trigram（store.SCHEMA），触发器随写随更新。
3 个字以上走索引，几毫秒；1–2 个字索引用不上，整表扫约 150 ms。
"""
import html

import store


def _pattern(q):
    """LIKE 模式 + 要不要 ESCAPE。带 ESCAPE 的 LIKE 用不上 trigram 索引，所以只在真有 % _ 时才加。"""
    esc = any(ch in q for ch in '%_\\')
    if esc:
        q = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{q}%', (" ESCAPE '\\'" if esc else '')


def snippet(text, q, before=20, after=40):
    text = text or ''
    i = text.lower().find(q.lower())
    if i < 0:
        return html.escape(text[:before + after].replace('\n', ' '))
    a, b = max(0, i - before), i + len(q)
    s = (('…' if a else '') + html.escape(text[a:i]) + '<mark>' + html.escape(text[i:b]) + '</mark>'
         + html.escape(text[b:b + after]) + ('…' if b + after < len(text) else ''))
    return s.replace('\n', ' ')


def messages(q, mine=True, limit=100):
    pat, esc = _pattern(q)
    rows = store.q(f"""SELECT m.id msg_id, m.conv_id, m.date, m.hhmm, m.text, c.title conv_title
                         FROM msg_fts f CROSS JOIN messages m ON m.rowid = f.rowid
                         JOIN conversations c ON c.id = m.conv_id
                        WHERE f.text LIKE ?{esc} {"AND m.role = 'human'" if mine else ''}
                        ORDER BY m.ts DESC LIMIT ?""", pat, limit)
    for r in rows:
        r['snip'] = snippet(r.pop('text'), q)
    return rows


def conversations(q):
    """{conv_id: {'hits': n, 'snip': 最近一处的片段, 'date', 'msg_id': 那一处}}；只是标题里有的，hits=0、没有片段。"""
    pat, esc = _pattern(q)
    out = {}
    for r in store.q(f"""SELECT m.conv_id, m.id, m.date, m.text FROM msg_fts f CROSS JOIN messages m ON m.rowid = f.rowid
                          WHERE f.text LIKE ?{esc} ORDER BY m.ts DESC""", pat):
        h = out.get(r['conv_id'])
        if h:
            h['hits'] += 1
        else:
            out[r['conv_id']] = {'hits': 1, 'snip': snippet(r['text'], q), 'date': r['date'], 'msg_id': r['id']}
    for r in store.q(f'SELECT id FROM conversations WHERE title LIKE ?{esc}', pat):
        out.setdefault(r['id'], {'hits': 0, 'snip': ''})
    return out


def journal(q, limit=50):
    pat, esc = _pattern(q)
    rows = store.q(f'SELECT date, page_id, text FROM journal_fts WHERE text LIKE ?{esc} ORDER BY date DESC LIMIT ?', pat, limit)
    for r in rows:
        r['snip'] = snippet(r.pop('text'), q)
    return rows
