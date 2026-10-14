"""每天自动把 ChatGPT 的新对话拉进来（2026-10-14，ChatGPT 也要）。

跟 claude_sync 一个做法：起 Ephemeris 自己 profile 的真 Chrome（chrome.py，不碰用户的 Chrome），把登录 cookie 塞进去，
在 chatgpt.com 页面里调网页自己的接口（Cloudflare 只认真浏览器）。接口和取正文的规矩跟 export_chatgpt.js 一样：
  /api/auth/session 拿令牌 → /backend-api/conversations 列（普通 + 已归档，按更新时间）→ /backend-api/conversation/<id> 读，
  只取最后停在的那一支、只要他和 ChatGPT 的文字。

登录：ephemeris/.env 的 CHATGPT_SESSION_TOKEN = 浏览器里 chatgpt.com 的 cookie `__Secure-next-auth.session-token`。
cookie 太长时 ChatGPT 会拆成 `.0` / `.1` 两段：那就把 .0 填进 CHATGPT_SESSION_TOKEN、.1 填进 CHATGPT_SESSION_TOKEN_1。
没填就不跑（早上那一串跳过它）。

增量：chatgpt_sync 表记着每个对话上次拉到的 update_time，没变的不拉。手动导进来的 chatgpt_export_*.json 也会记进去（seed），
所以第一次自动同步不会把一千多个对话全重拉一遍。边拉边入库，崩了重跑接着走。
"""
import json
import os
import tempfile
import time
from datetime import datetime

import claude_sync
import index as idx
import store
from chrome import Chrome

BATCH = 10
FLUSH = 100
HOST = 'chatgpt.com'
env = idx.env

_AUTH = """const s = await fetch('/api/auth/session', {credentials:'include'}).then(r => r.json()).catch(() => null);
  if (!s || !s.accessToken) return {error: 'no session', status: 401};
  const norm = t => String(Math.floor(typeof t === 'number' ? t : (Date.parse(t) || 0) / 1000));   // 接口有时给秒数、有时给 ISO：一律整秒
  const did = (document.cookie.match(/oai-did=([^;]+)/) || [])[1];
  const H = {Authorization: 'Bearer ' + s.accessToken, ...(did ? {'oai-device-id': did} : {})};
  const get = async (p) => { for (let i = 0; ; i++) {
    const r = await fetch(p, {credentials:'include', headers: H});
    if (r.ok) return r.json();
    if (r.status >= 500 && i < 3) { await new Promise(x => setTimeout(x, 3000 * 2 ** i)); continue; }
    if (r.status === 429 && i < 2) { await new Promise(x => setTimeout(x, 20000 * (i + 1))); continue; }
    throw Object.assign(new Error('HTTP ' + r.status), {status: r.status, body: (await r.text()).slice(0, 300)}); } };"""

_LIST_JS = """(async () => {
  %s
  const out = new Map();
  try {
    for (const archived of [false, true]) {
      for (let offset = 0; ; offset += 100) {
        const j = await get(`/backend-api/conversations?offset=${offset}&limit=100&order=updated&is_archived=${archived}`);
        for (const c of j.items || []) out.set(c.id, {id: c.id, title: c.title || '', update_time: norm(c.update_time || c.create_time),
                                                      create_time: c.create_time});
        if ((j.items || []).length < 100) break;
        await new Promise(x => setTimeout(x, 300));
      }
    }
  } catch (e) { return {error: String(e), status: e.status, body: e.body}; }
  try {                                   // 项目里的对话：拿不到就算了
    const side = await get('/backend-api/gizmos/snorlax/sidebar?conversations_per_gizmo=0');
    for (const g of side.items || []) {
      const gid = (g.gizmo && g.gizmo.gizmo && g.gizmo.gizmo.id) || (g.gizmo && g.gizmo.id);
      if (!gid) continue;
      let cursor = '0';
      while (cursor != null) {
        const j = await get(`/backend-api/gizmos/${gid}/conversations?cursor=${cursor}`);
        for (const c of j.items || []) out.set(c.id, {id: c.id, title: c.title || '', update_time: norm(c.update_time || c.create_time),
                                                      create_time: c.create_time});
        cursor = j.cursor ?? null;
      }
    }
  } catch (e) {}
  return {conversations: [...out.values()]};
})()"""

