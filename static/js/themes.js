// 浮层「主题」栏：主题账本 —— 一件事聊了多少天、断过多久、现在到哪。
import { $, esc } from './util.js';
import { navigate } from './nav.js';

let THEMES = null, TH_OPEN = null, TH_ONES = false;

export async function loadThemes(openId) {
  if (openId) TH_OPEN = +openId;
  // 每次打开都重拿：新写一篇日记账本就变了，数据量也就几百行
  if (!THEMES) $('#thBody').innerHTML = '<div class="pane-loading">在翻账本…</div>';
  THEMES = (await fetch('/api/themes').then(r => r.json())).themes;
  renderThemes();
  if (openId) setTimeout(() => $(`.th[data-id="${openId}"]`)?.scrollIntoView({ block: 'center' }), 30);
}

function renderThemes() {
  const kw = ($('#thFilter').value || '').trim().toLowerCase();
  // 只出现过一天的占大半（一次性的事），默认收起来，免得把真正连着的线淹掉。筛选/点进来的不收
  const hit = THEMES.filter(t => !kw || t.name.toLowerCase().includes(kw) || (t.state || '').toLowerCase().includes(kw));
  const ones = hit.filter(t => t.n_days === 1 && t.id !== TH_OPEN);
  const list = (TH_ONES || kw) ? hit : hit.filter(t => !ones.includes(t));
  if (!THEMES.length) {
    $('#thBody').innerHTML = '<div class="pane-loading">账本还是空的——新写的日记会往里记。</div>';
    return;
  }
  // 横条的时间轴：所有主题共用同一段，一眼看出谁连着、谁断了又回来
  const all = THEMES.flatMap(t => t.days.map(d => d.date)).sort();
  const t0 = Date.parse(all[0]), t1 = Date.parse(all[all.length - 1]);
  const span = Math.max(1, t1 - t0);
  const pos = d => ((Date.parse(d) - t0) / span * 100).toFixed(2);
  const today = new Date().toISOString().slice(0, 10);
  const ago = d => { const n = Math.round((Date.parse(today) - Date.parse(d)) / 864e5); return n <= 0 ? '今天' : n === 1 ? '昨天' : `${n} 天前`; };

  $('#thSub').textContent = `${THEMES.length} 个主题 · ${all[0]} → ${all[all.length - 1]}`;
  $('#thBody').innerHTML = list.map(t => `
    <div class="th${TH_OPEN === t.id ? ' open' : ''}" data-id="${t.id}">
      <div class="th-row">
        <b>${esc(t.name)}</b>
        <span class="th-n tnum">${t.n_days} 天</span>
        <span class="th-strip">${t.days.map(d => `<i style="left:${pos(d.date)}%"></i>`).join('')}</span>
        <span class="th-last">${ago(t.last)}</span>
      </div>
      <div class="th-state">${esc(t.state || '')}</div>
      ${TH_OPEN === t.id ? `<ol class="th-days">${t.days.slice().reverse().map(d =>
        `<li><a data-open="${d.date}">${d.date}</a><span>${(d.srcs || '').includes('hand') ? '<em class="th-src-hand">手写</em>' : ''}${esc(d.note || '')}</span></li>`).join('')}</ol>` : ''}
    </div>`).join('') + (!kw && ones.length
      ? `<a class="th-more" id="thOnes">${TH_ONES ? '收起' : `还有 ${ones.length} 个只出现过一天的`}</a>` : '');
  const more = $('#thOnes');
  if (more) more.onclick = () => { TH_ONES = !TH_ONES; renderThemes(); };
  $('#thBody').querySelectorAll('.th-row').forEach(r => r.onclick = () => {
    const id = +r.parentElement.dataset.id;
    TH_OPEN = TH_OPEN === id ? null : id;
    renderThemes();
  });
  $('#thBody').querySelectorAll('[data-open]').forEach(a => a.onclick = e => { e.stopPropagation(); navigate('day/' + a.dataset.open); });
}
$('#thFilter').oninput = () => THEMES && renderThemes();
