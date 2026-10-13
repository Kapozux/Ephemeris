// 浮层「主题」栏：主题账本 —— 一件事聊了多少天、断过多久、现在到哪。
import { $, esc } from './util.js';
import { navigate } from './nav.js';

let THEMES = null, TH_OPEN = null, TH_ONES = false, TH_CLICKED = false;
const WINDOW_DAYS = 182;          // 横条只画最近半年：全程画的话几年的刻度全挤在最右边

// 展开哪个主题由路由决定（#/panel/themes/<id>）：行里点开 / 收起也走 navigate，前进后退、日记里的主题胶囊都是同一条路
export async function loadThemes(openId) {
  TH_OPEN = openId ? +openId : null;
  // 每次打开都重拿：新写一篇日记账本就变了，数据量也就几百行
  if (!THEMES) $('#thBody').innerHTML = '<div class="pane-loading">在翻账本…</div>';
  THEMES = (await fetch('/api/themes').then(r => r.json())).themes;
  renderThemes();
  // 从别处点进来（日记里的主题胶囊）才把它滚到中间；在列表里自己点开的不动
  if (openId && !TH_CLICKED) setTimeout(() => $(`.th[data-id="${openId}"]`)?.scrollIntoView({ block: 'center' }), 30);
  TH_CLICKED = false;
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
  // 横条的时间轴：所有主题共用同一段（最近半年，到今天），一眼看出谁连着、谁断了又回来；更早的只在左端标一个 ←
  const all = THEMES.flatMap(t => t.days.map(d => d.date)).sort();
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const now = new Date(), today = iso(now);
  const from = iso(new Date(now.getFullYear(), now.getMonth(), now.getDate() - WINDOW_DAYS));
  const end = all[all.length - 1] > today ? all[all.length - 1] : today;      // 电脑时钟慢了也别让刻度跑出横条
  const t0 = Date.parse(from), span = Math.max(1, Date.parse(end) - t0);
  const pos = d => ((Date.parse(d) - t0) / span * 100).toFixed(2);
  const md = d => `${+d.slice(5, 7)}月${+d.slice(8)}日`;
  const strip = t => {
    const early = t.days.filter(d => d.date < from).length;
    return `<span class="th-strip">${early ? `<em class="th-early" title="半年前还有 ${early} 天">←</em>` : ''}<span class="th-track">${
      t.days.filter(d => d.date >= from).map(d => `<i style="left:${pos(d.date)}%"></i>`).join('')}</span></span>`;
  };
  const ago = d => { const n = Math.round((Date.parse(today) - Date.parse(d)) / 864e5); return n <= 0 ? '今天' : n === 1 ? '昨天' : `${n} 天前`; };

  $('#thSub').textContent = `${THEMES.length} 个主题 · ${all[0]} → ${all[all.length - 1]}`;
  $('#thBody').innerHTML = `<div class="th-axis" aria-hidden="true"><span class="th-axis-strip"><em>更早 ←</em><span>${md(from)}</span><span>${end === today ? '今天' : md(end)}</span></span></div>` + list.map(t => `
    <div class="th${TH_OPEN === t.id ? ' open' : ''}" data-id="${t.id}">
      <div class="th-row" role="button" tabindex="0" aria-expanded="${TH_OPEN === t.id}">
        <span class="th-caret" aria-hidden="true">›</span>
        <b>${esc(t.name)}</b>
        <span class="th-n tnum">${t.n_days} 天</span>
        ${strip(t)}
        <span class="th-last">${ago(t.last)}</span>
      </div>
      <div class="th-state">${esc(t.state || '')}</div>
      ${TH_OPEN === t.id ? `<ol class="th-days">${t.days.slice().reverse().map(d =>
        `<li><a data-open="${d.date}">${d.date}</a><span>${(d.srcs || '').includes('hand') ? '<em class="th-src-hand">手写</em>' : ''}${esc(d.note || '')}</span></li>`).join('')}</ol>` : ''}
    </div>`).join('') + (!kw && ones.length
      ? `<a class="th-more" id="thOnes">${TH_ONES ? '收起' : `还有 ${ones.length} 个只出现过一天的`}</a>` : '');
  const more = $('#thOnes');
  if (more) more.onclick = () => { TH_ONES = !TH_ONES; renderThemes(); };
  $('#thBody').querySelectorAll('.th-row').forEach(r => {
    r.onclick = () => {
      const id = +r.parentElement.dataset.id;
      TH_CLICKED = true;
      navigate(TH_OPEN === id ? 'panel/themes' : 'panel/themes/' + id);
    };
    r.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.click(); } };
  });
  $('#thBody').querySelectorAll('[data-open]').forEach(a => a.onclick = e => { e.stopPropagation(); navigate('day/' + a.dataset.open); });
}
$('#thFilter').oninput = () => THEMES && renderThemes();