_FETCH_JS = """(async () => {
  %s
  const ids = %s, out = [], fail = [];
  const iso = t => new Date((typeof t === 'number' ? t : Date.parse(t) / 1000) * 1000).toISOString();
  const textOf = m => { const c = m.content || {};
    if (!['text', 'multimodal_text'].includes(c.content_type)) return '';
    return (c.parts || []).filter(p => typeof p === 'string').join('\\n').trim(); };
  for (const it of ids) {
    try {
      const d = await get(`/backend-api/conversation/${it.id}`);
      const chain = [];
      for (let id = d.current_node; id && d.mapping[id]; id = d.mapping[id].parent) chain.push(d.mapping[id]);
      const messages = [];
      for (const node of chain.reverse()) {
        const m = node.message;
        if (!m || !m.create_time) continue;
        const role = m.author && m.author.role;
        if (role !== 'user' && role !== 'assistant') continue;
        if (m.metadata && m.metadata.is_visually_hidden_from_conversation) continue;
        if (role === 'assistant' && m.recipient && m.recipient !== 'all') continue;
        const text = textOf(m);
        if (text) messages.push({role: role === 'user' ? 'human' : 'assistant', created_at: iso(m.create_time), text});
      }
      if (messages.length) out.push({uuid: 'chatgpt-' + it.id, id: it.id, name: d.title || it.title || 'Untitled',
                                     created_at: iso(d.create_time || it.create_time), updated_at: it.update_time, messages});
      else out.push({id: it.id, updated_at: it.update_time, empty: true});
    } catch (e) {
      if (e.status === 429) return {conversations: out, failed: fail, limited: true};   // 被限速：今天先到这，下次接着
      fail.push({id: it.id, error: String(e)});
    }
    await new Promise(x => setTimeout(x, 1500));       // 10-14 实测一秒一个，三百来个之后就被限速
  }
  return {conversations: out, failed: fail};
})()"""


# ------------------------------------------------------------------ 记账

def known():
    return {r['id']: r['updated_at'] for r in store.q('SELECT id, updated_at FROM chatgpt_sync')}


def norm(t):
    """更新时间一律记成整秒的字符串（接口有时给秒数、有时给 ISO），对比才不会因为格式不同全部重拉。"""
    if t in (None, ''):
        return '0'
    try:
        return str(int(float(t)))
    except (TypeError, ValueError):
        try:
            return str(int(datetime.fromisoformat(str(t).replace('Z', '+00:00')).timestamp()))
        except ValueError:
            return str(t)


def remember(rows):
    now = datetime.now().isoformat(timespec='seconds')
    with store.tx() as db:
        db.executemany('INSERT OR REPLACE INTO chatgpt_sync (id, updated_at, synced_at) VALUES (?,?,?)',
                       [(i, norm(u), now) for i, u in rows])


def seed_from_export(path):
    """手动导进来的文件（export_chatgpt.js 的 json，或官方「导出数据」的 zip / conversations.json）：
    把里面每个对话的更新时间记下，自动同步就不再重拉它们。"""
    data = idx._load_claude_json(path)
    if isinstance(data, list):                                              # 官方导出
        rows = [(c.get('conversation_id') or c.get('id'), c.get('update_time')) for c in data if c.get('conversation_id') or c.get('id')]
    else:
        rows = [(c['uuid'].removeprefix('chatgpt-'), c.get('updated_at')) for c in data.get('conversations') or [] if c.get('uuid')]
    remember(rows)
    return len(rows)


def configured():
    return bool(env('CHATGPT_SESSION_TOKEN'))


def _explain(res):
    res = res or {}
    st, body = res.get('status'), res.get('body') or ''
    if st == 401 or res.get('error') == 'no session':
        return ('ChatGPT 的登录失效了：浏览器里登录 chatgpt.com，把 cookie __Secure-next-auth.session-token 的值'
                '填进 ephemeris/.env 的 CHATGPT_SESSION_TOKEN')
    if 'Just a moment' in body or '<html' in body.lower():
        return f'被 Cloudflare 拦下了（HTTP {st}）。隔一会儿再试'
    if st == 429:
        return '请求太频繁被限流了（HTTP 429），过一阵再同步'
    return f"列对话失败：{res.get('error')} {body[:120]}".strip()


