/**
 * Claude 对话导出（JSON 版，带增量）
 *
 * 和原来那版 markdown 导出的区别：
 *   1. 输出 JSON，created_at 保留原始 ISO —— 按天切片必须要这个，
 *      toLocaleString 转成 "Jun 27, 2026, 9:13 PM" 之后时区和精度都没了。
 *   2. 增量。用 localStorage 记住每个对话的 updated_at，没变的直接跳过，
 *      第二次以后只拉新的（422 个对话全量要跑 4 分半）。
 *   3. 保留 uuid，跨次导出能去重、能回链。
 *
 * 在 claude.ai 页面的控制台里粘贴运行。
 */
(async function exportClaudeJSON() {
  const START_DATE = '2026-01-01';   // 只看 updated_at 晚于这天的对话
  const INCREMENTAL = true;          // false = 忽略缓存全量重拉
  const INCLUDE_ASSISTANT = true;    // 日记只需要 human，但留着方便回查
  const DELAY_MS = 500;

  const STORE_KEY = 'claude_export_state_v1';
  const cutoff = new Date(START_DATE);
  const orgId = document.cookie.match(/lastActiveOrg=([^;]+)/)?.[1];
  if (!orgId) { alert('Cookie 里找不到 org id'); return; }

  const panel = document.createElement('div');
  panel.style.cssText = `position:fixed;top:10px;right:10px;z-index:99999;background:#1a1a2e;
    color:#e0e0e0;padding:16px 20px;border-radius:8px;font:13px/1.6 monospace;
    box-shadow:0 4px 20px rgba(0,0,0,.5);min-width:340px`;
  document.body.appendChild(panel);
  const say = (t, c) => { panel.innerHTML = t; if (c) panel.style.borderLeft = `4px solid ${c}`; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  let cache = {};
  if (INCREMENTAL) {
    try { cache = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) { cache = {}; }
  }

  // ---- 1. 列对话（分页）----
  say('列对话中…', '#2196F3');
  const PAGE = 50;
  let list = [], offset = 0, page = 0;
  while (true) {
    page++;
    const r = await fetch(
      `/api/organizations/${orgId}/chat_conversations?limit=${PAGE}&offset=${offset}`,
      { credentials: 'include', headers: { 'Content-Type': 'application/json' } });
    if (!r.ok) { say(`列表失败 ${r.status}`, '#f44336'); return; }
    const j = await r.json();
    const items = Array.isArray(j) ? j : (j.data || j.results || j.chat_conversations || []);
    if (!items.length) break;

    // 不靠「这一页出现旧的就停」——显式按 updated_at 判断，顺序变了也不会漏
    const fresh = items.filter(c => new Date(c.updated_at || c.created_at || 0) >= cutoff);
    list.push(...fresh);
    say(`列对话中… 第 ${page} 页，累计 ${list.length}`, '#2196F3');
    if (fresh.length < items.length || items.length < PAGE) break;
    offset += PAGE;
    await sleep(250);
  }
  if (!list.length) { say('没有符合条件的对话', '#ff9800'); return; }

  // ---- 2. 逐个拉，没变的走缓存 ----
  const conversations = [];
  let fetched = 0, reused = 0, failed = 0;
  for (let i = 0; i < list.length; i++) {
    const c = list[i];
    const id = c.uuid || c.id;
    const upd = c.updated_at || c.created_at || '';
    say(`[${i + 1}/${list.length}] ${(c.name || '').slice(0, 36)}<br>` +
        `拉取 ${fetched} · 复用 ${reused} · 失败 ${failed}`, '#2196F3');

    if (INCREMENTAL && cache[id] && cache[id].updated_at === upd) {
      conversations.push(cache[id].data); reused++; continue;
    }
    try {
      const r = await fetch(
        `/api/organizations/${orgId}/chat_conversations/${id}` +
        `?tree=true&rendering_mode=messages&render_all_tools=true`,
        { credentials: 'include', headers: { 'Content-Type': 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (!d.chat_messages?.length) throw new Error('空对话');

      const messages = [];
      for (const m of d.chat_messages) {
        const role = m.sender === 'human' ? 'human' : 'assistant';
        if (role === 'assistant' && !INCLUDE_ASSISTANT) continue;
        const text = (m.content || [])
          .filter(x => x.type === 'text' || !x.type)
          .map(x => x.text ?? '').join('').trim();
        if (!text) continue;
        messages.push({ role, created_at: m.created_at, text });   // ISO 原样保留
      }
      if (!messages.length) throw new Error('无正文');

      const obj = { uuid: id, name: c.name || 'Untitled',
                    created_at: c.created_at, updated_at: upd, messages };
      conversations.push(obj);
      cache[id] = { updated_at: upd, data: obj };
      fetched++;
    } catch (e) {
      console.warn('失败', c.name, id, e); failed++;
    }
    if (i < list.length - 1) await sleep(DELAY_MS);
  }

  if (INCREMENTAL) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(cache)); }
    catch (_) { console.warn('缓存写不下，下次仍是全量'); }
  }

  // ---- 3. 下载 ----
  const nMsg = conversations.reduce((a, c) => a + c.messages.length, 0);
  const payload = {
    exported_at: new Date().toISOString(),
    since: START_DATE,
    n_conversations: conversations.length,
    n_messages: nMsg,
    conversations,
  };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(payload)], { type: 'application/json' }));
  a.download = `claude_export_${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);

  say(`<b>完成</b><br>${conversations.length} 个对话 · ${nMsg} 条消息<br>` +
      `拉取 ${fetched} · 复用 ${reused}` + (failed ? ` · 失败 ${failed}` : ''), '#4CAF50');
  setTimeout(() => panel.remove(), 6000);
})();
