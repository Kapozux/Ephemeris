// 打卡页。两屏：此刻（记一下）/ 这个月（比例条 + 日历星 + 星座 + 一句话）。
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const WK = '日一二三四五六';

// 颜色只编码好坏：暖 = 好，冷 = 糟（越糟越深），灰 = 平。「说不上来」单独一个深灰。
// 是哪种情绪永远配文字，不靠颜色区分（焦虑、烦躁同分同色）。
// 色值在 checkin.css 的 :root（深浅两套），这里读出来是十六进制 —— 光球要拼透明度（${c}55），SVG 属性里也不认 var()
const CSSV = {};
const cssv = n => CSSV[n] ??= getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function vcol(v, word) {
  if (word === '说不上来') return cssv('--v-unsure');     // 一天几条时传的是 words（「兴奋→平稳→说不上来」），按平均分上色
  if (v >= 1.75) return cssv('--v-good2');
  if (v >= 1.25) return cssv('--v-good1');
  if (v >= 0.25) return cssv('--v-good0');
  if (v > -0.25) return cssv('--v-flat');
  if (v > -0.75) return cssv('--v-bad1');
  if (v > -1.25) return cssv('--v-bad2');
  if (v > -1.75) return cssv('--v-bad3');
  return cssv('--v-bad4');
}
const WV = {};   // 词 → 分

const S = { view: 'now', date: null, today: null, words: [], items: [], word: null, editing: null,
            month: null, M: null, selDay: null, oracleId: null };

// ---------------------------------------------------------------- 路由（?date= & v=month）
function syncUrl() {
  const p = new URLSearchParams();
  if (S.date && S.date !== S.today) p.set('date', S.date);
  if (S.view === 'month') { p.set('v', 'month'); if (S.month) p.set('m', S.month); }
  history.replaceState(null, '', '/checkin' + (p.toString() ? '?' + p : ''));
  const d = S.view === 'month' ? S.selDay : S.date;           // 左上角回主页：回到正在看的这一天
  $('.ck-back').href = d ? '/#/day/' + d : '/';
}

function setView(v) {
  S.view = v;
  document.querySelectorAll('.ck-tab').forEach(t => { t.classList.toggle('on', t.dataset.v === v); t.setAttribute('aria-selected', t.dataset.v === v); });
  $('#vNow').classList.toggle('hidden', v !== 'now');
  $('#vMonth').classList.toggle('hidden', v !== 'month');
  if (v === 'month') loadMonth(S.month || (S.date || S.today).slice(0, 7));
  syncUrl();
}
document.querySelectorAll('.ck-tab').forEach(t => t.onclick = () => setView(t.dataset.v));

// ---------------------------------------------------------------- 此刻
function dateLabel(d) {
  const diff = Math.round((Date.parse(S.today) - Date.parse(d)) / 864e5);
  const tag = diff === 0 ? '今天' : diff === 1 ? '昨天' : diff === 2 ? '前天' : `${diff} 天前`;
  const dt = new Date(d + 'T12:00:00');
  return `${tag} · ${dt.getMonth() + 1} 月 ${dt.getDate()} 日 周${WK[dt.getDay()]}`;
}

async function loadNow(date) {
  const r = await fetch('/api/checkin/home' + (date ? '?date=' + date : '')).then(r => r.json());
  // 换了一天：正在改的那条是原来那天的，退出编辑（以前不退，按「改好了」改的还是原来那天那条）。
  // 选的词和写了一半的备注留着，按钮上写清楚会记到哪天
  if (r.date !== S.date && S.editing) { S.editing = null; S.word = null; $('#note').value = ''; }
  if (r.date !== S.date) S.oracleId = null;
  Object.assign(S, { date: r.date, today: r.today, words: r.words, items: r.items });
  r.words.forEach(w => WV[w.word] = w.v);
  renderProgress(r.progress);
  S.orbs = r.orbs;
  renderTube(r.orbs);
  renderNow();
  syncUrl();
}

// 一共记了多少：日期行右边一行小字（以前是三张大统计卡，压在选词上面）
function renderProgress(p) {
  $('#prog').textContent = p.moments ? `记了 ${p.moments} 次 · ${p.days} 天${p.streak > 1 ? ` · 连着 ${p.streak} 天` : ''}` : '';
}

