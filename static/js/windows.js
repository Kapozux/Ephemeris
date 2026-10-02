// 按窗口回顾（侧栏切到「按窗口」）：和日记是转置关系 —— 日记把一天里所有窗口合成一篇；
// 这里挑一个窗口，看它自己的时间轴。只有跨天的窗口才值得看（1657 个里 165 个跨天），列表默认只给这些。
import { $, esc, rel } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';
import { msgHtml } from './day.js';

let WINS = [], WPOLL = null, WIN_SHOWN = 80;

function winRowHtml(w) {
  const title = (w.title || w.title_fallback || '').trim() || '(无标题)';
  return `<a class="winrow ${w.id === S.wsel ? 'sel' : ''}" data-w="${w.id}">
    <span class="wt">${esc(title)}</span>
    <span class="wm"><i class="src ${w.source}"></i>${w.days} 天 · ${w.n_msgs} 条${w.n_notes ? '<i class="dot" title="已生成回顾"></i>' : ''}</span>
    <span class="wd2">${(w.first_date || '').slice(2)} → ${(w.last_date || '').slice(2)}</span>
  </a>`;
}

function renderWinList() {
  const kw = ($('#winFilter').value || '').trim().toLowerCase();
  const hit = kw
    ? WINS.filter(w => ((w.title || '') + (w.title_fallback || '') + (w.preview || '')).toLowerCase().includes(kw))
    : WINS;
  const show = hit.slice(0, WIN_SHOWN);
  $('#winlist').innerHTML = show.map(winRowHtml).join('')
    + (hit.length > show.length ? `<a class="winmore" id="winMore">还有 ${hit.length - show.length} 个，点开</a>` : '')
    + (hit.length ? '' : '<div class="winempty">没有匹配的窗口</div>');
  const m = $('#winMore');
  if (m) m.onclick = () => { WIN_SHOWN += 200; renderWinList(); };
}

async function loadWindows() {
  const r = await fetch('/api/windows').then(r => r.json());
  WINS = r.windows;
  renderWinList();
}
export const ensureWindows = () => { if (!WINS.length) loadWindows(); };

function tlRow(d) {
  return `<div class="tlrow ${d.note ? '' : 'nonote'}" data-d="${d.date}">
    <div class="tldate"><span class="n">${d.date.slice(5)}</span><span class="y">${d.date.slice(0, 4)}</span></div>
    <div class="tlmain">
      <div class="tlnote">${d.note ? esc(d.note) : '<span class="dim">还没生成小结</span>'}${d.stale ? '<span class="tl-stale">这天后来又聊了，会重写</span>' : ''}</div>
      <div class="tlmeta">${d.msgs} 条 · ${(d.chars || 0).toLocaleString()} 字 · ${d.a}–${d.b}
        <a class="tlopen" data-d="${d.date}">展开原文</a>
        ${d.has_diary ? `<a class="tlday" data-day="${d.date}">看这天的日记</a>` : ''}</div>
      <div class="tlmsgs hidden" id="tlm-${d.date}"></div>
    </div>
  </div>`;
}

function renderWin(r) {
  const c = r.conv, j = r.job || {};
  const title = (c.title || c.title_fallback || '').trim() || '(无标题)';
  const days = r.days;
  $('#winTitle').textContent = title;
  $('#winMeta').textContent =
    `${days.length} 天 · ${days.reduce((s, d) => s + d.msgs, 0)} 条 · ` +
    `${days.reduce((s, d) => s + (d.chars || 0), 0).toLocaleString()} 字 · ` +
    `${days[0]?.date} → ${days[days.length - 1]?.date} · ${c.source}`;

  const missing = r.pending || 0;
  $('#winArc').innerHTML = j.running
    ? `<div class="r"><span class="pulse"></span>在读这个窗口… ${j.done}/${j.total}${j.current && j.current !== 'arc' ? ` · ${j.current}` : ''}</div>`
    : r.arc
      ? `<p>${esc(r.arc)}</p><div class="foot">${days.length} 天的小结汇总而成${r.generated_at ? ` · ${rel(r.generated_at)}` : ''}${missing ? ` · 有 ${missing} 天是新的或有更新，在补` : ''}</div>`
      : `<p class="dim">还没给这个窗口生成回顾。会为它活跃的 ${days.length} 天各写一句小结，再汇总成一段。</p>
         <button id="winGen" class="btn-primary" style="margin-top:14px">生成回顾</button>`;

  $('#winTl').innerHTML = days.map(tlRow).join('');

  const g = $('#winGen');
  if (g) g.onclick = () => startWinGen(false);

  clearTimeout(WPOLL);
  if (j.running) WPOLL = setTimeout(() => openWindow(S.wsel, true), 2000);
  // 已经做过回顾的窗口，又多聊了几天：自动只补这几天，旧小结不动
  else if (r.arc && missing && !WAUTO[S.wsel]) { WAUTO[S.wsel] = true; startWinGen(false); }
}

const WAUTO = {};      // 每个窗口每次打开页面只自动补一次，补失败了不会死循环
async function startWinGen(refresh) {
  if (!S.wsel) return;
  $('#winArc').innerHTML = '<div class="r"><span class="pulse"></span>起来了…</div>';
  await fetch(`/api/window/${S.wsel}${refresh ? '?refresh=1' : ''}`, { method: 'POST' });
  openWindow(S.wsel, true);
}

export async function openWindow(id, quiet) {
  S.wsel = id;
  if (!quiet) {
    document.querySelectorAll('.winrow.sel').forEach(x => x.classList.remove('sel'));
    document.querySelector(`.winrow[data-w="${id}"]`)?.classList.add('sel');
    $('#win').classList.remove('hidden');
    $('#main').scrollTop = 0;
  }
  const r = await fetch(`/api/window/${id}`).then(r => r.json());
  if (S.wsel !== id || r.error) return;
  renderWin(r);
}

$('#winRegen').onclick = () => { if (confirm('把这个窗口的小结全部删掉重写？\n（平时不用：新聊的天打开时会自动补，旧的不会动）')) startWinGen(true); };
$('#winFilter').addEventListener('input', () => { WIN_SHOWN = 80; renderWinList(); });
$('#winlist').addEventListener('click', e => {
  const a = e.target.closest('.winrow');
  if (a) navigate('win/' + a.dataset.w);
});

// 展开某一天在这个窗口里的原文
$('#winTl').addEventListener('click', async e => {
  const day = e.target.closest('.tlday');
  if (day) { navigate('day/' + day.dataset.day); return; }
  const more = e.target.closest('.more');
  if (more) {
    const x = more.parentElement;
    x.querySelector('.full').classList.remove('hidden');
    x.querySelector('.clip').remove(); more.remove(); return;
  }
  const op = e.target.closest('.tlopen');
  if (!op) return;
  const date = op.dataset.d, box = $('#tlm-' + date);
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); op.textContent = '展开原文'; return; }
  if (!box.innerHTML) {
    box.innerHTML = '<div class="dim" style="padding:8px 0">在取…</div>';
    const r = await fetch(`/api/window/${S.wsel}/day/${date}`).then(r => r.json());
    box.innerHTML = `<div class="tbar"><label><input type="checkbox" class="aitog"> 显示 AI 回复</label></div>`
      + r.messages.map(m => msgHtml(m)).join('');
    box.querySelector('.aitog').onchange = ev => box.classList.toggle('showai', ev.target.checked);
  }
  box.classList.remove('hidden');
  op.textContent = '收起';
});