def _flush(convs, totals, say):
    if not convs:
        return
    say('入库', len(convs), len(convs))
    real = [c for c in convs if not c.get('empty')]
    if real:
        fd, tmp = tempfile.mkstemp(suffix='.json', prefix='chatgpt-sync-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'exporter': 'ephemeris-chatgpt', 'exported_at': datetime.now().isoformat(), 'conversations': real},
                      f, ensure_ascii=False)
        try:
            with store.LOCK:
                r = idx.ingest(store.connect(), tmp, kind='claude_json', source='chatgpt',
                               label=f'chatgpt-sync {datetime.now():%Y-%m-%d}')
            claude_sync._archive(tmp)                  # 原件跟 claude 同步的放一起（imports/claude-sync/），只增不删
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        totals['msgs'] += r['n_msgs_new']
        totals['days'].update(r['days_touched'])
    remember([(c['id'], c['updated_at']) for c in convs])
    totals['convs'] += len(real)


def sync(progress=None, headless=True):
    """拉一次。返回 {'listed', 'fetched', 'new_msgs', 'days', 'failed'}。没配登录就抛 SyncError。"""
    say = progress or (lambda *a: None)
    tok = env('CHATGPT_SESSION_TOKEN')
    if not tok:
        raise claude_sync.SyncError('ephemeris/.env 里没有 CHATGPT_SESSION_TOKEN')
    if not claude_sync.wait_network(HOST):
        raise claude_sync.SyncError('连不上 chatgpt.com —— 网络或代理没起来')
    with Chrome(headless=headless) as c:
        say('起浏览器', 0, 0)
        c.goto(f'https://{HOST}/', wait=1)
        tok1 = env('CHATGPT_SESSION_TOKEN_1')
        if tok1:                                          # 拆成两段的 cookie
            c.set_cookie('__Secure-next-auth.session-token.0', tok, domain=f'.{HOST}')
            c.set_cookie('__Secure-next-auth.session-token.1', tok1, domain=f'.{HOST}')
        else:
            c.set_cookie('__Secure-next-auth.session-token', tok, domain=f'.{HOST}')
        c.goto(f'https://{HOST}/', wait=8)                # 给 Cloudflare 跑挑战的时间
        say('列对话', 0, 0)
        res = c.evaluate(_LIST_JS % _AUTH, timeout=300)
        if not res or res.get('error'):
            raise claude_sync.SyncError(_explain(res))
        listed = res['conversations']
        seen = known()
        todo = [x for x in listed if seen.get(x['id']) != norm(x['update_time'])]
        say('对比', len(listed) - len(todo), len(listed))
        totals, failed, buf, limited = {'msgs': 0, 'convs': 0, 'days': set()}, [], [], False
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            r = None
            for attempt in range(3):
                try:
                    r = c.evaluate(_FETCH_JS % (_AUTH, json.dumps(chunk)), timeout=300)
                    break
                except RuntimeError as e:
                    if 'navigated or closed' not in str(e) and 'context' not in str(e).lower():
                        raise
                    say('页面掉了，重连', attempt + 1, 3)
                    c.recover()
                    c.goto(f'https://{HOST}/', wait=6)
            if r is None:
                raise claude_sync.SyncError('页面反复失效，重连 3 次都不行')
            if r.get('error'):
                raise claude_sync.SyncError(_explain(r))
            buf += r.get('conversations', [])
            failed += r.get('failed', [])
            say('拉取', min(i + BATCH, len(todo)), len(todo))
            if r.get('limited'):                          # 被 ChatGPT 限速：拉到的先入库，剩下的下次（记着拉到哪了）
                limited = True
                break
            if len(buf) >= FLUSH or i + BATCH >= len(todo):
                _flush(buf, totals, say)
                buf = []
        _flush(buf, totals, say)
    return {'listed': len(listed), 'fetched': totals['convs'], 'failed': failed, 'limited': limited,
            'left': len(todo) - totals['convs'] - len(failed) if limited else 0,
            'new_msgs': totals['msgs'], 'days': sorted(totals['days'])}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', action='store_true', help='开着窗口跑（调试用）')
    ap.add_argument('--seed', help='把一个 chatgpt_export_*.json 里的对话记成「已经有了」，之后自动同步不重拉')
    a = ap.parse_args()
    if a.seed:
        print(f'记下 {seed_from_export(a.seed)} 个对话')
        raise SystemExit
    t0 = time.time()
    r = sync(headless=not a.show, progress=lambda st, x, y: print(f'  {st} {x}/{y}' if y else f'  {st}…', flush=True))
    print(f"列出 {r['listed']} 个，拉了 {r['fetched']} 个，新消息 {r['new_msgs']}，涉及 {len(r['days'])} 天，"
          f"失败 {len(r['failed'])}" + (f"，被限速停下、还剩约 {r['left']} 个下次接着" if r['limited'] else '') + f"（{time.time() - t0:.0f}s）")