function renderNow() {
  const isToday = S.date === S.today;
  $('#dLabel').textContent = dateLabel(S.date);
  $('#dNext').disabled = S.date >= S.today;
  $('#dToday').classList.toggle('hidden', isToday);
  $('#hTitle').firstChild.textContent = isToday ? '此刻怎么样' : '那天怎么样';
  $('#tags').innerHTML = S.words.map(w =>
    `<button class="ck-tag${S.word === w.word ? ' on' : ''}" data-w="${w.word}" style="color:${vcol(w.v, w.word)}">${w.word}</button>`).join('');
  $('#tags').querySelectorAll('.ck-tag').forEach(b => b.onclick = () => { S.word = S.word === b.dataset.w ? null : b.dataset.w; renderNow(); });
  $('#record').disabled = !S.word;
  const dt = new Date(S.date + 'T12:00:00');
  $('#record').textContent = !S.word ? '先点一个词' : S.editing ? '改好了 →' : isToday ? '记下 →' : `记到 ${dt.getMonth() + 1} 月 ${dt.getDate()} 日 →`;
  $('#note').placeholder = isToday ? '此刻在发生什么……' : '那天发生了什么……';
  $('#cancelEdit').classList.toggle('hidden', !S.editing);

  $('#todayList').innerHTML = S.items.length
    ? `<h3>${isToday ? '今天' : '这天'}记的 · ${S.items.length} 条</h3>` + S.items.slice().reverse().map(x => `
      <div class="ck-item${S.editing === x.id ? ' editing' : ''}">
        <span class="ck-item-t">${(x.created_at || '').slice(11, 16)}</span>
        <span class="ck-item-w" style="color:${vcol(x.valence, x.word)}">${esc(x.word)}</span>
        <span class="ck-item-n">${esc(x.note || '')}</span>
        <span class="ck-item-ops"><button type="button" class="ck-link" data-e="${x.id}">改</button><button type="button" class="ck-link" data-d="${x.id}">删</button></span>
        ${x.reply && x.id !== S.oracleId ? `<div class="ck-item-r">${esc(x.reply)}</div>` : ''}
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
  const editing = S.editing, o = $('#oracle');
  let id = editing;
  $('#record').disabled = true;
  try {
    const r = editing ? await fetch('/api/checkin/' + id, { method: 'PUT', ...opt })
                      : await fetch('/api/checkin', { method: 'POST', ...opt });
    if (!r.ok) throw new Error(r.status);
    if (!editing) id = (await r.json()).id;
  } catch (e) {                                   // 没记上：选的词和备注都留着，说一句
    o.classList.remove('hidden', 'thinking'); $('#oText').textContent = '没记上（连不上 Ephemeris？），词和备注都还在，再按一次试试。';
    renderNow();
    return;
  }
  S.word = null; S.editing = null; $('#note').value = '';
  if (editing) { o.classList.add('hidden'); S.oracleId = null; loadNow(S.date); return; }   // 改的：原来的「它说」留着
  S.oracleId = id;
  await loadNow(S.date);
  // 它说：记完才去要，记下这个动作本身不等模型
  o.classList.remove('hidden'); o.classList.add('thinking'); $('#oText').textContent = '在想';
  const r = await fetch(`/api/checkin/${id}/reply`, { method: 'POST' }).then(r => r.json()).catch(() => ({}));
  o.classList.remove('thinking'); $('#oText').textContent = r.reply || '（这次没想出来说什么）';
  loadNow(S.date);
};
// ⌘↩ 记下；还没点词就让词那一行闪一下，告诉他缺什么
$('#note').addEventListener('keydown', e => {
  if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return;
  e.preventDefault();
  if (S.word) { $('#record').click(); return; }
  const t = $('#tags'); t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash');
});
$('#cancelEdit').onclick = () => { S.editing = null; S.word = null; $('#note').value = ''; renderNow(); };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && S.editing) { $('#cancelEdit').click(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const back = e.key === 'ArrowLeft';
  if (S.view === 'month') { if (back) $('#mPrev').click(); else $('#mNext').click(); }
  else if (back) $('#dPrev').click(); else $('#dNext').click();
});
const shiftDay = n => { const d = new Date(S.date + 'T12:00:00'); d.setDate(d.getDate() + n);
  $('#oracle').classList.add('hidden'); loadNow(d.toISOString().slice(0, 10)); };
$('#dPrev').onclick = () => shiftDay(-1);
$('#dNext').onclick = () => { if (S.date < S.today) shiftDay(1); };
$('#dToday').onclick = () => { $('#oracle').classList.add('hidden'); loadNow(); };

// 最近记的：一刻一颗，只摆最近 14 颗；更早的去「这个月」看
const TUBE_N = 14;
function orbStyle(v, word) {
  const c = vcol(v, word);
  return `background:radial-gradient(circle at 34% 30%, #ffffffcc 0%, ${c} 26%, ${c} 58%, #00000066 100%);box-shadow:0 0 18px ${c}55, inset -4px -6px 10px #0005;`;
}
function renderTube(all) {
  const orbs = all.slice(0, TUBE_N);
  $('#tubeHead').textContent = orbs.length ? `最近记的 ${orbs.length} 次` : '记下第一刻，这里就会亮一颗';
  $('#tubeMonth').classList.toggle('hidden', !orbs.length);
  $('#tube').innerHTML = orbs.map((o, i) => `<div class="ck-orb" data-i="${i}" style="${orbStyle(WV[o.word] ?? 0, o.word)}"></div>`).join('');
  $('#tube').querySelectorAll('.ck-orb').forEach(el => {
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
  renderNote();
}

// 星座图下面那一句：只显示存着的；没有就给个按钮，点了才调模型写（2026-10-14：以前一打开「这个月」就自动写，没点也花额度）
function renderNote(st = '') {
  const note = S.M.note || '';
  $('#mNote').textContent = note;
  $('#mNoteAsk').classList.toggle('hidden', !!note || !S.M.stars.length);
  const go = $('#mNoteGo'), busy = S.noteBusy === S.month;      // 翻走又翻回来：还在写就别让再点一次
  go.disabled = busy;
  go.textContent = busy ? '在写' : st ? '重试' : '写一句这个月';
  $('#mNoteSt').textContent = st;
}

$('#mNoteGo').onclick = async () => {
  const m = S.month, go = $('#mNoteGo');
  S.noteBusy = m; go.disabled = true; go.textContent = '在写'; $('#mNoteSt').textContent = '';
  let n;
  try {
    n = await fetch('/api/stars/note?month=' + m, { method: 'POST' }).then(r => r.json());
  } catch (e) {
    n = { error: '连不上 Ephemeris' };
  }
  S.noteBusy = null;
  if (m !== S.month) return;               // 写的时候翻到别的月了：那个月下次打开自己显示存着的
  if (n.note) { S.M.note = n.note; renderNote(); }
  else renderNote('没写成：' + (n.error || '不知道为什么'));
};

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
  let cells = '一二三四五六日'.split('').map(w => `<div class="ck-cal-wd">${w}</div>`).join('') + '<div></div>'.repeat(pad);
  for (let d = 1; d <= days; d++) {
    const date = `${S.month}-${String(d).padStart(2, '0')}`, s = by[date];
    const future = date > S.today;
    cells += `<div class="ck-cell${date === S.today ? ' today' : ''}${date === S.selDay ? ' sel' : ''}${future ? ' future' : ''}" data-d="${date}">
      <span class="ck-cell-num">${d}</span>${s ? star4(vcol(s.v, s.words || s.word), s.src !== 'checkin') : '<svg viewBox="0 0 24 24"></svg>'}</div>`;
  }
  $('#cal').innerHTML = cells;
  $('#cal').querySelectorAll('.ck-cell[data-d]').forEach(el => {
    const s = by[el.dataset.d];
    if (s) tipOn(el, () => `<b>${s.date.slice(5)}　${esc(s.words || s.word)}</b>${s.note ? esc(s.note.slice(0, 120)) : ''}<em>${s.src === 'checkin' ? (s.n > 1 ? `打卡 ${s.n} 条` : '自己打卡') : '从手写日记读出来的'}</em>`);
    el.onclick = () => { if (el.classList.contains('future')) return; S.selDay = el.dataset.d; renderMonth(); showDayBig(); };
  });

  renderDayBig(by[S.selDay]);
  renderStars();
  syncUrl();                                   // 选中的那天变了：左上角回主页跟着变
}

function renderDayBig(s) {
  const box = $('#dayBig');
  if (!S.selDay) { box.innerHTML = ''; return; }
  const dt = new Date(S.selDay + 'T12:00:00');
  box.innerHTML = `<div class="ck-big-d">${dt.getDate()}</div>
    <div class="ck-big-meta"><div class="ck-big-wk">周${WK[dt.getDay()]}</div>
      <div class="ck-big-wd">${s ? `<span style="color:${vcol(s.v, s.words || s.word)};font-weight:600">${esc(s.words || s.word)}</span>${s.note ? ' · ' + esc(s.note.slice(0, 40)) : ''}` : '还没记'}</div></div>
    <button type="button" class="ck-big-go">${s && s.src === 'checkin' ? '看 / 改这天 →' : S.selDay === S.today ? '记今天 →' : '给这天补一个 →'}</button>`;
  box.querySelector('.ck-big-go').onclick = () => { setView('now'); loadNow(S.selDay); };
}

// 点了一天：详情不在视野里（窄屏单栏时它在日历下面）就滚过去；宽屏两栏本来就看得见，不动
function showDayBig() {
  const b = $('#dayBig').getBoundingClientRect();
  if (b.top < 0 || b.bottom > innerHeight) $('#dayBig').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// 星座：一天一颗星，高度 = 过得怎么样，挨着的日子连成线
function renderStars() {
  const box = $('#stars'), stars = S.M.stars;
  const [yy, mm] = S.month.split('-').map(Number), days = new Date(yy, mm, 0).getDate();
  const W = Math.max(box.clientWidth, 280), H = 250, L = 30, R = 16, T = 20, B = 26;
  const x = d => L + (d - 1) / Math.max(days - 1, 1) * (W - L - R);
  const y = v => T + (2 - v) / 4 * (H - T - B);
  let seed = yy * 100 + mm; const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
  const ink = cssv('--star-ink');      // 深色是白线白字，浅色是墨色；星尘只在深色夜空里有
  const dust = +cssv('--star-dust') ? Array.from({ length: 80 }, () => `<circle cx="${(rnd() * W).toFixed(1)}" cy="${(rnd() * H).toFixed(1)}" r="${(rnd() * .9 + .3).toFixed(2)}" fill="${ink}" opacity="${(rnd() * .3 + .1).toFixed(2)}"/>`).join('') : '';
  const pts = stars.map(s => ({ ...s, d: +s.date.slice(8), cx: x(+s.date.slice(8)), cy: y(s.v) }));
  const lines = pts.slice(1).map((p, i) => p.d - pts[i].d === 1
    ? `<line x1="${pts[i].cx}" y1="${pts[i].cy}" x2="${p.cx}" y2="${p.cy}" stroke="${ink}" stroke-opacity=".3"/>` : '').join('');
  const els = pts.map((p, i) => {
    const c = vcol(p.v, p.words || p.word), r = 3.2 + Math.abs(p.v) * 1.2 + Math.min(p.n - 1, 3) * .6;
    const sel = p.date === S.selDay ? `<circle cx="${p.cx}" cy="${p.cy}" r="${r + 5}" fill="none" stroke="${c}" stroke-opacity=".7"/>` : '';
    return `<g class="ck-star" data-i="${i}" style="cursor:pointer"><circle cx="${p.cx}" cy="${p.cy}" r="${r * 3.2}" fill="${c}" opacity=".16" pointer-events="none"/>${sel}` +
      (p.src === 'checkin' ? `<circle cx="${p.cx}" cy="${p.cy}" r="${r}" fill="${c}"/>` : `<circle cx="${p.cx}" cy="${p.cy}" r="${r}" fill="none" stroke="${c}" stroke-width="1.4"/>`) +
      `<circle cx="${p.cx}" cy="${p.cy}" r="12" fill="transparent"/></g>`;
  }).join('');
  const ax = (t, yy2) => `<text x="${L - 8}" y="${yy2 + 4}" text-anchor="end" fill="${ink}" fill-opacity=".4" font-size="10.5">${t}</text>`;
  const ticks = [1, 8, 15, 22, days].map(d => `<text x="${x(d)}" y="${H - 8}" text-anchor="middle" fill="${ink}" fill-opacity=".4" font-size="10.5">${d}</text>`).join('');
  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${S.month} 每天过得怎么样">${dust}
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="${ink}" stroke-opacity=".12" stroke-dasharray="2 4"/>
    ${ax('好', y(2))}${ax('平', y(0))}${ax('糟', y(-2))}${lines}${els}${ticks}
    ${stars.length ? '' : `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${ink}" fill-opacity=".45" font-size="12.5">这个月还没有星星</text>`}</svg>`;
  box.querySelectorAll('.ck-star').forEach(g => {
    const p = pts[+g.dataset.i];
    tipOn(g, () => `<b>${p.date.slice(5)}　${esc(p.words || p.word)}</b>${p.note ? esc(p.note.slice(0, 120)) : ''}<em>${p.src === 'checkin' ? '自己打卡' : '从手写日记读出来的'}</em>`);
    g.onclick = () => { S.selDay = p.date; renderMonth(); showDayBig(); };
  });
}

$('#tubeMonth').onclick = () => { setView('month'); window.scrollTo({ top: 0 }); };

const shiftMonth = n => { let [y, m] = S.month.split('-').map(Number); m += n;
  if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } loadMonth(`${y}-${String(m).padStart(2, '0')}`); };
$('#mPrev').onclick = () => shiftMonth(-1);
$('#mNext').onclick = () => { if (S.month < S.today.slice(0, 7)) shiftMonth(1); };
window.addEventListener('resize', () => { if (S.view === 'month' && S.M) renderStars(); });
// 系统换了明暗：颜色重新读，画过的重画（不重新请求，「这个月」那一句不会因此重写）
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  for (const k in CSSV) delete CSSV[k];
  if (!S.date) return;
  renderNow(); renderTube(S.orbs || []);
  if (S.M) renderMonth();
});

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
  // 不自动聚焦备注：先选词，手机上也不会一打开就弹键盘
})();
