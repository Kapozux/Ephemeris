/**
 * ChatGPT 对话导出（给 Ephemeris 用，2026-10-14 用户：「有没有类似 Claude 的抓的」）
 *
 * ChatGPT 官方的「导出数据」要等邮件；这个脚本在 chatgpt.com 页面的控制台里跑，用网页自己的接口一个个拉：
 *   /api/auth/session                    拿登录令牌
 *   /backend-api/conversations           列对话（普通的 + 已归档的，按更新时间）
 *   /backend-api/gizmos/snorlax/sidebar  列「项目」，项目里的对话另外列（拿不到就跳过）
 *   /backend-api/conversation/<id>       读一个对话：消息是一棵树（重新生成、改问题都会分叉），只取你最后停在的那一支
 * 每条消息带时间，拖进 Ephemeris 能按天写日记；只要你和 ChatGPT 的文字，不要工具调用、代码执行结果、系统塞的说明。
 *
 * 用法：打开 chatgpt.com 并登录 → ⌥⌘J（Windows：Ctrl+Shift+J）打开控制台 → 粘贴 → 回车。
 * Chrome 第一次粘贴会让你先打一行 allow pasting。跑完会下载一个 chatgpt_export_日期.json，
 * 拖进 Ephemeris 的「数据来源 → 导入」就行。登着哪个账号就导哪个账号的。
 *
 * 增量：localStorage 只记着每个对话上次拉到的更新时间，第二次起只拉、只下载变过的（Ephemeris 导入只加新的，没变的不用再给）；
 * 每 50 个存一次，中途关掉下次能接着。
 * 同时拉 3 个，被限速（429）会自己等一会儿再试。想全部重拉，把 INCREMENTAL 改成 false。
 * 这是 OpenAI 没公开的接口，哪天网页改版这个脚本可能就不灵了 —— 那时还可以用官方「导出数据」（Ephemeris 也认那个 zip）。
 */
