/**
 * Gemini 对话导出（给 Ephemeris 用，2026-10-10）
 *
 * Gemini 自己没有「导出一个对话」：Takeout 只能整个导，要等几小时到几天。
 * 这个脚本在 gemini.google.com 页面的控制台里跑，用网页自己的接口（batchexecute）一个个拉：
 *   MaZiqc  列对话（每页 100 个，带下一页的 token）
 *   hNvQHb  读一个对话的全部轮次（新的在前，每页 50 轮，带 token）
 * 每一轮带时间（精确到毫秒，跟 Takeout 里的一模一样），所以拖进 Ephemeris 能按天写日记，
 * 跟以前用 Takeout 导进来的重叠部分也能认出来、不会重复。
 *
 * 用法：打开 gemini.google.com → ⌥⌘J（Windows：Ctrl+Shift+J）打开控制台 → 粘贴 → 回车。
 * Chrome 第一次粘贴会让你先打一行 allow pasting。跑完会下载一个 gemini_export_日期.json，
 * 拖进 Ephemeris 的「窗口与导入」就行。
 *
 * 增量：localStorage 记着每个对话上次拉到的更新时间，第二次起只拉变过的。想全部重拉，把 INCREMENTAL 改成 false。
 * 这是 Google 没公开的接口，哪天网页改版这个脚本可能就不灵了 —— 那时还可以用 Takeout。
 */
async function exportGemini(opts = {}) {
  const START_DATE = opts.since || '2000-01-01';   // 只拉这天之后更新过的对话
  const INCREMENTAL = opts.incremental ?? true;
  const DOWNLOAD = opts.download ?? true;
  const MAX = opts.max || Infinity;                // 测试用：只拉前几个
  const DELAY_MS = 300;
  const STORE_KEY = 'ephemeris_gemini_export_v1';

  const w = window.WIZ_global_data;
  if (!w || !w.SNlM0e) { alert('没拿到登录信息：先在这个页面登录 Gemini，再运行'); return; }
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const panel = document.createElement('div');
  panel.style.cssText = `position:fixed;top:10px;right:10px;z-index:99999;background:#1a1a2e;color:#e0e0e0;
    padding:16px 20px;border-radius:8px;font:13px/1.6 -apple-system,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,.5);min-width:320px`;
  if (DOWNLOAD) document.body.appendChild(panel);
  const say = (t, c) => { panel.innerHTML = t; if (c) panel.style.borderLeft = `4px solid ${c}`; };

  async function rpc(id, payload) {
    const qs = new URLSearchParams({ rpcids: id, 'source-path': '/app', bl: w.cfb2h, 'f.sid': w.FdrFJe,
      hl: 'en', _reqid: String(100000 + Math.floor(Math.random() * 800000)), rt: 'c' });
    const body = new URLSearchParams({ 'f.req': JSON.stringify([[[id, JSON.stringify(payload), null, 'generic']]]), at: w.SNlM0e });
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await fetch('/_/BardChatUi/data/batchexecute?' + qs, { method: 'POST', body,
          headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8' } });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const t = await r.text();
        for (const line of t.split('\n')) {
          if (!line.startsWith('[["wrb.fr"')) continue;
          for (const x of JSON.parse(line)) if (x[0] === 'wrb.fr' && x[1] === id) return x[2] ? JSON.parse(x[2]) : null;
        }
        return null;
      } catch (e) {
        if (attempt >= 3) throw e;
        await sleep(1500 * attempt);
      }
    }
  }

  // ---- 1. 列对话 ----
  say('列对话中…', '#2196F3');
  const cutoff = Date.parse(START_DATE) / 1000;
  let list = [], tok = null;
  do {
    const p = await rpc('MaZiqc', [100, tok, [0, null, 1]]);
    for (const c of (p && p[2]) || []) list.push({ id: c[0], title: c[1] || '', updated: (c[5] && c[5][0]) || 0 });
    tok = p && p[1];
    say(`列对话中… ${list.length}`);
  } while (tok);

  let cache = {};
  if (INCREMENTAL) { try { cache = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) { cache = {}; } }
  const todo = list.filter(c => c.updated >= cutoff && !(INCREMENTAL && cache[c.id] === c.updated)).slice(0, MAX);

  // ---- 2. 一个个读 ----
  const pickReply = t => {
    const cands = (t[3] && t[3][0]) || [];
    const c = cands.find(x => x && x[0] === t[3][3]) || cands[0];
    let text = (c && c[1] && c[1][0]) || '';
    if (/^https?:\/\/googleusercontent\.com\/\S+$/.test(text.trim())) text = '[图片]';     // 生成图片的占位链接
    return text;
  };
  const out = [], failed = [];
  for (let i = 0; i < todo.length; i++) {
    const c = todo[i];
    say(`读对话 ${i + 1} / ${todo.length}<br><span style="opacity:.7">${c.title.replace(/[<&]/g, '').slice(0, 40)}</span>`, '#2196F3');
    try {
      const turns = [];
      let t2 = null;
      do {
        const r = await rpc('hNvQHb', [c.id, 50, t2, 1, [0], [4], null, 1]);
        for (const t of (r && r[0]) || []) {
          const ts = t[4] || [0, 0];
          turns.push({ ts, user: (t[2] && t[2][0] && t[2][0][0]) || '', reply: pickReply(t) });
        }
        t2 = r && typeof r[1] === 'string' ? r[1] : null;
        await sleep(DELAY_MS);
      } while (t2);
      turns.reverse();                                // 接口给的是新的在前
      out.push({ id: c.id, title: c.title, updated: c.updated, turns });
      cache[c.id] = c.updated;
    } catch (e) {
      failed.push({ id: c.id, title: c.title, error: String(e) });
    }
  }
  if (INCREMENTAL && DOWNLOAD) localStorage.setItem(STORE_KEY, JSON.stringify(cache));

  // exporter 放第一个：Ephemeris 靠文件开头这一段认格式
  const payload = { exporter: 'ephemeris-gemini', version: 1, exported_at: new Date().toISOString(),
    since: START_DATE, n_listed: list.length, conversations: out, failed };
  if (!DOWNLOAD) return payload;
  if (!out.length) { say(`没有新的对话（一共 ${list.length} 个，都拉过了）`, '#4CAF50'); return payload; }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(payload)], { type: 'application/json' }));
  a.download = `gemini_export_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  say(`好了：${out.length} 个对话${failed.length ? `，${failed.length} 个没拉下来（下次再试）` : ''}。<br>把下载的文件拖进 Ephemeris 的「窗口与导入」。`, '#4CAF50');
  setTimeout(() => panel.remove(), 15000);
  return payload;
}
exportGemini();
