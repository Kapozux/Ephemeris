"""每天自动把 claude.ai 的新对话拉进来。不用开浏览器，不用点任何东西。

`export_claude.js` 干的是同一件事，区别只在谁来跑：那个要你打开 claude.ai、
按 F12、粘贴、再把下载的文件拖进应用。这里是 headless Chrome 替你做完。

**为什么非要浏览器。** claude.ai 在 Cloudflare 后面，Python 直接请求拿回的是
「Just a moment...」的 JS 挑战页（403）。伪装 TLS 指纹也不行，它要求真的执行 JS。
所以起一个真 Chrome（不是 chrome-headless-shell，那个 CF 一测一个准），
把 sessionKey 塞进去，然后在**页面里**调 fetch —— 请求带着完整的浏览器身份，CF 放行。

**增量。** 列表接口给每个对话的 updated_at，跟本地 `claude_sync` 表比一下，
没变的根本不拉。你一天大概碰 5-6 个窗口，所以每天实际下载的就那几个。
就算整个对话重拉也不会脏：`ingest` 按消息哈希去重，只有新消息会写进去。

反过来说，「每次都是整个对话」恰恰是它比 Gemini Takeout 强的地方 ——
「按窗口」的时间轴和日记里的「前情」，都得有完整的线才成立。
"""
import json
import os
import pathlib
import socket
import tempfile
import time
from datetime import datetime

import index as idx
from chrome import Chrome

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SINCE = '2026-01-01'        # 头一次拉多久以前的；之后靠 updated_at 增量
BATCH = 15                          # 一次 evaluate 拉几个对话，太多 CDP 回包会很大
FLUSH = 120                         # 攒到这么多就落一次盘（兼顾内存和崩溃止损）


# ------------------------------------------------------------------ 配置

env = idx.env


def wait_network(host='claude.ai', tries=10, gap=30):
    """刚开机 / 刚唤醒时网络（或代理）还没起来，页面里的 fetch 会直接 Failed to fetch。
    先确认连得上再起 Chrome，最多等 5 分钟。"""
    for i in range(tries):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            if i < tries - 1:
                time.sleep(gap)
    return False


def _transient(e):
    return 'Failed to fetch' in str(e) or 'NetworkError' in str(e)


class SyncError(RuntimeError):
    pass


# ------------------------------------------------------------------ 记账

def ensure_table():
    with idx.LOCK:
        c = idx.connect()
        c.execute("""CREATE TABLE IF NOT EXISTS claude_sync (
                        uuid       TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        synced_at  TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sync_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at TEXT, finished_at TEXT, ok INTEGER,
                        n_listed INTEGER, n_fetched INTEGER,
                        n_msgs_new INTEGER, days_touched INTEGER, note TEXT)""")
        c.commit()


def known():
    ensure_table()
    with idx.LOCK:
        return {r['uuid']: r['updated_at']
                for r in idx.connect().execute('SELECT uuid, updated_at FROM claude_sync')}


def _remember(rows):
    now = datetime.now().isoformat(timespec='seconds')
    with idx.LOCK:
        c = idx.connect()
        c.executemany('INSERT OR REPLACE INTO claude_sync VALUES (?,?,?)',
                      [(u, up, now) for u, up in rows])
        c.commit()


