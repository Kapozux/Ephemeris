// 浮层「Notion」栏：日记推到 Notion 的一览和同步。
import { $, esc, rel } from './util.js';

// 判别「哪页是 Ephemeris 推的」在后端做（icon 💬 + 裸日期标题 + notion_pages 表）。
// 这里只管把结果摆出来，以及起/盯后台任务。

let NTPOLL = null;

function ntRow(label, n, hint) {
  return `<div class="pane-row"><span class="pane-row-title">${label}<span class="pane-row-meta" style="margin-left:10px">${hint || ''}</span></span><span class="pane-row-meta">${n} 天</span></div>`;
}

function renderNotion(s) {
  const body = $('#ntBody'), sub = $('#ntSub');

  if (s.computing && !s.connected) {      // 首次扫远端要 90s+，后台算着，这里等一拍
    sub.textContent = '在核对…';
    body.innerHTML = '<div class="pane-loading">在核对 Notion（远端上千页，头一次要一分多钟）…</div>';
    clearTimeout(NTPOLL); NTPOLL = setTimeout(pollNotion, 3000);
    return;
  }
  if (!s.connected) {
    sub.textContent = '没连上';
    body.innerHTML = `<div class="pane-sec">
      <p>还没配 Notion token。在 Notion 里建一个 <b>internal integration</b>（不是 personal access token），
      把 secret 写进 <code>ephemeris/.env</code> 的 <code>NOTION_TOKEN=</code>，再把 Notes Database 共享给它，然后重启。</p>
      ${s.error ? `<p class="nt-note" style="color:var(--coral-deep)">${esc(s.error)}</p>` : ''}
    </div>`;
    return;
  }

  const j = s.job || {};
  sub.textContent = `已连接 · ${esc(s.integration || '')} · 远端 ${s.remote_pages} 页`;

  const pending = (s.create || 0) + (s.append || 0) + (s.update || 0) + (s.replace || 0);
  const running = j.running;
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;

  body.innerHTML = `
    <div class="pane-sec">
      <div class="pane-sec-head"><span class="pane-sec-label">${s.diaries} 篇日记</span>
        <span class="pane-sub">${pending ? `${pending} 天待同步` : '全部已同步'}</span></div>
      <div class="pane-list">
        ${ntRow('已同步', s.skip || 0, '内容没变，不动')}
        ${ntRow('新建', s.create || 0, '这天你没写过日记')}
        ${ntRow('追加', s.append || 0, '追到你自己那页末尾')}
        ${(s.update || 0) ? ntRow('重推', s.update, '日记重跑过，或是误建的重复页') : ''}
        ${(s.replace || 0) ? ntRow('换新', s.replace, '日记重跑过，内容在你自己那页里 — 只换掉分隔线之后属于 Ephemeris 的那段') : ''}
      </div>
    </div>
    <div class="pane-sec">
      ${running
        ? `<div class="nt-status"><span class="pulse"></span>${esc(j.action || '')} ${esc(j.current || '')} — ${j.done}/${j.total}（${pct}%）</div>`
        : pending
          ? `<button id="ntSync" class="btn-primary">同步这 ${pending} 天</button>`
          : `<div class="nt-note">没有待办。生成了新日记之后回来这里推。</div>`}
      ${j.finished && !running ? `<div class="nt-note" style="margin-top:10px">上次跑完 ${rel(j.finished)}${j.failed ? ` · 失败 ${j.failed}` : ''}</div>` : ''}
      ${(j.errors || []).length ? `<div class="pane-list" style="margin-top:12px">${j.errors.map(e =>
          `<div class="pane-row bad"><span class="pane-row-title">${esc(e.date)}</span><span class="pane-row-meta">${esc(e.error)}</span></div>`).join('')}</div>` : ''}
    </div>
    <div class="pane-sec">
      <div class="pane-sec-head"><span class="pane-sec-label">约定</span></div>
      <p class="nt-note">标题是裸日期（<code>2025-8-21</code>，不补零），属性全部留空交给 Notion AI。
      你自己写过日记的那天不会新建页，只在你那页末尾加一条分隔线再往后写。</p>
    </div>`;

  const b = $('#ntSync');
  if (b) b.onclick = async () => {
    b.disabled = true; b.textContent = '起来了…';
    await fetch('/api/notion/sync', { method: 'POST' });
    pollNotion();
  };

  clearTimeout(NTPOLL);
  if (running) NTPOLL = setTimeout(pollNotion, 1500);
}

async function pollNotion() {
  const s = await fetch('/api/notion/status').then(r => r.json()).catch(() => null);
  if (s) renderNotion(s);
}

$('#ntRefresh').onclick = async () => { $('#ntBody').innerHTML = '<div class="pane-loading">在重新核对 Notion…</div>';
  await fetch('/api/notion/status?refresh=1'); pollNotion(); };

// 打开这栏：头一次先摆个「在核对」，再去拿
export function ensureNotion() {
  if (!$('#ntBody').innerHTML.trim()) $('#ntBody').innerHTML = '<div class="pane-loading">在核对 Notion…</div>';
  pollNotion();
}
