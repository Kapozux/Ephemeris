// 打卡页。两屏：此刻（记一下）/ 这个月（比例条 + 日历星 + 星座 + 一句话）。
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const WK = '日一二三四五六';

// 颜色只编码好坏：暖 = 好（越好越亮），冷 = 糟（越糟越深），灰 = 平。「说不上来」单独一个深灰。
// 是哪种情绪永远配文字，不靠颜色区分（焦虑、烦躁同分同色）。
function vcol(v, word) {
  if (word === '说不上来') return '#6E7280';
  if (v >= 1.75) return '#F5A45D';
  if (v >= 1.25) return '#EDBB72';
  if (v >= 0.25) return '#D8C690';
  if (v > -0.25) return '#A3A9B6';
  if (v > -0.75) return '#A5B7DA';
  if (v > -1.25) return '#7F9ED8';
  if (v > -1.75) return '#5F83D0';
  return '#4B63C2';
}
const WV = {};   // 词 → 分

const S = { view: 'now', date: null, today: null, words: [], items: [], word: null, editing: null,
            month: null, M: null, selDay: null };

// ---------------------------------------------------------------- 路由（?date= & v=month）
function syncUrl() {
  const p = new URLSearchParams();
  if (S.date && S.date !== S.today) p.set('date', S.date);
  if (S.view === 'month') { p.set('v', 'month'); if (S.month) p.set('m', S.month); }
  history.replaceState(null, '', '/checkin' + (p.toString() ? '?' + p : ''));
}

function setView(v) {
  S.view = v;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.v === v));
  $('#vNow').classList.toggle('hidden', v !== 'now');
  $('#vMonth').classList.toggle('hidden', v !== 'month');
  if (v === 'month') loadMonth(S.month || (S.date || S.today).slice(0, 7));
  syncUrl();
}
document.querySelectorAll('.tab').forEach(t => t.onclick = () => setView(t.dataset.v));

// ---------------------------------------------------------------- 此刻
function dateLabel(d) {
  const diff = Math.round((Date.parse(S.today) - Date.parse(d)) / 864e5);
  const tag = diff === 0 ? '今天' : diff === 1 ? '昨天' : diff === 2 ? '前天' : `${diff} 天前`;
  const dt = new Date(d + 'T12:00:00');
  return `${tag} · ${dt.getMonth() + 1} 月 ${dt.getDate()} 日 周${WK[dt.getDay()]}`;
}

async function loadNow(date) {
  const r = await fetch('/api/checkin/home' + (date ? '?date=' + date : '')).then(r => r.json());
  Object.assign(S, { date: r.date, today: r.today, words: r.words, items: r.items });
  r.words.forEach(w => WV[w.word] = w.v);
  renderProgress(r.progress);
  renderTube(r.orbs);
  renderNow();
  syncUrl();
}

