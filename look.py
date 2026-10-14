"""「问 Claude」的查资料工具（2026-10-13）：只读，Claude Code 只被允许跑这一个命令（qa.answer_deep）。人也能在终端直接用。

python look.py msgs  <关键词> [--since D] [--until D] [--who me|ai] [--old] [--n 30]   搜原始聊天（默认最新的在前，--old 最早的在前）
python look.py cards <问题>   [--kw 词,词] [--since D] [--until D] [--n 40]           搜证据卡（字面 + 按意思，qa.rank）
python look.py read  <消息id> [--before 4] [--after 4] [--full]                        读一条消息和它前后几条
python look.py conv  <窗口id>                                                          窗口小结 + 每条消息一行的目录
python look.py convs <关键词>                                                          哪些窗口聊过（标题、小结、正文）
python look.py day   <YYYY-MM-DD>                                                      那天有哪些窗口、那天的日记、手写日记
python look.py stats [--since D] [--until D]                                           一共有多少：消息、字数、窗口、按来源、证据卡、日记、按月

出处记号：消息是 m:<id>，卡是 c:<id>。回答时照抄就能被 Ephemeris 认回原话。
搜原文 / 找窗口不看「问」自己的对话（来源 ask）：不然会引到自己以前的回答。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa      # noqa: E402
import store   # noqa: E402

WHO = {'human': '我', 'assistant': 'AI'}


def _one_line(text, n):
    t = ' '.join((text or '').split())
    return t if len(t) <= n else t[:n] + '…'


def _around(text, kw, width=90):
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
    kw = ' '.join(a.words)
    rng, args = _rng(a)
    who = {'me': " AND m.role='human'", 'ai': " AND m.role='assistant'"}.get(a.who or '', '')
    rows = store.q(f"""SELECT m.id, m.date, m.hhmm, m.role, m.text, m.chars, c.title FROM messages m
                        JOIN conversations c ON c.id = m.conv_id
                       WHERE m.text LIKE ?{rng}{who} AND m.source <> 'ask' ORDER BY m.ts {'ASC' if a.old else 'DESC'} LIMIT ?""",
                   f'%{kw}%', *args, a.n)
    total = store.one(f"SELECT COUNT(*) n FROM messages m WHERE m.text LIKE ?{rng}{who} AND m.source <> 'ask'", f'%{kw}%', *args)['n']
    print(f'「{kw}」原文命中 {total} 条，列出 {len(rows)} 条（{"最早" if a.old else "最新"}的在前）')
    for r in rows:
        print(f"[m:{r['id']}] {r['date']} {r['hhmm']} {WHO.get(r['role'], r['role'])} 《{r['title'] or '无标题'}》 {_around(r['text'], kw)}")


def cards_(a):
    q = ' '.join(a.words)
    plan = {'q': q, 'keywords': [k for k in (a.kw or '').split(',') if k] or q.split(), 'since': a.since, 'until': a.until}
    ids = qa.rank(plan, budget=a.n * 200)[:a.n]
    rows = sorted(filter(None, map(qa.card, ids)), key=lambda c: (c['date'], c['ts'] or ''))
    print(f'证据卡 {len(rows)} 张（按时间）')
    for c in rows:
        print(f"[c:{c['id']}] {c['date']} {c['hhmm'] or ''} {'我' if c['who'] == 'me' else 'AI'} {c['kind']} · "
              f"{c['topic'] or ''} 《{c['title'] or '无标题'}》 {c['text']}「{c['quote']}」 (原话 m:{c['msg_id']})")


def read(a):
    mid = a.words[0].removeprefix('m:')
    m = store.one('SELECT conv_id, ts, id FROM messages WHERE id=?', mid)
    if not m:
        print('没有这条消息'); return
    t = store.one('SELECT title FROM conversations WHERE id=?', m['conv_id'])['title']
    before = store.q('SELECT * FROM messages WHERE conv_id=? AND (ts < ? OR (ts = ? AND id < ?)) ORDER BY ts DESC, id DESC LIMIT ?',
                     m['conv_id'], m['ts'], m['ts'], m['id'], a.before)[::-1]
    after = store.q('SELECT * FROM messages WHERE conv_id=? AND (ts > ? OR (ts = ? AND id > ?)) ORDER BY ts, id LIMIT ?',
                    m['conv_id'], m['ts'], m['ts'], m['id'], a.after)
    this = store.one('SELECT * FROM messages WHERE id=?', mid)
    print(f"《{t or '无标题'}》（窗口 {m['conv_id']}）")
    for r in before + [this] + after:
        cap = 100000 if a.full else (4000 if r['id'] == mid else 1200)
        mark = ' ◀ 这条' if r['id'] == mid else ''
        print(f"\n[m:{r['id']}] {r['date']} {r['hhmm']} {WHO.get(r['role'], r['role'])}{mark}（{r['chars']} 字）\n"
              f"{(r['text'] or '')[:cap]}{'…（后面略，--full 看全文）' if len(r['text'] or '') > cap else ''}")


def conv(a):
    cid = a.words[0]
    c = store.one('SELECT title FROM conversations WHERE id=?', cid)
    if not c:
        print('没有这个窗口'); return
    s = store.one('SELECT summary FROM card_convs WHERE conv_id=?', cid)
    rows = store.q('SELECT id, date, hhmm, role, text, chars FROM messages WHERE conv_id=? AND chars > 0 ORDER BY ts, id', cid)
    print(f"《{c['title'] or '无标题'}》 {len(rows)} 条，{rows[0]['date'] if rows else ''} → {rows[-1]['date'] if rows else ''}")
    if s and s['summary']:
        print('小结：\n' + s['summary'])
    print('\n目录：')
    for r in rows:
        print(f"[m:{r['id']}] {r['date']} {r['hhmm']} {WHO.get(r['role'], r['role'])}（{r['chars']} 字）{_one_line(r['text'], 70)}")


def convs(a):
    kw = ' '.join(a.words)
    rows = store.q("""SELECT c.id, c.title, s.summary,
                             (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id AND m.text LIKE ?) hits,
                             (SELECT MIN(date) FROM messages m WHERE m.conv_id = c.id) first,
                             (SELECT MAX(date) FROM messages m WHERE m.conv_id = c.id) last
                        FROM conversations c LEFT JOIN card_convs s ON s.conv_id = c.id
                       WHERE c.source <> 'ask' AND (c.title LIKE ? OR s.summary LIKE ? OR EXISTS
                             (SELECT 1 FROM messages m WHERE m.conv_id = c.id AND m.text LIKE ?))
                       ORDER BY hits DESC LIMIT 25""", *([f'%{kw}%'] * 4))
    print(f'「{kw}」出现在 {len(rows)} 个窗口（命中多的在前，最多 25 个）')
    for r in rows:
        print(f"{r['id']} 《{r['title'] or '无标题'}》 {r['first']}→{r['last']} 原文命中 {r['hits']} 处｜{_one_line(r['summary'], 160)}")


def day(a):
    d = a.words[0]
    print(f'== {d}')
    for r in store.q("""SELECT c.id, c.title, COUNT(*) n, SUM(m.role='human') mine, MIN(m.hhmm) a, MAX(m.hhmm) b
                          FROM messages m JOIN conversations c ON c.id = m.conv_id WHERE m.date = ?
                         GROUP BY c.id ORDER BY a""", d):
        print(f"{r['id']} 《{r['title'] or '无标题'}》 {r['a']}–{r['b']} {r['n']} 条（我 {r['mine']}）")
    dia = store.one('SELECT headline, narrative, mood FROM diaries WHERE date=?', d)
    if dia:
        print(f"\n日记：{dia['headline']}\n{dia['narrative']}" + (f"\n心情：{dia['mood']}" if dia['mood'] else ''))
    for j in store.q('SELECT text FROM journal WHERE date=? AND (gone_at IS NULL OR gone_at = "")', d):
        print('\n手写日记：\n' + (j['text'] or '')[:3000])


def stats(a):
    """总量（2026-10-14 用户：问「你能搜集的数据规模」时，它只能拿「的」字搜一遍猜下限）。不算「问」自己的对话。"""
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


def main(argv=None):
    p = argparse.ArgumentParser(prog='look.py', description='只读查 Ephemeris 的聊天记录和证据卡')
    p.add_argument('cmd', choices=['msgs', 'cards', 'read', 'conv', 'convs', 'day', 'stats'])
    p.add_argument('words', nargs='*')
    p.add_argument('--since'); p.add_argument('--until')
    p.add_argument('--who', choices=['me', 'ai']); p.add_argument('--old', action='store_true')
    p.add_argument('--kw'); p.add_argument('--n', type=int, default=30)
    p.add_argument('--before', type=int, default=4); p.add_argument('--after', type=int, default=4)
    p.add_argument('--full', action='store_true')
    a = p.parse_args(argv)
    {'msgs': msgs, 'cards': cards_, 'read': read, 'conv': conv, 'convs': convs, 'day': day, 'stats': stats}[a.cmd](a)


if __name__ == '__main__':
    main()