def last_run():
    ensure_table()
    with idx.LOCK:
        r = idx.connect().execute(
            'SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()
    return dict(r) if r else None


def last_ok_run():
    ensure_table()
    with idx.LOCK:
        r = idx.connect().execute(
            'SELECT * FROM sync_runs WHERE ok=1 ORDER BY id DESC LIMIT 1').fetchone()
    return dict(r) if r else None


def _log_run(**kw):
    with idx.LOCK:
        c = idx.connect()
        c.execute("""INSERT INTO sync_runs
                     (started_at, finished_at, ok, n_listed, n_fetched, n_msgs_new,
                      days_touched, note) VALUES (?,?,?,?,?,?,?,?)""",
                  (kw.get('started_at'), datetime.now().isoformat(timespec='seconds'),
                   1 if kw.get('ok') else 0, kw.get('n_listed', 0), kw.get('n_fetched', 0),
                   kw.get('n_msgs_new', 0), kw.get('days_touched', 0), kw.get('note')))
        c.commit()


# -------------------------------------------------------- 页面里跑的 JS

_LIST_JS = """(async () => {
  const org = %s;
  const out = []; let offset = 0;
  while (true) {
    const r = await fetch(`/api/organizations/${org}/chat_conversations?limit=50&offset=${offset}`,
                          {credentials:'include'});
    if (!r.ok) return {error: 'list HTTP ' + r.status};
    const j = await r.json();
    const items = Array.isArray(j) ? j : (j.data || j.results || j.chat_conversations || []);
    if (!items.length) break;
    for (const c of items) out.push({uuid: c.uuid || c.id, name: c.name || '',
                                     created_at: c.created_at, updated_at: c.updated_at || c.created_at});
    if (items.length < 50) break;
    offset += 50;
    await new Promise(r => setTimeout(r, 200));
  }
  return {conversations: out};
})()"""

# 跟 export_claude.js 里那段取正文的逻辑保持一致：只要 text 型 content，created_at 原样留 ISO
_FETCH_JS = """(async () => {
  const org = %s, ids = %s, out = [], fail = [];
  for (const it of ids) {
    try {
      const r = await fetch(`/api/organizations/${org}/chat_conversations/${it.uuid}` +
                            `?tree=true&rendering_mode=messages&render_all_tools=true`,
                            {credentials:'include'});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const messages = [];
      for (const m of (d.chat_messages || [])) {
        const role = m.sender === 'human' ? 'human' : 'assistant';
        const text = (m.content || []).filter(x => x.type === 'text' || !x.type)
                       .map(x => x.text ?? '').join('').trim();
        if (text) messages.push({role, created_at: m.created_at, text});
      }
      if (messages.length) out.push({uuid: it.uuid, name: it.name || 'Untitled',
                                     created_at: it.created_at, updated_at: it.updated_at, messages});
    } catch (e) { fail.push({uuid: it.uuid, error: String(e)}); }
    await new Promise(r => setTimeout(r, 350));
  }
  return {conversations: out, failed: fail};
})()"""


def _flush(convs, since, totals, say):
    """把一批对话落盘入库，并记下 uuid+updated_at（崩了重跑就能跳过）。"""
    if not convs:
        return
    say('入库', len(convs), len(convs))
    payload = {'exported_at': datetime.now().isoformat(), 'since': since,
               'n_conversations': len(convs), 'conversations': convs}
    fd, tmp = tempfile.mkstemp(suffix='.json', prefix='claude-sync-')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    try:
        r = idx.ingest(idx.connect(), tmp, kind='claude_json', source='claude',
                       label=f'claude-sync {datetime.now():%Y-%m-%d}')
    finally:
        os.unlink(tmp)
    _remember([(x['uuid'], x['updated_at']) for x in convs])
    totals['convs'] += len(convs)
    totals['msgs'] += r['n_msgs_new']
    totals['days'].update(r['days_touched'])
    totals['inval'] += r['diaries_invalidated']


# ------------------------------------------------------------------ 主流程

def sync(since=None, force=False, progress=None, headless=True):
    """拉一次。progress(阶段, 已完成, 总数) 会被逐步调用。"""
    ensure_table()
    started = datetime.now().isoformat(timespec='seconds')
    say = progress or (lambda *a: None)

    sk = env('CLAUDE_SESSION_KEY')
    if not sk:
        raise SyncError('ephemeris/.env 里没有 CLAUDE_SESSION_KEY')
    org = env('CLAUDE_ORG_ID')
    since = since or env('CLAUDE_SINCE') or DEFAULT_SINCE

    say('等网络', 0, 0)
    if not wait_network():
        raise SyncError('连不上 claude.ai —— 网络或代理没起来，下个小时再试')

    try:
        with Chrome(headless=headless) as c:
            say('起浏览器', 0, 0)
            c.goto('https://claude.ai/', wait=1)
            c.set_cookie('sessionKey', sk)
            if org:
                c.set_cookie('lastActiveOrg', org)
            c.goto('https://claude.ai/', wait=8)          # 给 Cloudflare 跑挑战的时间

            if not org:
                orgs = c.evaluate(
                    "(async()=>{const r=await fetch('/api/organizations',{credentials:'include'});"
                    "return r.ok ? await r.json() : null})()", timeout=60)
                if not orgs:
                    raise SyncError('拿不到 org —— sessionKey 多半过期了，重新取一个')
                org = orgs[0]['uuid']

            say('列对话', 0, 0)
            for attempt in range(3):
                try:
                    res = c.evaluate(_LIST_JS % json.dumps(org), timeout=180)
                    break
                except Exception as e:                       # noqa: BLE001
                    if attempt == 2 or not _transient(e):
                        raise
                    say('网络抖了，重试', attempt + 1, 3)
                    time.sleep(20)
                    c.goto('https://claude.ai/', wait=8)
            if not res or res.get('error'):
                raise SyncError(f"列对话失败：{(res or {}).get('error')} —— sessionKey 可能过期了")
            listed = [x for x in res['conversations'] if (x.get('updated_at') or '') >= since]

            seen = {} if force else known()
            todo = [x for x in listed if seen.get(x['uuid']) != x['updated_at']]
            say('对比', len(listed) - len(todo), len(listed))
            if not todo:
                _log_run(started_at=started, ok=True, n_listed=len(listed), note='没有变化')
                return {'listed': len(listed), 'fetched': 0, 'new_msgs': 0,
                        'days': [], 'note': '没有变化'}

            # 边拉边入库。1177 个对话全拉完再一次性写，崩一次就全白费
            # （2026-09-20 踩过：60/1177 时页面上下文作废，前面 60 个全丢）。
            # 每 FLUSH 个落一次盘 + 记一次 claude_sync，崩了重跑能接着走。
            done, failed, buf = 0, [], []
            totals = {'msgs': 0, 'convs': 0, 'days': set(), 'inval': 0}
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                r = None
                for attempt in range(3):
                    try:
                        r = c.evaluate(_FETCH_JS % (json.dumps(org), json.dumps(chunk)),
                                       timeout=300)
                        break
                    except RuntimeError as e:
                        if 'navigated or closed' not in str(e) and 'context' not in str(e).lower():
                            raise
                        say('页面掉了，重连', attempt + 1, 3)
                        c.recover()
                        c.goto('https://claude.ai/', wait=6)
                if r is None:
                    raise SyncError('页面反复失效，重连 3 次都不行')
                buf += r.get('conversations', [])
                failed += r.get('failed', [])
                done = min(i + BATCH, len(todo))
                say('拉取', done, len(todo))
                if len(buf) >= FLUSH or done >= len(todo):
                    _flush(buf, since, totals, say)
                    buf = []

        if not totals['convs']:
            raise SyncError(f'一个都没拉到（失败 {len(failed)}）')

        note = f'失败 {len(failed)} 个' if failed else None
        _log_run(started_at=started, ok=True, n_listed=len(listed), n_fetched=totals['convs'],
                 n_msgs_new=totals['msgs'], days_touched=len(totals['days']), note=note)
        return {'listed': len(listed), 'fetched': totals['convs'], 'failed': failed,
                'new_msgs': totals['msgs'], 'days': sorted(totals['days']),
                'diaries_invalidated': totals['inval']}

    except Exception as e:                                   # noqa: BLE001
        _log_run(started_at=started, ok=False, note=f'{type(e).__name__}: {e}'[:300])
        raise


# -------------------------------------------------------------------- CLI

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help=f'只看 updated_at 晚于这天的对话（默认 {DEFAULT_SINCE}）')
    ap.add_argument('--force', action='store_true', help='忽略增量记录，全部重拉')
    ap.add_argument('--show', action='store_true', help='开着窗口跑（调试用）')
    ap.add_argument('--status', action='store_true')
    args = ap.parse_args()

    if args.status:
        print(json.dumps(last_run(), ensure_ascii=False, indent=1))
        raise SystemExit

    t0 = time.time()
    r = sync(since=args.since, force=args.force, headless=not args.show,
             progress=lambda st, a, b: print(f'  {st} {a}/{b}' if b else f'  {st}…', flush=True))
    print()
    print(f"列出 {r['listed']} 个，拉了 {r['fetched']} 个，"
          f"新消息 {r.get('new_msgs', 0)}，涉及 {len(r['days'])} 天  （{time.time()-t0:.0f}s）")
    if r['days']:
        print('  ', ' '.join(r['days'][-12:]))
    if r.get('diaries_invalidated'):
        print(f"  作废日记 {r['diaries_invalidated']} 篇，侧栏可以补全")