function renderProgress(p) {
  $('#prog').innerHTML = [['光球', p.moments], ['打卡天数', p.days], ['连续', p.streak + ' 天']]
    .map(([k, v]) => `<div class="pcard"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

function renderNow() {
  const isToday = S.date === S.today;
  $('#dLabel').textContent = dateLabel(S.date);
  $('#dNext').disabled = S.date >= S.today;
  $('#dToday').classList.toggle('hidden', isToday);
  $('#hTitle').firstChild.textContent = isToday ? '此刻怎么样' : '那天怎么样';
  $('#tags').innerHTML = S.words.map(w =>
    `<button class="tag${S.word === w.word ? ' on' : ''}" data-w="${w.word}" style="color:${vcol(w.v, w.word)}">${w.word}</button>`).join('');
  $('#tags').querySelectorAll('.tag').forEach(b => b.onclick = () => { S.word = S.word === b.dataset.w ? null : b.dataset.w; renderNow(); });
  $('#record').disabled = !S.word;
  $('#record').textContent = S.editing ? '改好了 →' : '记下 →';
  $('#cancelEdit').classList.toggle('hidden', !S.editing);

  $('#todayList').innerHTML = S.items.length
    ? `<h3>${isToday ? '今天' : '这天'}记的 · ${S.items.length} 条</h3>` + S.items.slice().reverse().map(x => `
      <div class="item${S.editing === x.id ? ' editing' : ''}">
        <span class="t">${(x.created_at || '').slice(11, 16)}</span>
        <span class="w" style="color:${vcol(x.valence, x.word)}">${esc(x.word)}</span>
        <span class="n">${esc(x.note || '')}</span>
        <span class="ops"><a data-e="${x.id}">改</a><a data-d="${x.id}">删</a></span>
        ${x.reply ? `<div class="r">${esc(x.reply)}</div>` : ''}
      </div>`).join('')
    : '';
  $('#todayList').querySelectorAll('[data-e]').forEach(a => a.onclick = () => {
    const x = S.items.find(i => i.id === +a.dataset.e);
    S.editing = x.id; S.word = x.word; $('#note').value = x.note || ''; renderNow();
    window.scrollTo({ top: 0, behavior: 'smooth' }); $('#note').focus();
  });
  $('#todayList').querySelectorAll('[data-d]').forEach(a => a.onclick = async () => {
    if (!confirm('删掉这条？')) return;
    await fetch('/api/checkin/' + a.dataset.d, { method: 'DELETE' });
    $('#oracle').classList.add('hidden');
    loadNow(S.date);
  });
}

$('#record').onclick = async () => {
  if (!S.word) return;
  const body = JSON.stringify({ date: S.date, word: S.word, note: $('#note').value });
  const opt = { headers: { 'Content-Type': 'application/json' }, body };
  let id = S.editing;
  if (id) await fetch('/api/checkin/' + id, { method: 'PUT', ...opt });
  else id = (await fetch('/api/checkin', { method: 'POST', ...opt }).then(r => r.json())).id;
  S.word = null; S.editing = null; $('#note').value = '';
  await loadNow(S.date);
  // 它说：记完才去要，记下这个动作本身不等模型
  const o = $('#oracle');
  o.classList.remove('hidden'); o.classList.add('thinking'); $('#oText').textContent = '在想';
  const r = await fetch(`/api/checkin/${id}/reply`, { method: 'POST' }).then(r => r.json());
  o.classList.remove('thinking'); $('#oText').textContent = r.reply || '（这次没想出来说什么）';
  loadNow(S.date);
};
$('#note').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) $('#record').click(); });
$('#cancelEdit').onclick = () => { S.editing = null; S.word = null; $('#note').value = ''; renderNow(); };
const shiftDay = n => { const d = new Date(S.date + 'T12:00:00'); d.setDate(d.getDate() + n);
  $('#oracle').classList.add('hidden'); loadNow(d.toISOString().slice(0, 10)); };
$('#dPrev').onclick = () => shiftDay(-1);
$('#dNext').onclick = () => { if (S.date < S.today) shiftDay(1); };
$('#dToday').onclick = () => { $('#oracle').classList.add('hidden'); loadNow(); };

// 光球管：最近记的一刻一颗
function orbStyle(v, word) {
  const c = vcol(v, word);
  return `background:radial-gradient(circle at 34% 30%, #ffffffcc 0%, ${c} 26%, ${c} 58%, #00000066 100%);box-shadow:0 0 18px ${c}55, inset -4px -6px 10px #0005;`;
}
function renderTube(orbs) {
  $('#tubeHead').textContent = orbs.length ? `你的光球 · ${orbs.length >= 60 ? '最近 60' : orbs.length} 颗` : '记下第一刻，这里就会多一颗光球';
  $('#tube').innerHTML = orbs.map((o, i) => `<div class="orb" data-i="${i}" style="${orbStyle(WV[o.word] ?? 0, o.word)}"></div>`).join('');
  $('#tube').querySelectorAll('.orb').forEach(el => {
    const o = orbs[+el.dataset.i];
    tipOn(el, () => `<b>${o.date.slice(5)} ${(o.created_at || '').slice(11, 16)}　${esc(o.word)}</b>${o.note ? esc(o.note.slice(0, 120)) : ''}<em>点一下去那天</em>`);
    el.onclick = () => { $('#oracle').classList.add('hidden'); loadNow(o.date); window.scrollTo({ top: 0, behavior: 'smooth' }); };
  });
}

// ---------------------------------------------------------------- 这个月
async function loadMonth(m) {
  S.month = m;
  S.M = await fetch('/api/checkin/month?month=' + m).then(r => r.json());
  if (!S.selDay || S.selDay.slice(0, 7) !== m) S.selDay = m === S.today.slice(0, 7) ? S.today : null;
  renderMonth();
  syncUrl();
  const my = m;
  $('#mNote').textContent = S.M.note || (S.M.stars.length ? '' : '');
  if (S.M.stars.length) {       // 数据变了才真去调模型；没变就是缓存
    const n = await fetch('/api/stars/note?month=' + m, { method: 'POST' }).then(r => r.json());
    if (my === S.month) $('#mNote').textContent = n.note || '';
  }
}

function star4(c, hollow) {
  const d = 'M12 1.5 L14.2 9.8 L22.5 12 L14.2 14.2 L12 22.5 L9.8 14.2 L1.5 12 L9.8 9.8 Z';
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}" ${hollow ? `fill="none" stroke="${c}" stroke-width="1.6"` : `fill="${c}"`}/></svg>`;
}

function renderMonth() {
  const M = S.M, [yy, mm] = S.month.split('-').map(Number);
  $('#mTitle').textContent = `${yy} 年 ${mm} 月`;
  $('#mNext').disabled = S.month >= S.today.slice(0, 7);

  // 比例条：按好坏顺序排（兴奋 → 崩溃），颜色 = 好坏
  const comp = M.composition;
  $('#bar').innerHTML = comp.map(c => `<i style="flex:${c.n};background:${vcol(WV[c.word] ?? 0, c.word)}" title="${c.word} ${c.pct}%"></i>`).join('');
  $('#barLegend').innerHTML = comp.length
    ? comp.map(c => `<span><b style="background:${vcol(WV[c.word] ?? 0, c.word)}"></b>${c.word}<em>${c.pct}%</em></span>`).join('')
    : '<span style="color:var(--muted)">这个月还没有记录</span>';

  // 日历：周一开头，每天一颗四角星（实心 = 打卡，空心 = 从手写读的）
  const by = {}; M.stars.forEach(s => by[s.date] = s);
  const first = new Date(yy, mm - 1, 1), days = new Date(yy, mm, 0).getDate();
  const pad = (first.getDay() + 6) % 7;
  let cells = '一二三四五六日'.split('').map(w => `<div class="wd">${w}</div>`).join('') + '<div></div>'.repeat(pad);
  for (let d = 1; d <= days; d++) {
    const date = `${S.month}-${String(d).padStart(2, '0')}`, s = by[date];
    const future = date > S.today;
    cells += `<div class="c${date === S.today ? ' today' : ''}${date === S.selDay ? ' sel' : ''}${future ? ' future' : ''}" data-d="${date}">
      <span class="num">${d}</span>${s ? star4(vcol(s.v, s.word), s.src !== 'checkin') : '<svg viewBox="0 0 24 24"></svg>'}</div>`;
  }
  $('#cal').innerHTML = cells;
  $('#cal').querySelectorAll('.c[data-d]').forEach(el => {
    const s = by[el.dataset.d];
    if (s) tipOn(el, () => `<b>${s.date.slice(5)}　${esc(s.word)}</b>${s.note ? esc(s.note.slice(0, 120)) : ''}<em>${s.src === 'checkin' ? (s.n > 1 ? `打卡 ${s.n} 条` : '自己打卡') : '从手写日记读出来的'}</em>`);
    el.onclick = () => { if (el.classList.contains('future')) return; S.selDay = el.dataset.d; renderMonth(); };
  });

  renderDayBig(by[S.selDay]);
  renderStars();
}

function renderDayBig(s) {
  const box = $('#dayBig');
  if (!S.selDay) { box.innerHTML = ''; return; }
  const dt = new Date(S.selDay + 'T12:00:00');
  box.innerHTML = `<div class="d">${dt.getDate()}</div>
    <div class="meta"><div class="wk">周${WK[dt.getDay()]}</div>
      <div class="wd2">${s ? `<span style="color:${vcol(s.v, s.word)};font-weight:600">${esc(s.word)}</span>${s.note ? ' · ' + esc(s.note.slice(0, 40)) : ''}` : '还没记'}</div></div>
    <a class="go">${s && s.src === 'checkin' ? '看 / 改这天 →' : '给这天补一个 →'}</a>`;
  box.querySelector('.go').onclick = () => { setView('now'); loadNow(S.selDay); };
}

// 星座：一天一颗星，高度 = 过得怎么样，挨着的日子连成线
function renderStars() {
  const box = $('#stars'), stars = S.M.stars;
  const [yy, mm] = S.month.split('-').map(Number), days = new Date(yy, mm, 0).getDate();
  const W = Math.max(box.clientWidth, 280), H = 250, L = 30, R = 16, T = 20, B = 26;
  const x = d => L + (d - 1) / Math.max(days - 1, 1) * (W - L - R);
  const y = v => T + (2 - v) / 4 * (H - T - B);
  let seed = yy * 100 + mm; const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
  const dust = Array.from({ length: 80 }, () => `<circle cx="${(rnd() * W).toFixed(1)}" cy="${(rnd() * H).toFixed(1)}" r="${(rnd() * .9 + .3).toFixed(2)}" fill="#fff" opacity="${(rnd() * .3 + .1).toFixed(2)}"/>`).join('');
  const pts = stars.map(s => ({ ...s, d: +s.date.slice(8), cx: x(+s.date.slice(8)), cy: y(s.v) }));
  const lines = pts.slice(1).map((p, i) => p.d - pts[i].d === 1
    ? `<line x1="${pts[i].cx}" y1="${pts[i].cy}" x2="${p.cx}" y2="${p.cy}" stroke="#fff" stroke-opacity=".3"/>` : '').join('');
  const els = pts.map((p, i) => {
    const c = vcol(p.v, p.word), r = 3.2 + Math.abs(p.v) * 1.2 + Math.min(p.n - 1, 3) * .6;
    const sel = p.date === S.selDay ? `<circle cx="${p.cx}" cy="${p.cy}" r="${r + 5}" fill="none" stroke="${c}" stroke-opacity=".7"/>` : '';
    return `<g class="st" data-i="${i}" style="cursor:pointer"><circle cx="${p.cx}" cy="${p.cy}" r="${r * 3.2}" fill="${c}" opacity=".16"/>${sel}` +
      (p.src === 'checkin' ? `<circle cx="${p.cx}" cy="${p.cy}" r="${r}" fill="${c}"/>` : `<circle cx="${p.cx}" cy="${p.cy}" r="${r}" fill="none" stroke="${c}" stroke-width="1.4"/>`) +
      `<circle cx="${p.cx}" cy="${p.cy}" r="12" fill="transparent"/></g>`;
  }).join('');
  const ax = (t, yy2) => `<text x="${L - 8}" y="${yy2 + 4}" text-anchor="end" fill="#fff" fill-opacity=".4" font-size="10.5">${t}</text>`;
  const ticks = [1, 8, 15, 22, days].map(d => `<text x="${x(d)}" y="${H - 8}" text-anchor="middle" fill="#fff" fill-opacity=".4" font-size="10.5">${d}</text>`).join('');
  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${S.month} 每天过得怎么样">${dust}
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#fff" stroke-opacity=".12" stroke-dasharray="2 4"/>
    ${ax('好', y(2))}${ax('平', y(0))}${ax('糟', y(-2))}${lines}${els}${ticks}
    ${stars.length ? '' : `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="#fff" fill-opacity=".45" font-size="12.5">这个月还没有星星</text>`}</svg>`;
  box.querySelectorAll('.st').forEach(g => {
    const p = pts[+g.dataset.i];
    tipOn(g, () => `<b>${p.date.slice(5)}　${esc(p.word)}</b>${p.note ? esc(p.note.slice(0, 120)) : ''}<em>${p.src === 'checkin' ? '自己打卡' : '从手写日记读出来的'}</em>`);
    g.onclick = () => { S.selDay = p.date; renderMonth(); };
  });
}

const shiftMonth = n => { let [y, m] = S.month.split('-').map(Number); m += n;
  if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } loadMonth(`${y}-${String(m).padStart(2, '0')}`); };
$('#mPrev').onclick = () => shiftMonth(-1);
$('#mNext').onclick = () => { if (S.month < S.today.slice(0, 7)) shiftMonth(1); };
window.addEventListener('resize', () => { if (S.view === 'month' && S.M) renderStars(); });

// ---------------------------------------------------------------- 悬停提示
function tipOn(el, html) {
  const tip = $('#tip');
  el.addEventListener('mouseenter', () => { tip.innerHTML = html(); tip.classList.remove('hidden'); });
  el.addEventListener('mousemove', e => {
    let l = e.clientX + 14, t = e.clientY + 14;
    if (l + 290 > innerWidth) l = e.clientX - 294;
    if (t + tip.offsetHeight > innerHeight) t = e.clientY - tip.offsetHeight - 10;
    tip.style.left = l + 'px'; tip.style.top = t + 'px';
  });
  el.addEventListener('mouseleave', () => tip.classList.add('hidden'));
}

// ---------------------------------------------------------------- 启动
(async () => {
  const p = new URLSearchParams(location.search);
  await loadNow(p.get('date'));
  if (p.get('v') === 'month') { S.month = p.get('m'); setView('month'); }
  else $('#note').focus();
})();
