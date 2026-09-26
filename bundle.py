"""把某一天拼成喂给模型的材料：当天我发的消息按窗口分组，每个窗口补一段「前情」。

从 app.py 挪出来的：mood / mood_lab 也要用，而 `import app` 会撞上 getAudio 里同名的 app.py
（diary 把 getAudio 插到了 sys.path 最前面）。这里只依赖 index，谁都能放心导。
"""
import index as idx


def q(sql, *args):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, args).fetchall()]


def day_bundle(date):
    """当天的消息，按窗口分组；每个窗口再补一段「前情」。

    只喂当天的消息，模型看到的是一条线被切掉的中段，会缺背景（实测 65% 的天有这个问题、
    30% 的「窗口·天」是接着前面聊的）。所以对每个在今天之前已经聊过的窗口，
    带上：这条线什么时候开的、之前多少天多少条、开头问的是什么、昨天之前最后聊到哪儿。
    全是确定性查询，不额外调模型。
    """
    rows = q("""SELECT m.*, c.title conv_title FROM messages m
                  JOIN conversations c ON c.id = m.conv_id
                 WHERE m.date = ? AND m.role='human' ORDER BY m.ts""", date)
    threads = {}
    for m in rows:
        t = threads.setdefault(m['conv_id'], {'conv_id': m['conv_id'],
                                              'conversation': m['conv_title'], 'messages': []})
        t['messages'].append({'time': m['hhmm'], 'role': 'human', 'text': m['text']})
    items = []
    for t in threads.values():
        t['first'] = t['messages'][0]['time']
        t['last'] = t['messages'][-1]['time']
        t['n_msgs'] = len(t['messages'])
        t['chars'] = sum(len(x['text']) for x in t['messages'])
        t['prior'] = prior_context(t['conv_id'], date)
        items.append(t)
    items.sort(key=lambda x: x['first'])
    return {'date': date, 'n_threads': len(items),
            'n_user_msgs': sum(t['n_msgs'] for t in items),
            'user_chars': sum(t['chars'] for t in items), 'threads': items}


def prior_context(conv_id, date, opening=320, recent=3, recent_chars=260):
    """这个窗口在 date 之前聊过什么。没聊过就返回 None。"""
    agg = q("""SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) since
                 FROM messages WHERE conv_id=? AND role='human' AND date < ?""", conv_id, date)[0]
    if not agg['n']:
        return None
    first = q("""SELECT date, text FROM messages WHERE conv_id=? AND role='human' AND date < ?
                  ORDER BY ts LIMIT 1""", conv_id, date)[0]
    last = q("""SELECT date, hhmm, text FROM messages WHERE conv_id=? AND role='human' AND date < ?
                 ORDER BY ts DESC LIMIT ?""", conv_id, date, recent)
    return {'n': agg['n'], 'days': agg['days'], 'since': agg['since'],
            'opening': first['text'][:opening],
            'recent': [{'date': r['date'], 'hhmm': r['hhmm'], 'text': r['text'][:recent_chars]}
                       for r in reversed(last)]}