async function exportChatGPT(opts = {}) {
  const START_DATE = opts.since || '2000-01-01';   // 只拉这天之后更新过的对话
  const INCREMENTAL = opts.incremental ?? true;
  const DOWNLOAD = opts.download ?? true;
  const MAX = opts.max || Infinity;                // 测试用：只拉前几个
  const DELAY_MS = 300;
  const PARALLEL = opts.parallel || 3;               // 同时拉几个（被限速会自己退避）
  const STORE_KEY = 'ephemeris_chatgpt_export_v2';    // 只记每个对话的更新时间（全文存不下：浏览器给一个网站约 5MB）
  const cutoff = new Date(START_DATE).getTime() / 1000;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const panel = document.createElement('div');
  panel.style.cssText = `position:fixed;top:10px;right:10px;z-index:99999;background:#1a1a2e;color:#e0e0e0;
    padding:16px 20px;border-radius:8px;font:13px/1.6 monospace;box-shadow:0 4px 20px rgba(0,0,0,.5);min-width:340px`;
  document.body.appendChild(panel);
  const say = (t, c) => { panel.innerHTML = t; if (c) panel.style.borderLeft = `4px solid ${c}`; };

  // ---- 0. 登录令牌 ----
  const session = await fetch('/api/auth/session', { credentials: 'include' }).then(r => r.json()).catch(() => null);
  const token = session?.accessToken;
  if (!token) { say('没拿到登录信息：先在这个页面登录 ChatGPT，再运行', '#f44336'); return; }
  const did = document.cookie.match(/oai-did=([^;]+)/)?.[1];
  const headers = { Authorization: `Bearer ${token}`, ...(did ? { 'oai-device-id': did } : {}) };

  async function get(path, tries = 4) {
    for (let i = 0; ; i++) {
      const r = await fetch(path, { credentials: 'include', headers });
      if (r.ok) return r.json();
      if ((r.status === 429 || r.status >= 500) && i < tries) { await sleep(3000 * 2 ** i); continue; }   // 被限速：等一会儿再试
      throw new Error(`HTTP ${r.status}`);
    }
  }
  const secs = t => typeof t === 'number' ? t : t ? Date.parse(t) / 1000 : 0;   // 接口有时给秒数、有时给 ISO

  let cache = {};
  if (INCREMENTAL) { try { cache = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) { cache = {}; } }

  // ---- 1. 列对话 ----
  const seen = new Map();
  const keep = c => { if (c?.id && secs(c.update_time || c.create_time) >= cutoff && !seen.has(c.id)) seen.set(c.id, c); };
  for (const archived of [false, true]) {
    for (let offset = 0; ; offset += 100) {
      say(`列对话中…${archived ? '（已归档）' : ''} 已有 ${seen.size}`, '#2196F3');
      const j = await get(`/backend-api/conversations?offset=${offset}&limit=100&order=updated&is_archived=${archived}`);
      const items = j.items || [];
      items.forEach(keep);
      if (items.length < 100 || seen.size >= MAX) break;
      await sleep(300);
    }
  }
  try {                                                          // 项目里的对话：普通列表里不一定有
    const side = await get('/backend-api/gizmos/snorlax/sidebar?conversations_per_gizmo=0');
    for (const g of side.items || []) {
      const gid = g.gizmo?.gizmo?.id || g.gizmo?.id;
      if (!gid) continue;
      let cursor = '0';
      while (cursor != null) {
        const j = await get(`/backend-api/gizmos/${gid}/conversations?cursor=${cursor}`);
        (j.items || []).forEach(keep);
        cursor = j.cursor ?? null;
        await sleep(300);
      }
    }
  } catch (e) { console.warn('项目列表拿不到，跳过', e); }
  const list = [...seen.values()].slice(0, MAX);
  if (!list.length) { say('没有符合条件的对话', '#ff9800'); return; }

  // ---- 2. 逐个拉，没变的跳过 ----
  const textOf = m => {
    const c = m.content || {};
    if (!['text', 'multimodal_text'].includes(c.content_type)) return '';   // 代码执行、搜索结果、思考过程、自定义说明都不要
    return (c.parts || []).filter(p => typeof p === 'string').join('\n').trim();
  };
  const conversations = [];
  let fetched = 0, reused = 0, failed = 0, next = 0, done = 0;
  const saveCache = () => { if (INCREMENTAL) { try { localStorage.setItem(STORE_KEY, JSON.stringify(cache)); } catch (_) {} } };
  async function one(c) {
    const upd = String(c.update_time || c.create_time || '');
    if (INCREMENTAL && cache[c.id] === upd) { reused++; return; }   // 没变：Ephemeris 里已经有了，不用再放进文件
    try {
      const d = await get(`/backend-api/conversation/${c.id}`);
      const chain = [];
      for (let id = d.current_node; id && d.mapping[id]; id = d.mapping[id].parent) chain.push(d.mapping[id]);   // 最后停在的那一支
      const messages = [];
      for (const node of chain.reverse()) {
        const m = node.message;
        if (!m || !m.create_time) continue;
        const role = m.author?.role;
        if (role !== 'user' && role !== 'assistant') continue;
        if (m.metadata?.is_visually_hidden_from_conversation) continue;
        if (role === 'assistant' && m.recipient && m.recipient !== 'all') continue;     // 发给工具的
        const text = textOf(m);
        if (text) messages.push({ role: role === 'user' ? 'human' : 'assistant', created_at: new Date(m.create_time * 1000).toISOString(), text });
      }
      if (!messages.length) throw new Error('无正文');
      const obj = { uuid: 'chatgpt-' + c.id, name: d.title || c.title || 'Untitled',
                    created_at: new Date(secs(d.create_time || c.create_time) * 1000).toISOString(), updated_at: upd, messages };
      conversations.push(obj);
      cache[c.id] = upd;
      fetched++;
    } catch (e) { console.warn('失败', c.title, c.id, e); failed++; }
    await sleep(DELAY_MS);
  }
  // 几路同时拉；每做完 50 个存一次进度，中途关掉页面下次也能接着（已拉的走缓存）
  await Promise.all(Array.from({ length: PARALLEL }, async () => {
    while (next < list.length) {
      const c = list[next++];
      await one(c);
      done++;
      say(`[${done}/${list.length}] ${(c.title || '').slice(0, 36)}<br>拉取 ${fetched} · 没变跳过 ${reused} · 失败 ${failed}`, '#2196F3');
      if (done % 50 === 0) saveCache();
    }
  }));
  saveCache();

  // ---- 3. 下载 ----
  const nMsg = conversations.reduce((a, c) => a + c.messages.length, 0);
  const payload = { exporter: 'ephemeris-chatgpt', exported_at: new Date().toISOString(), since: START_DATE,
                    account: session?.user?.email || '', n_conversations: conversations.length, n_messages: nMsg, conversations };
  if (DOWNLOAD) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(payload)], { type: 'application/json' }));
    a.download = `chatgpt_export_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  }
  say(`<b>完成</b><br>${conversations.length} 个对话 · ${nMsg} 条消息<br>拉取 ${fetched} · 没变跳过 ${reused}` +
      (failed ? ` · 失败 ${failed}（控制台里有原因）` : ''), '#4CAF50');
  setTimeout(() => panel.remove(), 8000);
  return payload;
}
exportChatGPT();
