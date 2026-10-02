"""每天 08:30 同步完之后的整理，一步接一步。每步独立：前一步失败不拦后一步，结果记在 daily_runs。

  1. 手写日记    从 Notion 拉新写/改过的页（只读）
  2. 手写进账本  那几天的手写记进主题账本（src='hand'）
  3. 两个指标    新聊天天打「卡得多狠」，新手写天打「过得怎么样」
  4. 主题归类    新冒出来的主题归到河流图的大类里
  5. 窗口回顾    做过回顾的窗口，只补新聊的天

写日记本身（diary + 账本 ai 那一侧）在这之前由 app 的批量流程做完。
"""
import json
import time
from datetime import datetime

import store


def _step(name, fn, out):
    t0 = time.time()
    try:
        res = fn()
        out.append({'step': name, 'ok': True, 'result': res, 'secs': round(time.time() - t0, 1)})
    except Exception as e:                                   # noqa: BLE001
        out.append({'step': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'[:300],
                    'secs': round(time.time() - t0, 1)})
        print(f'[daily] {name} 失败：{e}', flush=True)


def run(call_model):
    import conv_review
    import journal
    import ledger
    import mood
    import river
    started = datetime.now().isoformat(timespec='seconds')
    out, ctx = [], {}

    def s_journal():
        r = journal.sync(say=lambda m: None)
        ctx['dates'] = r['dates']
        return {'fetched': r['fetched'], 'removed': r['removed'], 'dates': r['dates'], 'failed': len(r['failed'])}

    def s_hand():
        return {'days': ledger.record_hand_for(ctx.get('dates') or [], call_model)}

    def s_mood():
        a, b = mood.fill(call_model, say=lambda m: None)
        return {'friction': a, 'hand': b}

    def s_cats():
        before = store.one('SELECT COUNT(*) n FROM theme_cats')['n']
        river.classify(call_model, say=lambda m: None)
        return {'new': store.one('SELECT COUNT(*) n FROM theme_cats')['n'] - before}

    def s_windows():
        with store.LOCK:
            cids = [r[0] for r in store.connect().execute("SELECT conv_id FROM conv_notes WHERE date=''")]
        done = []
        for cid in cids:
            todo = conv_review.pending(cid)
            if todo:
                conv_review.generate(cid)
                done.append({'conv': cid[:10], 'days': len(todo)})
        return {'windows': done}

    for name, fn in (('手写日记', s_journal), ('手写进账本', s_hand), ('两个指标', s_mood),
                     ('主题归类', s_cats), ('窗口回顾', s_windows)):
        _step(name, fn, out)

    with store.LOCK:
        db = store.connect()
        db.execute('INSERT INTO daily_runs(started_at, finished_at, steps) VALUES (?,?,?)',
                   (started, datetime.now().isoformat(timespec='seconds'), json.dumps(out, ensure_ascii=False)))
        db.commit()
    brief = ' · '.join(f"{o['step']}{'' if o['ok'] else ' ✗'}" for o in out)
    print(f'[daily] {brief}', flush=True)
    return out


def last():
    with store.LOCK:
        r = store.connect().execute('SELECT * FROM daily_runs ORDER BY id DESC LIMIT 1').fetchone()
    if not r:
        return None
    r = dict(r)
    r['steps'] = json.loads(r['steps'] or '[]')
    return r
