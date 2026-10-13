// 按窗口回顾（侧栏切到「按窗口」）：和日记是转置关系 —— 日记把一天里所有窗口合成一篇；
// 这里挑一个窗口，看它自己的时间轴。只有跨天的窗口才值得看（1657 个里 165 个跨天），列表默认只给这些。
import { $, esc, rel, toContent, scrollWithin, usd, ask } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';
import { msgHtml } from './day.js';

let WINS = [], WPOLL = null, WIN_SHOWN = 80;

function winRowHtml(w) {
  const title = (w.title || w.title_fallback || '').trim() || '(无标题)';
  return `<a class="wl-row ${w.id === S.wsel ? 'sel' : ''}" data-w="${w.id}">
    <span class="wl-title">${esc(title)}</span>
    <span class="wl-meta"><i class="src ${w.source}"></i>${w.days} 天 · ${w.n_msgs} 条${w.n_notes ? '<i class="wl-dot" title="已生成回顾"></i>' : ''}</span>
    <span class="wl-dates">${(w.first_date || '').slice(2)} → ${(w.last_date || '').slice(2)}</span>
  </a>`;
}

function renderWinList() {
  const kw = ($('#winFilter').value || '').trim().toLowerCase();
  const hit = kw
    ? WINS.filter(w => ((w.title || '') + (w.title_fallback || '') + (w.preview || '')).toLowerCase().includes(kw))
    : WINS;
  const show = hit.slice(0, WIN_SHOWN);
  $('#winlist').innerHTML = show.map(winRowHtml).join('')
    + (hit.length > show.length ? `<a class="wl-more" id="winMore">还有 ${hit.length - show.length} 个，点开</a>` : '')
    + (hit.length ? '' : '<div class="wl-empty">没有匹配的窗口</div>');
  const m = $('#winMore');
  if (m) m.onclick = () => { WIN_SHOWN += 200; renderWinList(); };
}

async function loadWindows() {
  const r = await fetch('/api/windows').then(r => r.json());
  WINS = r.windows;
  const i = WINS.findIndex(w => w.id === S.wsel);              // 直接打开 #/win/<id>：列表放长到它
  if (i >= WIN_SHOWN) WIN_SHOWN = i + 1;
  renderWinList();
  scrollWithin($('#winlist'), document.querySelector('.wl-row.sel'), true);
}
export const ensureWindows = () => { if (!WINS.length) loadWindows(); };

function tlRow(d) {
  return `<div class="tl-row ${d.note ? '' : 'nonote'}" data-d="${d.date}">
    <div class="tl-date"><span class="tl-day">${d.date.slice(5)}</span><span class="tl-year">${d.date.slice(0, 4)}</span></div>
    <div class="tl-main">
      <div class="tl-note">${d.note ? esc(d.note) : '<span class="dim">还没生成小结</span>'}${d.stale ? '<span class="tl-stale">这天后来又聊了，下次补的时候重写</span>' : ''}</div>
      <div class="tl-meta">${d.msgs} 条 · ${(d.chars || 0).toLocaleString()} 字 · ${d.a}–${d.b}
        <a class="tl-open" data-d="${d.date}">展开原文</a>
        ${d.has_diary ? `<a class="tl-diary" data-day="${d.date}">看这天的日记</a>` : ''}</div>
      <div class="tl-msgs hidden" id="tlm-${d.date}"></div>
    </div>
  </div>`;
}

function renderWin(r) {
  const c = r.conv, j = r.job || {};
  const title = (c.title || c.title_fallback || '').trim() || '(无标题)';
  const days = r.days;
  $('#winTitle').textContent = title;
  $('#winExport').href = `/api/conversation/${c.id}/export.md`;
  $('#winMeta').textContent =
    `${days.length} 天 · ${days.reduce((s, d) => s + d.msgs, 0)} 条 · ` +
    `${days.reduce((s, d) => s + (d.chars || 0), 0).toLocaleString()} 字 · ` +
    `${days[0]?.date} → ${days[days.length - 1]?.date} · ${{ ask: '问', cc: 'Claude Code' }[c.source] || c.source}`;

  const missing = r.pending || 0, pNow = r.price?.now || {}, pAll = r.price?.all || {};
  const price = p => `${p.calls} 次调用，${usd(p.cost)}`;
  $('#winArc').innerHTML = j.running
    ? `<div class="win-status"><span class="pulse"></span>在读这个窗口… ${j.done}/${j.total}${j.current && j.current !== 'arc' ? ` · ${j.current}` : ''}</div>`
    : r.arc
      ? `<p>${esc(r.arc)}</p><div class="diary-foot">${days.length} 天的小结汇总而成${r.generated_at ? ` · ${rel(r.generated_at)}` : ''}${missing ? ` · 后来又聊了 ${missing} 天，每周补一次 · <a id="winNow">现在就补（${price(pNow)}）</a>` : ''}</div>`
      : `<p class="dim">还没给这个窗口生成回顾。会为它活跃的 ${days.length} 天各写一句小结，再汇总成一段。</p>
         <button id="winGen" class="btn-primary" style="margin-top:14px">生成回顾（${price(pNow)}）</button>`;
  // 「全部重写」：只有已经有回顾时才有意义；会把现有的小结和汇总都替换掉，先问一句
  $('#winRegen').classList.toggle('hidden', !r.arc || !!j.running);
  $('#winRegen').onclick = e => ask(e.currentTarget, `把 ${days.length} 天的小结和汇总全部重写（${price(pAll)}）`, () => startWinGen(true));

  $('#winTl').innerHTML = days.map(tlRow).join('');

  const g = $('#winGen');
  if (g) g.onclick = () => startWinGen(false);
  const now = $('#winNow');
  if (now) now.onclick = () => startWinGen(false);

  clearTimeout(WPOLL);
  if (j.running) WPOLL = setTimeout(() => openWindow(S.wsel, true), 2000);
  // 又多聊了几天的窗口：不在打开时自动补（以前天天聊就天天重写一遍汇总），早上整理时每周补一次；急的话点「现在就补」
}

async function startWinGen(refresh) {
  if (!S.wsel) return;
  $('#winArc').innerHTML = '<div class="win-status"><span class="pulse"></span>起来了…</div>';
  await fetch(`/api/window/${S.wsel}${refresh ? '?refresh=1' : ''}`, { method: 'POST' });
  openWindow(S.wsel, true);
}

export async function openWindow(id, quiet) {
  S.wsel = id;
  if (!quiet) {
    // 选中的那个不在列表里（排在后面，或者被筛选框里留着的字筛掉了；比如从某天的「按窗口看全程」过来）：
    // 清掉筛选、列表放长到它，再把它滚进视野
    const i = WINS.findIndex(w => w.id === id);
    if (i >= 0 && !document.querySelector(`.wl-row[data-w="${id}"]`)) {
      $('#winFilter').value = '';
      WIN_SHOWN = Math.max(80, i + 1);
      renderWinList();
    }
    document.querySelectorAll('.wl-row.sel').forEach(x => x.classList.remove('sel'));
    const row = document.querySelector(`.wl-row[data-w="${id}"]`);
    row?.classList.add('sel');
    scrollWithin($('#winlist'), row, true);
    $('#win').classList.remove('hidden');
    toContent($('#win'));
  }
  const r = await fetch(`/api/window/${id}`).then(r => r.json());
  if (S.wsel !== id || r.error) return;
  renderWin(r);
}

$('#winFilter').addEventListener('input', () => { WIN_SHOWN = 80; renderWinList(); });
$('#winlist').addEventListener('click', e => {
  const a = e.target.closest('.wl-row');
  if (a) navigate('win/' + a.dataset.w);
});

// 展开某一天在这个窗口里的原文
$('#winTl').addEventListener('click', async e => {
  const day = e.target.closest('.tl-diary');
  if (day) { navigate(`day/${day.dataset.day}/conv/${S.wsel}`); return; }      // 到那天，并把这个窗口展开
  const more = e.target.closest('.msg-more');
  if (more) {
    const x = more.parentElement;
    x.querySelector('.msg-full').classList.remove('hidden');
    x.querySelector('.msg-clip').remove(); more.remove(); return;
  }
  const op = e.target.closest('.tl-open');
  if (!op) return;
  const date = op.dataset.d, box = $('#tlm-' + date);
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); op.textContent = '展开原文'; return; }
  if (!box.innerHTML) {
    box.innerHTML = '<div class="dim" style="padding:8px 0">在取…</div>';
    const r = await fetch(`/api/window/${S.wsel}/day/${date}`).then(r => r.json());
    box.innerHTML = `<div class="thread-bar"><label><input type="checkbox" class="thread-aitog"> 显示 AI 回复</label></div>`
      + r.messages.map(m => msgHtml(m)).join('');
    box.querySelector('.thread-aitog').onchange = ev => box.classList.toggle('showai', ev.target.checked);
  }
  box.classList.remove('hidden');
  op.textContent = '收起';
});
