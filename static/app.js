const $ = s => document.querySelector(s);
const esc = s => (s || '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const CLIP = 300;   // 长消息先折到这么多字

let DAYS = [], BYDATE = {}, SEL = null, NAV = { prev: null, next: null };
let MODE = 'day', LATEST = null;                   // day | win
let POLL = null, BATCHPOLL = null;

function rel(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}

// ---------------------------------------------------------------- 日历
// 颜色编码的是「窗口数」，不是字数：字数最高的天往往是复制粘贴日，没信息量。

function heat(threads) {
  if (!threads) return 'var(--h0)';
  return threads < 2 ? 'var(--h1)' : threads < 5 ? 'var(--h2)' : threads < 10 ? 'var(--h3)' : 'var(--h4)';
}

// 日历颜色三档：活跃（窗口数）/ 过得（手写的 -2..+2）/ 卡得（聊天的 0..4）
let CMODE = 'act';
try { CMODE = localStorage.getItem('cmode') || 'act'; } catch (e) { /* 无痕模式 */ }
const FEEL = { '-2': 'var(--f-2)', '-1.5': 'var(--f-2)', '-1': 'var(--f-1)', '-0.5': 'var(--f-1)', '0': 'var(--f0)',
               '0.5': 'var(--f1)', '1': 'var(--f1)', '1.5': 'var(--f2)', '2': 'var(--f2)' };
// 打卡是多条取平均，可能不是 .5 的整数倍：就近落到五档色上
function feelColor(v) { const b = v <= -1.25 ? -2 : v <= -0.25 ? -1 : v < 0.25 ? 0 : v < 1.25 ? 1 : 2; return `var(--f${b})`; }
function cellColor(info) {
  if (CMODE === 'feel') return info.feel == null ? 'transparent' : feelColor(info.feel);
  if (CMODE === 'stuck') return info.stuck == null ? 'transparent' : `var(--s${info.stuck})`;
  return heat(info.threads);
}
function renderLegend() {
  const sw = c => `<i class="sw" style="background:${c}"></i>`;
  $('#legend').innerHTML = CMODE === 'feel'
    ? `<span>糟</span>${['--f-2', '--f-1', '--f0', '--f1', '--f2'].map(v => sw(`var(${v})`)).join('')}<span>好</span><span class="sp"></span><i class="sw empty"></i><span>没手写</span>`
    : CMODE === 'stuck'
    ? `<span>顺</span>${[0, 1, 2, 3, 4].map(v => sw(`var(--s${v})`)).join('')}<span>崩</span><span class="sp"></span><i class="sw empty"></i><span>没聊天</span>`
    : `<span>少</span>${[1, 2, 3, 4].map(v => sw(`var(--h${v})`)).join('')}<span>多</span><span class="sp"></span><i class="sw jr"></i><span>手写</span><i class="sw faded"></i><span>还没日记</span>`;
}
document.querySelectorAll('.cm-item').forEach(b => {
  b.classList.toggle('active', b.dataset.c === CMODE);
  b.onclick = () => {
    CMODE = b.dataset.c;
    try { localStorage.setItem('cmode', CMODE); } catch (e) { /* 无痕模式 */ }
    document.querySelectorAll('.cm-item').forEach(x => x.classList.toggle('active', x === b));
    buildCalendar(); if (SEL) document.querySelector(`.day[data-d="${SEL}"]`)?.classList.add('sel');
  };
});

function buildCalendar() {
  renderLegend();
  const months = [];
  DAYS.forEach(d => {
    const ym = d.date.slice(0, 7);
    if (!months.length || months[months.length - 1] !== ym) months.push(ym);
  });
  $('#cal').innerHTML = months.map(ym => {
    const [y, mo] = ym.split('-').map(Number);
    const pad = (new Date(y, mo - 1, 1).getDay() + 6) % 7;   // 周一开头
    const last = new Date(y, mo, 0).getDate();
    let cells = '<i class="day"></i>'.repeat(pad);
    for (let d = 1; d <= last; d++) {
      const date = `${ym}-${String(d).padStart(2, '0')}`;
      const info = BYDATE[date];
      if (!info) { cells += '<i class="day"></i>'; continue; }
      const onlyJ = !info.threads && info.journal;
      const tip = onlyJ ? `${date}　只有手写日记 · ${info.journal.toLocaleString()} 字`
        : `${date}　${info.threads} 个窗口 · ${info.msgs} 条` +
          (info.journal ? ` · 手写 ${info.journal.toLocaleString()} 字` : '') +
          (info.headline ? `\n${info.headline}` : '\n（还没有日记）');
      const act = CMODE === 'act';
      const cls = (!act || info.has_diary || onlyJ ? '' : 'nodiary') + (act && info.journal ? ' jr' : '') +
                  (cellColor(info) === 'transparent' ? ' blank' : '');
      const sig = (info.feel != null ? `\n过得 ${info.feel > 0 ? '+' : ''}${info.feel}` : '') +
                  (info.stuck != null ? `　卡得 ${info.stuck}/4` : '');
      cells += `<i class="day on ${cls}" data-d="${date}"
                   style="background:${cellColor(info)}" title="${esc(tip + sig)}"></i>`;
    }
    return `<div class="mon" id="m-${ym}"><h4>${ym.slice(2).replace('-', '.')}</h4>
              <div class="grid">${cells}</div></div>`;
  }).join('');
  $('#cal').onclick = e => { const c = e.target.closest('.day.on'); if (c) navigate('day/' + c.dataset.d); };
}

function markDiary(date) {
  const i = BYDATE[date]; if (i) i.has_diary = true;
  document.querySelector(`.day[data-d="${date}"]`)?.classList.remove('nodiary');
  refreshBatchButton();
}

// 两个指标：过得怎么样（手写）、卡得多狠（聊天）。分开放，不合成一个数 —— 它们量的是一天里不同的部分
function renderSignals(sg) {
  const f = sg.mood, k = sg.friction, out = [];
  if (f) out.push(`<span class="sig" title="${esc(f.evidence || '')}"><i style="background:${feelColor(f.valence)}"></i>过得 <b>${f.valence > 0 ? '+' : ''}${f.valence}</b> ${esc(f.label || '')}<em>${f.src === 'checkin' ? '打卡' : '手写'}</em></span>`);
  if (k) out.push(`<span class="sig" title="${esc(k.evidence || '')}"><i style="background:var(--s${k.valence})"></i>卡得 <b>${k.valence}/4</b> ${esc(k.label || '')}<em>聊天</em></span>`);
  out.push(`<a class="sig sig-add" href="/checkin?date=${SEL}">${(sg.checkins || []).length ? '改打卡' : '补打卡'}</a>`);
  $('#daySig').innerHTML = out.join('');
}

// ---------------------------------------------------------------- 手写日记
// 我自己在 Notion 里写的，放在 AI 日记上面。只读；长的先收起来。

const J_FOLD = 700;

function renderJournal(list, open) {
  const box = $('#journal');
  const pages = list.filter(j => (j.text || '').trim());
  if (!pages.length) { box.className = 'journal hidden'; box.innerHTML = ''; return; }
  const para = t => t.split('\n').map(l => {
    const h = l.match(/^(#{1,3}) (.*)$/);
    if (h) return `<h4>${esc(h[2])}</h4>`;
    return l.trim() ? `<p>${esc(l)}</p>` : '';
  }).join('');
  box.className = 'journal card-shell';
  box.innerHTML = `<div class="j-head"><span class="j-tag">我当天写的</span>
      <span class="dim tnum">${pages.reduce((a, j) => a + j.chars, 0).toLocaleString()} 字</span>
      <span class="sp"></span>${pages.map(j => `<a href="${j.url}" target="_blank" rel="noopener">在 Notion 打开</a>`).join(' · ')}</div>` +
    pages.map((j, i) => {
      const long = j.text.length > J_FOLD && !open;
      return `<div class="j-body${long ? ' folded' : ''}" data-i="${i}">${para(j.text)}</div>` +
             (long ? `<a class="j-more" data-i="${i}">展开全文</a>` : '');
    }).join('');
  box.querySelectorAll('.j-more').forEach(a => a.onclick = () => {
    box.querySelector(`.j-body[data-i="${a.dataset.i}"]`).classList.remove('folded'); a.remove();
  });
  if (open) box.scrollIntoView({ block: 'start' });
}

// ---------------------------------------------------------------- 日记

function renderDiary(r) {
  const box = $('#diary');
  box.className = 'diary';
  if (!r.threads.length && !r.diary) { box.className = 'hidden'; box.innerHTML = ''; return; }
  if (r.generating) {
    box.classList.add('gen');
    box.innerHTML = `<span class="pulse"></span>正在读这天的 ${r.threads.length} 个窗口，写日记…`;
    return;
  }
  const d = r.diary;
  if (!d) {
    box.classList.add('empty');
    box.innerHTML = `<p>这天还没有日记。${r.threads.length} 个窗口、${(BYDATE[SEL]?.chars || 0).toLocaleString()} 字，压成 300 字大概要 20 秒。</p>
                     <button class="pri" id="genBtn">生成这天的日记</button>`;
    $('#genBtn').onclick = generateCurrent;
    return;
  }
  const list = (t, arr) => (arr && arr.length)
    ? `<div><h5>${t}</h5><ul>${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '';
  // 这天接着哪几条线：第几天、隔了多久。点进去看那条线的全程
  const chips = (r.themes || []).map(t => {
    const extra = t.nth === 1 ? '新' : `第 ${t.nth} 天${t.gap > 7 ? ` · 隔 ${t.gap} 天` : ''}`;
    return `<a class="tchip${t.nth === 1 ? ' new' : ''}${t.gap > 7 ? ' back' : ''}" data-theme="${t.id}" title="${esc(t.note || '')}">${esc(t.name)}<em>${extra}</em></a>`;
  }).join('');
  box.innerHTML = `
    <h2>${esc(d.headline || '')}</h2>
    ${chips ? `<div class="tchips">${chips}</div>` : ''}
    ${(d.narrative || '').split('\n\n').filter(Boolean).map(p => `<p>${esc(p)}</p>`).join('')}
    <div class="lists">${list('做出来的', d.artifacts)}${list('没做完的', d.open_loops)}</div>
    <div class="foot">
      <span class="topics">${(d.topics || []).map(esc).join(' · ')}</span>
      ${d.mood ? `<span>${esc(d.mood)}</span>` : ''}
      <span class="sp"></span>
      <span title="${esc(d.generated_at || '')}">${rel(d.generated_at)}</span>
      <a id="genBtn">重新生成</a>
    </div>`;
  $('#genBtn').onclick = generateCurrent;
  box.querySelectorAll('[data-theme]').forEach(a => a.onclick = () => navigate('panel/themes/' + a.dataset.theme));
}

async function generateCurrent() {
  if (!SEL) return;
  await fetch(`/api/diary/${SEL}`, { method: 'POST' });
  renderDiary({ generating: true, threads: { length: BYDATE[SEL]?.threads || 0 } });
  poll(SEL);
}

function poll(date) {
  clearInterval(POLL);
  POLL = setInterval(async () => {
    const r = await fetch(`/api/day/${date}`).then(r => r.json());
    if (!r.generating) {
      clearInterval(POLL);
      if (r.diary) markDiary(date);
      if (SEL === date) openDay(date);
    }
  }, 2500);
}

// ---------------------------------------------------------------- 窗口列表

function msgHtml(m, target) {
  const long = m.text.length > CLIP;
  const body = long
    ? `${esc(m.text.slice(0, CLIP))}<span class="clip">…</span><a class="more" data-full="1">展开 ${m.text.length} 字</a><span class="full hidden">${esc(m.text.slice(CLIP))}</span>`
    : esc(m.text);
  return `<div class="msg ${m.role === 'human' ? '' : 'ai'} ${m.id === target ? 'target' : ''}" id="msg-${m.id}">
            <span class="t">${m.hhmm}</span><span class="x">${body}</span></div>`;
}

function renderThreads(threads, focus) {
  $('#threadCount').textContent = `${threads.length}`;
  if (!threads.length) { $('#threads').innerHTML = '<div class="empty-note">这天没有和 AI 聊天</div>'; return; }
  $('#threads').innerHTML = threads.map((t, i) => {
    const open = focus && t.conv_id === focus.conv;
    return `<div class="thread ${open ? 'hl' : ''}" data-conv="${t.conv_id}">
      <div class="thead" data-i="${i}">
        <span class="ts">${t.first}</span>
        <span class="src ${t.source}"></span>
        <span class="tt">${esc(t.title)}</span>
        <span class="right">${t.multi_day ? `<span class="span" data-conv="${t.conv_id}" title="这个窗口横跨了 ${t.span_from} → ${t.span_to}">${t.span_from.slice(5)} → ${t.span_to.slice(5)}</span>` : ''}<span class="cnt">${t.n_msgs} 条 · ${(t.chars || 0).toLocaleString()} 字</span></span>
      </div>
      <div class="tbody ${open ? '' : 'hidden'}" id="tb${i}">
        <div class="tbar"><label><input type="checkbox" class="aitog"> 显示 AI 回复</label></div>
        ${t.messages.map(m => msgHtml(m, focus?.msg)).join('')}
      </div></div>`;
  }).join('');

  $('#threads').onclick = e => {
    const sp = e.target.closest('.span');
    if (sp) { e.stopPropagation(); showSpan(sp.dataset.conv, sp); return; }
    const more = e.target.closest('.more');
    if (more) {
      const x = more.parentElement;
      x.querySelector('.full').classList.remove('hidden');
      x.querySelector('.clip').remove(); more.remove(); return;
    }
    const tog = e.target.closest('.aitog');
    if (tog) { tog.closest('.tbody').classList.toggle('showai', tog.checked); return; }
    const h = e.target.closest('.thead');
    if (h) $('#tb' + h.dataset.i).classList.toggle('hidden');
  };

  if (focus?.msg) {
    const el = document.getElementById('msg-' + focus.msg);
    if (el) setTimeout(() => el.scrollIntoView({ block: 'center', behavior: 'smooth' }), 60);
  }
}

// 一个窗口横跨的天 —— 这是这个东西和「回 Claude 翻记录」的区别
async function showSpan(convId, anchor) {
  const r = await fetch(`/api/conversation/${convId}`).then(r => r.json());
  const c = r.conversation;
  const pop = $('#pop');
  pop.innerHTML = `
    <h6>${esc(c.title)}</h6>
    <div class="sub">${c.first_ts.slice(0, 10)} → ${c.last_ts.slice(0, 10)} · 活跃 ${r.days.length} 天 · ${c.n_msgs} 条</div>
    <div class="days">${r.days.map(d =>
      `<a data-d="${d.date}" class="${d.date === SEL ? 'cur' : ''}" title="${d.msgs} 条 · ${d.chars.toLocaleString()} 字">${d.date.slice(5)}</a>`).join('')}</div>`;
  const b = anchor.getBoundingClientRect();
  pop.style.left = Math.min(b.left, window.innerWidth - 360) + 'px';
  pop.style.top = (b.bottom + 6) + 'px';
  pop.classList.remove('hidden');
  pop.onclick = e => {
    const a = e.target.closest('a[data-d]');
    if (a) { pop.classList.add('hidden'); navigate(`day/${a.dataset.d}/conv/${convId}`); }
  };
}
document.addEventListener('click', e => {
  if (!e.target.closest('#pop') && !e.target.closest('.span')) $('#pop').classList.add('hidden');
});

// ---------------------------------------------------------------- 一天

async function openDay(date, focus) {
  SEL = date;
  document.querySelectorAll('.day.sel').forEach(c => c.classList.remove('sel'));
  const c = document.querySelector(`.day[data-d="${date}"]`);
  if (c) { c.classList.add('sel'); c.scrollIntoView({ block: 'nearest' }); }

  const r = await fetch(`/api/day/${date}`).then(r => r.json());
  if (SEL !== date) return;            // 用户已经点了别的
  $('#day').classList.remove('hidden');
  NAV = { prev: r.prev, next: r.next };
  $('#prevBtn').disabled = !r.prev;
  $('#nextBtn').disabled = !r.next;

  const info = BYDATE[date] || {};
  const wd = '日一二三四五六'[new Date(date + 'T12:00:00').getDay()];
  $('#dayTitle').innerHTML = `<span class="num">${date}</span><span class="wd">周${wd}</span>`;
  const jChars = (r.journal || []).reduce((a, j) => a + (j.chars || 0), 0);
  $('#dayMeta').textContent = r.threads.length
    ? `${r.threads.length} 个对话窗口 · ${info.msgs || 0} 条 · ${(info.chars || 0).toLocaleString()} 字 · ${info.first_at || ''}–${info.last_at || ''}` +
      (jChars ? ` · 手写 ${jChars.toLocaleString()} 字` : '')
    : `这天没有 AI 聊天，只有手写日记 · ${jChars.toLocaleString()} 字`;

  renderSignals(r.signals || {});
  renderJournal(r.journal || [], focus && focus.journal);
  renderDiary(r);
  renderThreads(r.threads, focus);
  requestAnimationFrame(() => {
    const card = document.querySelector('.list-card');
    if (card) card.style.setProperty('--list-top', card.getBoundingClientRect().top + 'px');
  });
  if (!focus) $('#main').scrollTop = 0;
  if (r.generating) poll(date);
}

$('#prevBtn').onclick = () => NAV.prev && navigate('day/' + NAV.prev);
$('#nextBtn').onclick = () => NAV.next && navigate('day/' + NAV.next);
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowLeft' && NAV.prev) navigate('day/' + NAV.prev);
  if (e.key === 'ArrowRight' && NAV.next) navigate('day/' + NAV.next);
});

// ---------------------------------------------------------------- 搜索

let tmr = null;
const closeResults = () => $('#results').classList.add('hidden');
$('#q').addEventListener('input', e => {
  clearTimeout(tmr);
  const kw = e.target.value.trim();
  if (kw.length < 2) { closeResults(); return; }
  tmr = setTimeout(async () => {
    const r = await fetch('/api/search?q=' + encodeURIComponent(kw)).then(r => r.json());
    const box = $('#results');
    box.classList.remove('hidden');
    box.innerHTML = `<div class="rhead"><span>${r.hits.length} 条${r.hits.length >= 100 ? '（只显示前 100）' : ''}</span><a id="rclose">关闭</a></div>` +
      (r.hits.length
        ? r.hits.map(h => h.kind === 'journal'
            ? `<div class="hit jhit" data-d="${h.date}" data-j="1"><b>${h.date}　手写日记</b>${h.snip}</div>`
            : `<div class="hit" data-d="${h.date}" data-c="${h.conv_id}" data-m="${h.msg_id}">
             <b>${h.date} ${h.hhmm}　${esc(h.conv_title || '')}</b>${h.snip}</div>`).join('')
        : '<div class="hit"><b>没找到</b></div>');
    box.onclick = ev => {
      if (ev.target.id === 'rclose') { closeResults(); return; }
      const el = ev.target.closest('.hit');
      if (el?.dataset.j) { closeResults(); navigate(`day/${el.dataset.d}/journal`); return; }
      if (el?.dataset.d) { closeResults(); navigate(`day/${el.dataset.d}/conv/${el.dataset.c}/msg/${el.dataset.m}`); }
    };
  }, 240);
});
$('#q').addEventListener('keydown', e => { if (e.key === 'Escape') { e.target.value = ''; closeResults(); } });

// ---------------------------------------------------------------- 批量补全

function refreshBatchButton() {
  const missing = DAYS.filter(d => !d.has_diary && (d.chars || 0) >= 800).length;
  const box = $('#batch');
  box.dataset.want = missing ? '1' : '';          // 「按窗口」模式下要藏起来，但别忘了本来该不该显示
  if (!missing) { box.classList.add('hidden'); return; }
  if (MODE === 'day') box.classList.remove('hidden');
  $('#batchBtn').textContent = `补全 ${missing} 天的日记（约 $${(missing * 0.01).toFixed(2)}）`;
}

async function pollBatch() {
  refreshCkDot();
  const s = await fetch('/api/diary/batch').then(r => r.json());
  const bar = $('#batchBar');
  if (s.running) {
    bar.classList.remove('hidden');
    const p = s.total ? ((s.done + s.failed) / s.total * 100) : 0;
    bar.querySelector('i').style.width = p + '%';
    bar.querySelector('span').textContent = `${s.done + s.failed}/${s.total}　${s.current || ''}`;
    $('#batchBtn').textContent = '停止';
    $('#batchBtn').dataset.stop = '1';
    if (s.current && BYDATE[s.current]) { /* 当前那天完成后会由 markDiary 标记 */ }
    return;
  }
  clearInterval(BATCHPOLL); BATCHPOLL = null;
  bar.classList.add('hidden');
  delete $('#batchBtn').dataset.stop;
  // 跑完重新拉一次日历，把新标记全部落上
  const d = await fetch('/api/days').then(r => r.json());
  DAYS = d.days; BYDATE = {}; DAYS.forEach(x => BYDATE[x.date] = x);
  buildCalendar(); refreshBatchButton();
  if (SEL) openDay(SEL);
}

$('#batchBtn').onclick = async () => {
  if ($('#batchBtn').dataset.stop) { await fetch('/api/diary/batch', { method: 'DELETE' }); return; }
  const missing = DAYS.filter(d => !d.has_diary && (d.chars || 0) >= 800).length;
  if (!confirm(`要给 ${missing} 天各生成一篇日记，顺序跑，大约 ${Math.ceil(missing * 20 / 60)} 分钟、$${(missing * 0.01).toFixed(2)}。开始？`)) return;
  await fetch('/api/diary/batch', { method: 'POST' });
  BATCHPOLL = setInterval(pollBatch, 3000); pollBatch();
};

// ---------------------------------------------------------------- 启动

(async function init() {
  const d = await fetch('/api/days').then(r => r.json());
  DAYS = d.days;
  DAYS.forEach(x => BYDATE[x.date] = x);
  buildCalendar();
  refreshBatchButton();
  const s = await fetch('/api/diary/batch').then(r => r.json());
  if (s.running) { BATCHPOLL = setInterval(pollBatch, 3000); pollBatch(); }
  // 冷启动交给路由：hash 里有什么就恢复什么，没有就落到最近一天
  LATEST = d.latest;
  window.addEventListener('hashchange', applyRoute);
  applyRoute();
})();


// ================================================================ 回顾 / 数据 弹窗
const SHADES = ['#A9502B', '#C4764F', '#D99A78', '#E9BFA6', '#B7AEA0', '#D3CEC4', '#E5E1D8', '#EFECE5', '#F4F2ED'];
const WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
let RDATA = null, RMETRIC = 'msgs', RPOLL = null, RREQ = 0;

function niceCeil(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const k of [1, 2, 3, 4, 5, 6, 8, 10]) if (k * p >= v) return k * p;
  return 10 * p;
}
function monotonePath(xs, ys) {
  const n = xs.length; if (n < 2) return '';
  const d = [], m = [];
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = (d[i - 1] * d[i] <= 0) ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], h = Math.hypot(a, b);
    if (h > 3) { m[i] = 3 * a / h * d[i]; m[i + 1] = 3 * b / h * d[i]; }
  }
  let p = `M${xs[0].toFixed(1)},${ys[0].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const dx = (xs[i + 1] - xs[i]) / 3;
    p += ` C${(xs[i] + dx).toFixed(1)},${(ys[i] + m[i] * dx).toFixed(1)} ${(xs[i + 1] - dx).toFixed(1)},${(ys[i + 1] - m[i + 1] * dx).toFixed(1)} ${xs[i + 1].toFixed(1)},${ys[i + 1].toFixed(1)}`;
  }
  return p;
}
const fmtW = v => v >= 10000 ? (v / 10000).toFixed(v >= 100000 ? 0 : 1).replace(/\.0$/, '') + '万' : Math.round(v);
const rDate = iso => { const [y, m, d] = iso.split('-').map(Number); return `${m}月${d}日`; };

function reflectChart(series, prev, metric) {
  const W = 900, H = 250, L = 46, R = 12, T = 18, B = 34;
  const cur = series.map(p => p[metric]); const pv = prev.map(p => p[metric]);
  if (cur.length < 2) return '<div class="reflect-empty">数据不够画图</div>';
  const maxV = niceCeil(Math.max(...cur, ...pv, 1));
  const X = i => L + i / (cur.length - 1) * (W - L - R), Y = v => H - B - v / maxV * (H - T - B);
  const xs = cur.map((_, i) => X(i)), pxs = pv.map((_, i) => X(i + (cur.length - pv.length)));
  const grid = [maxV, maxV * 0.6].map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="ylab" x="${L - 12}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${fmtW(v)}</text>`).join('');
  const base = `<line class="base" x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}"/><text class="ylab" x="${L - 12}" y="${Y(0) + 4}" text-anchor="end">0</text>`;
  const n = cur.length - 1, idxs = [0, Math.round(n / 3), Math.round(n * 2 / 3), n];
  const xl = idxs.map((i, k) => `<text class="xlab" x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="${k === 0 ? 'start' : k === 3 ? 'end' : 'middle'}">${rDate(series[i].date)}</text>`).join('');
  const pp = pv.length >= 2 ? `<path class="prev" d="${monotonePath(pxs, pv.map(Y))}"/>` : '';
  return `<svg class="reflect-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${base}${xl}${pp}<path class="cur" d="${monotonePath(xs, cur.map(Y))}"/></svg>`;
}

function delta(cur, prev) {
  if (!prev) return '';
  const p = Math.round((cur - prev) / prev * 100);
  if (!p) return '<small>持平</small>';
  return `<small class="${p > 0 ? 'up' : 'down'}">${p > 0 ? '+' : ''}${p}%</small>`;
}

function renderReflect() {
  const d = RDATA, box = $('#reflectBody'); if (!d) return;
  const t = d.totals;
  if (!t.msgs) { box.innerHTML = '<div class="reflect-empty">这段时间没有聊天记录。</div>'; return; }
  const noDiary = !t.diaries;
  const headline = noDiary ? '这段时间的日记还没生成' : (d.generated ? d.headline : (d.regenerating ? '正在写这段时间的回顾…' : ''));
  const narrative = noDiary ? '先在侧栏点「补全日记」，回顾是基于日记写的。' : (d.generated ? d.narrative : '');
  const topics = d.topics || [];
  const bar = topics.map((x, i) => `<i style="flex:${x.percent};background:${SHADES[Math.min(i, SHADES.length - 1)]}"></i>`).join('');
  const list = topics.map((x, i) => `<div class="reflect-topic"><span class="dot" style="background:${SHADES[Math.min(i, SHADES.length - 1)]}"></span><span class="name">${esc(x.name)}</span><span class="pct">${x.percent}%</span><div class="desc">${esc(x.desc || `出现在 ${x.count} 天的日记里`)}</div></div>`).join('');
  box.innerHTML = `
    ${d.regenerating ? '<div class="reflect-updating"><i></i>正在重写这段时间的回顾，先给你看上一版</div>' : ''}
    <div class="reflect-headline">${esc(headline)}</div>
    ${narrative ? `<p class="reflect-narrative${d.generated ? '' : ' draft'}">${esc(narrative)}</p>` : ''}
    <div class="reflect-kpis">
      <div class="reflect-kpi"><b class="text">${d.most_active_weekday == null ? '—' : WD[d.most_active_weekday]}</b><span>最常聊的一天</span></div>
      <div class="reflect-kpi"><b>${d.peak_hour == null ? '—' : d.peak_hour + ':00'}</b><span>最活跃的钟点</span></div>
      <div class="reflect-kpi"><b>${t.days}</b><span>有聊天的天数</span></div>
      <div class="reflect-kpi"><b>${t.msgs.toLocaleString()}${delta(t.msgs, t.prev_msgs)}</b><span>我发的消息</span></div>
      <div class="reflect-kpi"><b>${fmtW(t.chars)}${delta(t.chars, t.prev_chars)}</b><span>我打的字</span></div>
      <div class="reflect-kpi"><b>${t.multi_window_days}<small>/ ${t.days} 天</small></b><span>同一天跨多个窗口</span></div>
    </div>
    <div class="reflect-sec">
      <div class="reflect-sec-head"><span class="reflect-sec-label">每天的量</span>
        <span class="reflect-seg">${['msgs', 'chars', 'threads'].map(k => `<button type="button" data-m="${k}" class="${RMETRIC === k ? 'active' : ''}">${{ msgs: '消息', chars: '字数', threads: '窗口' }[k]}</button>`).join('')}</span></div>
      <div id="rchart">${reflectChart(d.series, d.prev_series || [], RMETRIC)}</div>
      <div class="reflect-legend"><span><i></i>这段时间</span>${(d.prev_series || []).length ? '<span><i class="prev"></i>上一段同长</span>' : ''}</div>
    </div>
    ${topics.length ? `<div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">在忙什么</span><span class="reflect-sub">按日记里的话题标签算</span></div><div class="reflect-bar">${bar}</div><div class="reflect-topics">${list}</div></div>` : ''}`;
  box.querySelectorAll('.reflect-seg button').forEach(b => b.onclick = () => {
    RMETRIC = b.dataset.m;
    box.querySelectorAll('.reflect-seg button').forEach(x => x.classList.toggle('active', x === b));
    $('#rchart').innerHTML = reflectChart(d.series, d.prev_series || [], RMETRIC);
  });
}

function renderData() {
  const d = RDATA, box = $('#dataBody'); if (!d) return;
  const t = d.totals;
  const hmax = Math.max(...d.hour_counts, 1), wmax = Math.max(...d.weekday_counts, 1);
  const hours = d.hour_counts.map((v, h) => `<i style="height:${Math.max(2, v / hmax * 100)}%" class="${v < hmax * .25 ? 'lo' : ''}" title="${h}:00  ${v} 条"></i>`).join('');
  const hx = d.hour_counts.map((_, h) => `<span>${h % 3 === 0 ? h : ''}</span>`).join('');
  const wds = d.weekday_counts.map((v, i) => `<i style="height:${Math.max(2, v / wmax * 100)}%" title="${WD[i]} ${v} 条"></i>`).join('');
  const g = t.gemini_chars || 0, c = t.chars - g;
  const moods = (d.moods || []).map(([m, n]) => `<span class="pill">${esc(m)}<b>${n}</b></span>`).join('') || '<span class="reflect-sub">还没有日记</span>';
  const longs = (d.long_conversations || []).map(x => `<div class="row"><span class="tt"><a data-open="${x.b}" data-conv="${x.id}">${esc(x.title)}</a></span><span class="m">${x.a.slice(5)} → ${x.b.slice(5)}</span><span class="m">${x.days} 天 · ${x.msgs} 条</span></div>`).join('') || '<div class="reflect-sub">这段时间没有跨天的窗口</div>';
  const busy = (d.busiest || []).map(x => `<div class="row"><span class="tt"><a data-open="${x.date}">${x.date}${BYDATE[x.date]?.headline ? '　' + esc(BYDATE[x.date].headline) : ''}</a></span><span class="m">${x.threads} 个窗口</span><span class="m">${fmtW(x.chars)} 字</span></div>`).join('');
  box.innerHTML = `
    <div class="reflect-kpis">
      <div class="reflect-kpi"><b>${t.threads}</b><span>窗口·天（同一窗口每天算一次）</span></div>
      <div class="reflect-kpi"><b>${t.days ? (t.threads / t.days).toFixed(1) : '—'}</b><span>平均每天几个窗口</span></div>
      <div class="reflect-kpi"><b>${t.diaries}<small>/ ${t.days}</small></b><span>写了日记的天</span></div>
      <div class="reflect-kpi"><b>${t.artifacts}</b><span>日记里记的「做出来的」</span></div>
      <div class="reflect-kpi"><b>${t.open_loops}</b><span>「没做完的」</span></div>
    </div>
    <div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">一天里什么时候在聊</span><span class="reflect-sub">按消息条数，凌晨 4 点前算前一天</span></div>
      <div class="bars">${hours}</div><div class="bars-x">${hx}</div></div>
    <div class="two" style="margin-top:34px">
      <div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">一周里哪天</span></div><div class="bars wd">${wds}</div><div class="bars-x wd">${WD.map(x => `<span>${x}</span>`).join('')}</div></div>
      <div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">日记里的情绪词</span></div><div class="pills">${moods}</div>
        <div class="reflect-sec-head" style="margin-top:22px"><span class="reflect-sec-label">来源</span></div>
        <div class="split"><i style="flex:${c};background:var(--claude)" title="Claude ${fmtW(c)} 字"></i><i style="flex:${g};background:var(--gemini)" title="Gemini ${fmtW(g)} 字"></i></div>
        <div class="reflect-legend" style="margin-top:8px"><span><i style="border-color:var(--claude)"></i>Claude ${fmtW(c)}</span><span><i style="border-color:var(--gemini)"></i>Gemini ${fmtW(g)}</span></div></div>
    </div>
    <div class="two" style="margin-top:34px">
      <div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">跨天最久的窗口</span></div><div class="dlist">${longs}</div></div>
      <div class="reflect-sec"><div class="reflect-sec-head"><span class="reflect-sec-label">字最多的几天</span></div><div class="dlist">${busy}</div></div>
    </div>`;
  box.querySelectorAll('a[data-open]').forEach(a => a.onclick = () =>
    navigate('day/' + a.dataset.open + (a.dataset.conv ? '/conv/' + a.dataset.conv : '')));
}

async function loadReflect(refresh) {
  const my = ++RREQ; clearTimeout(RPOLL);
  const rk = $('#reflectRange').value;
  const d = await fetch(`/api/reflect?range=${rk}${refresh ? '&refresh=1' : ''}`).then(r => r.json());
  if (my !== RREQ) return;
  RDATA = d; renderReflect(); renderData();
  if (d.regenerating) RPOLL = setTimeout(() => loadReflect(false), 4000);
}
function openModal(pane, arg) {
  $('#overlay').classList.remove('hidden');
  showPane(pane);
  if (pane === 'themes') loadThemes(arg);
  if (pane === 'river') loadRiver();
  if (!RDATA) loadReflect(false);
  if (pane === 'convs') { pollCSync(); if (!CONVS.length) loadConvs(); }
  if (pane === 'notion') {
    if (!$('#ntBody').innerHTML.trim()) $('#ntBody').innerHTML = '<div class="reflect-loading">在核对 Notion…</div>';
    pollNotion();
  }
}
function closeModal() { $('#overlay').classList.add('hidden'); clearTimeout(RPOLL); }
$('#reflectOpen').onclick = () => navigate('panel/reflect');
$('#ckOpen').onclick = () => { location.href = '/checkin'; };
$('#convsOpen').onclick = () => navigate('panel/convs');
$('#modalClose').onclick = () => navigate(mainRoute());
$('#overlay').addEventListener('click', e => { if (e.target.id === 'overlay') navigate(mainRoute()); });
$('#reflectRange').onchange = () => { RDATA = null; loadReflect(false); };
$('#reflectRefresh').onclick = () => loadReflect(true);
// 面板切换本身只管样式；进哪个面板由路由决定（下面 .modal-nav-item 的 onclick 走 navigate）
function showPane(pane) {
  document.querySelectorAll('.modal-nav-item[data-pane]').forEach(x =>
    x.classList.toggle('active', x.dataset.pane === pane));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.dataset.pane === pane));
}
document.querySelectorAll('.modal-nav-item[data-pane]').forEach(b =>
  b.onclick = () => navigate('panel/' + b.dataset.pane));
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#overlay').classList.contains('hidden')) navigate(mainRoute()); });


// ================================================================ 打卡（单独的全屏页 /checkin）
async function refreshCkDot() {
  const r = await fetch('/api/checkin').then(r => r.json());
  $('#ckDot').classList.toggle('hidden', r.items.length > 0);
}

// ================================================================ 话题河流
// 每周各大类占了多少「主题·天」。颜色 = 类别身份，固定顺序（已过配色校验）；叠放顺序 = 颜色顺序。
const RV_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948', '#b3aea3'];
let RV = null, RV_SCALE = 'share', RV_FROM = '2025-08-18', RV_HI = -1;

async function loadRiver() {
  $('#rvChart').innerHTML = '<div class="reflect-loading">在数…</div>';
  RV = await fetch('/api/river?from=' + RV_FROM).then(r => r.json());
  renderRiver();
}

// 单调三次插值（Fritsch–Carlson）：平滑但不过冲。返回分段的贝塞尔 [p0, c1, c2, p1]，
// 这样同一条边界既能正着画（上面那条带子的下沿）也能倒着画（下面那条的上沿），两边严丝合缝。
// 以前是把点倒过来再插值一次，陡的地方两次插值对不上，会裂出白缝。
function monoSegs(pts) {
  const n = pts.length, dx = [], m = [], t = [], segs = [];
  if (n < 2) return segs;
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
    if (h > 9) { const k = 3 / Math.sqrt(h); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], h = dx[i] / 3;
    segs.push([[x0, y0], [x0 + h, y0 + t[i] * h], [x1 - h, y1 - t[i + 1] * h], [x1, y1]]);
  }
  return segs;
}
const _f = p => p[0].toFixed(1) + ',' + p[1].toFixed(1);
const segsFwd = segs => segs.map(s => `C${_f(s[1])} ${_f(s[2])} ${_f(s[3])}`).join('');
const segsRev = segs => segs.slice().reverse().map(s => `C${_f(s[2])} ${_f(s[1])} ${_f(s[0])}`).join('');

function rvValues() {
  return RV.smooth.map(row => {
    if (RV_SCALE === 'count') return row;
    const s = row.reduce((a, b) => a + b, 0);
    return s < 0.34 ? null : row.map(v => v / s);      // 这周（平滑后）基本没记录：留空档，不假装各类都归零
  });
}

function renderRiver() {
  const box = $('#rvChart');
  if (!RV.weeks.length) { box.innerHTML = '<div class="reflect-loading">还没有数据</div>'; return; }
  const vals = rvValues(), n = vals.length, K = RV.cats.length;
  const W = Math.max(box.clientWidth, 300), H = 400, L = 38, R = 10, T = 10, B = 26;
  const stacks = vals.map(row => { if (!row) return null; let acc = 0; return row.map(v => { const y0 = acc; acc += v; return [y0, acc]; }); });
  const max = RV_SCALE === 'share' ? 1 : Math.max(...stacks.filter(Boolean).map(s => s[K - 1][1]), 1);
  const x = i => L + (n === 1 ? 0 : i / (n - 1)) * (W - L - R);
  const y = v => T + (1 - v / max) * (H - T - B);

  // 连续有数据的几段分开画；段与段之间是没记录的空档
  const segs = [];
  stacks.forEach((s, i) => { if (!s) return; const g = segs[segs.length - 1]; if (g && g[g.length - 1] === i - 1) g.push(i); else segs.push([i]); });
  const half = (W - L - R) / Math.max(n - 1, 1) / 2;
  // 每段先算出 K+1 条边界曲线（第 k 条 = 第 k 类的下沿 = 第 k-1 类的上沿），带子用相邻两条围出来
  const bands = segs.map(g => {
    const xs = g.length === 1 ? [x(g[0]) - half, x(g[0]) + half] : g.map(x);   // 孤零零一周：画成一小截
    const at = j => g.length === 1 ? g[0] : g[j];
    const edges = [];
    for (let k = 0; k <= K; k++)
      edges.push(monoSegs(xs.map((xx, j) => [xx, y(k === 0 ? stacks[at(j)][0][0] : stacks[at(j)][k - 1][1])])));
    return RV.cats.map((c, k) => {
      const lo = edges[k], hi = edges[k + 1];
      return `<path class="rv-band" data-k="${k}" d="M${_f(hi[0][0])}${segsFwd(hi)}L${_f(lo[lo.length - 1][3])}${segsRev(lo)}Z" fill="${RV_COLORS[k]}"/>`;
    }).join('');
  }).join('');
  const gaps = segs.slice(1).map((g, j) => {
    const a = x(segs[j][segs[j].length - 1]), b = x(g[0]);
    return `<rect x="${a}" y="${T}" width="${b - a}" height="${H - T - B}" class="rv-gap"/>` +
      (b - a > 40 ? `<text x="${(a + b) / 2}" y="${T + 16}" text-anchor="middle" class="rv-ax">没记录</text>` : '');
  }).join('');

  // 纵轴：占比 0/50/100%，数量取 3 个整数刻度
  const yt = RV_SCALE === 'share' ? [0, .5, 1] : [0, Math.round(max / 2), Math.round(max)];
  const grid = yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="rv-grid"/>
    <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="rv-ax">${RV_SCALE === 'share' ? Math.round(v * 100) + '%' : v}</text>`).join('');
  // 横轴：每月第一周标一下，太挤就隔几个月
  const months = [];
  RV.weeks.forEach((w, i) => { const m = w.slice(0, 7); if (!months.length || months[months.length - 1].m !== m) months.push({ m, i }); });
  const every = Math.ceil(months.length / Math.max(1, Math.floor((W - L - R) / 46)));
  let lastX = -99;
  const xt = months.filter((_, j) => j % every === 0).filter(({ i }) => { const ok = x(i) - lastX >= 40; if (ok) lastX = x(i); return ok; }).map(({ m, i }) =>
    `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" class="rv-ax">${m.slice(2).replace('-', '.')}</text>`).join('');

  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="各类事情每周占比的变化">
    ${grid}${gaps}<g class="rv-bands">${bands}</g>${xt}
    <line id="rvCross" class="rv-cross hidden" y1="${T}" y2="${H - B}"/>
    <rect id="rvHit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;

  $('#rvLegend').innerHTML = RV.cats.map((c, k) =>
    `<span class="rv-li" data-k="${k}"><i style="background:${RV_COLORS[k]}"></i>${c}</span>`).join('');
  $('#rvLegend').querySelectorAll('.rv-li').forEach(el => {
    el.onmouseenter = () => rvEmph(+el.dataset.k); el.onmouseleave = () => rvEmph(-1);
  });
  rvEmph(RV_HI);
  $('#rvSub').textContent = `${RV.weeks[0]} → ${RV.weeks[n - 1]}，${n} 周。每周算各类事情占了多少：一件事做了一天算一份，AI 聊天和手写日记都算；前后 3 周平滑过。`;

  const hit = $('#rvHit'), tip = $('#rvTip'), cross = $('#rvCross');
  hit.onmousemove = e => {
    const r = box.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round((e.clientX - r.left - L) / (W - L - R) * (n - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.classList.remove('hidden');
    const raw = RV.raw[i], v = vals[i] || RV.cats.map(() => 0);
    const rows = RV.cats.map((c, k) => ({ c, k, v: v[k], raw: raw[k], top: RV.top[i][k] }))
      .filter(r => r.v > 0.004).reverse();
    tip.innerHTML = `<b>${RV.weeks[i]} 那周</b>` + (rows.length ? rows.map(r =>
      `<div class="rv-tr"><i style="background:${RV_COLORS[r.k]}"></i><span>${r.c}</span>
         <em>${RV_SCALE === 'share' ? Math.round(r.v * 100) + '%' : r.v.toFixed(1)}</em></div>` +
      (r.top.length ? `<div class="rv-top">${r.top.map(esc).join('、')}</div>` : '')).join('') : '<div class="dim">这周没记录</div>');
    tip.classList.remove('hidden');
    const pane = tip.parentElement.getBoundingClientRect();
    let left = e.clientX - pane.left + 14;
    if (left + 260 > pane.width) left = e.clientX - pane.left - 274;
    tip.style.left = Math.max(0, left) + 'px';
    tip.style.top = (box.offsetTop + 8) + 'px';
  };
  hit.onmouseleave = () => { tip.classList.add('hidden'); cross.classList.add('hidden'); };
  if (!$('#rvTableBox').classList.contains('hidden')) renderRiverTable();
}

function rvEmph(k) {
  RV_HI = k;
  document.querySelectorAll('.rv-band').forEach(p => p.style.opacity = k < 0 || +p.dataset.k === k ? 1 : .18);
}

// 表格视图：按月合计的占比（低对比度的几个颜色靠它补上可读性）
function renderRiverTable() {
  const by = {};
  RV.weeks.forEach((w, i) => { const m = w.slice(0, 7); by[m] = by[m] || RV.cats.map(() => 0); RV.raw[i].forEach((v, k) => by[m][k] += v); });
  $('#rvTableBox').innerHTML = `<table class="rv-table"><tr><th>月份</th>${RV.cats.map(c => `<th>${c}</th>`).join('')}</tr>` +
    Object.entries(by).map(([m, row]) => { const s = row.reduce((a, b) => a + b, 0) || 1;
      return `<tr><td>${m}</td>${row.map(v => `<td>${v ? Math.round(v / s * 100) + '%' : ''}</td>`).join('')}</tr>`; }).join('') + '</table>';
}

document.querySelectorAll('#rvScale button').forEach(b => b.onclick = () => {
  RV_SCALE = b.dataset.v; document.querySelectorAll('#rvScale button').forEach(x => x.classList.toggle('on', x === b)); if (RV) renderRiver();
});
document.querySelectorAll('#rvRange button').forEach(b => b.onclick = () => {
  RV_FROM = b.dataset.v; document.querySelectorAll('#rvRange button').forEach(x => x.classList.toggle('on', x === b)); loadRiver();
});
$('#rvTable').onclick = () => { const t = $('#rvTableBox'); t.classList.toggle('hidden'); if (!t.classList.contains('hidden') && RV) renderRiverTable(); };
window.addEventListener('resize', () => { if (RV && !$('#overlay').classList.contains('hidden')) renderRiver(); });

// ================================================================ 主题账本
let THEMES = null, TH_OPEN = null, TH_ONES = false;

async function loadThemes(openId) {
  if (openId) TH_OPEN = +openId;
  // 每次打开都重拿：新写一篇日记账本就变了，数据量也就几百行
  if (!THEMES) $('#thBody').innerHTML = '<div class="reflect-loading">在翻账本…</div>';
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
    $('#thBody').innerHTML = '<div class="reflect-loading">账本还是空的——新写的日记会往里记。</div>';
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
        `<li><a data-open="${d.date}">${d.date}</a><span>${(d.srcs || '').includes('hand') ? '<em class="src-hand">手写</em>' : ''}${esc(d.note || '')}</span></li>`).join('')}</ol>` : ''}
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


// ================================================================ 窗口管理 / 导入
let CONVS = [], CONV_SHOWN = 120;

function renderConvs() {
  const kw = ($('#convFilter').value || '').trim().toLowerCase();
  const rows = CONVS.filter(c => !kw || (c.title || '').toLowerCase().includes(kw) || (c.preview || '').toLowerCase().includes(kw));
  const list = rows.slice(0, CONV_SHOWN).map(c => {
    const multi = (c.n_days || 0) > 1;
    const title = c.title_fallback ? esc((c.preview || c.title || '').replace(/\s+/g, ' ').slice(0, 60)) : esc(c.title);
    return `<div class="crow" data-d="${c.last_date}" data-c="${c.id}">
      <span class="src ${c.source}"></span>
      <span class="tt ${c.title_fallback ? 'fb' : ''}">${title}${multi ? `<span class="span">${c.n_days} 天</span>` : ''}${c.last_import && c.first_import !== c.last_import ? '<small>已合并</small>' : ''}</span>
      <span class="m">${multi ? c.first_date.slice(5) + ' → ' : ''}${c.last_date}</span>
      <span class="m">${c.n_msgs} 条</span>
      <span class="m">${fmtW(c.chars || 0)} 字</span></div>`;
  }).join('');
  $('#convList').innerHTML = list + (rows.length > CONV_SHOWN ? `<a class="more" id="convMore">还有 ${rows.length - CONV_SHOWN} 个，再显示 200</a>` : '');
  $('#convList').querySelectorAll('.crow').forEach(r => r.onclick = () => navigate(`day/${r.dataset.d}/conv/${r.dataset.c}`));
  const m = $('#convMore'); if (m) m.onclick = () => { CONV_SHOWN += 200; renderConvs(); };
}

async function loadConvs() {
  const d = await fetch('/api/conversations').then(r => r.json());
  CONVS = d.conversations;
  const src = Object.entries(d.by_source).map(([k, v]) => `${k === 'claude' ? 'Claude' : 'Gemini'} ${v}`).join(' · ');
  $('#convSub').textContent = `${d.total} 个窗口（${src}），其中 ${d.multi_day} 个横跨多天。最新的在上面。`;
  renderConvs();
  const h = await fetch('/api/imports').then(r => r.json());
  $('#importHist').innerHTML = h.imports.map(x => `<div class="row">
      <span class="tt">${esc(x.file)}</span>
      <span class="m">${(x.imported_at || '').replace('T', ' ').slice(0, 16)}</span>
      <span class="m">${x.n_convs} 窗口 / 新 ${x.n_convs_new} · ${x.n_msgs.toLocaleString()} 条 / 新 ${x.n_msgs_new.toLocaleString()}${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated}` : ''}</span>
    </div>`).join('') || '<div class="reflect-sub">还没有导入记录</div>';
}
$('#convFilter').addEventListener('input', () => { CONV_SHOWN = 120; renderConvs(); });

async function doImport(files) {
  if (!files.length) return;
  const box = $('#importResult'); box.classList.remove('hidden');
  box.innerHTML = `<div class="r"><span class="pulse"></span>正在导入 ${files.length} 个文件…</div>`;
  const fd = new FormData(); [...files].forEach(f => fd.append('file', f));
  const r = await fetch('/api/import', { method: 'POST', body: fd }).then(r => r.json());
  box.innerHTML = (r.results || []).map(x => x.ok
    ? `<div class="r"><b>${esc(x.file)}</b><span class="m">${x.n_convs} 个窗口（新 ${x.n_convs_new}）· ${x.n_msgs.toLocaleString()} 条（新 ${x.n_msgs_new.toLocaleString()}）· 涉及 ${x.days_touched.length} 天${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated} 篇，侧栏可补全` : ''}</span></div>`
    : `<div class="r bad"><b>${esc(x.file)}</b><span class="m">${esc(x.error || '失败')}</span></div>`).join('')
    + (r.error ? `<div class="r bad">${esc(r.error)}</div>` : '');
  // 日历、日记状态、回顾指纹都可能变了：全部重拉
  const d = await fetch('/api/days').then(r => r.json());
  DAYS = d.days; BYDATE = {}; DAYS.forEach(x => BYDATE[x.date] = x);
  buildCalendar(); refreshBatchButton(); RDATA = null;
  loadConvs();
  if (SEL) openDay(SEL);
}
$('#importPick').onclick = () => $('#importFile').click();
$('#importFile').onchange = e => { doImport(e.target.files); e.target.value = ''; };
const ib = $('#importBox');
ib.addEventListener('dragover', e => { e.preventDefault(); ib.classList.add('over'); });
ib.addEventListener('dragleave', () => ib.classList.remove('over'));
ib.addEventListener('drop', e => { e.preventDefault(); ib.classList.remove('over'); doImport(e.dataTransfer.files); });

// 切到「窗口」栏目时再加载
// 切到「窗口」栏目时的加载已经收进 openModal()，这里不再单独挂

// ---------------------------------------------------------------- Notion 同步
// 判别「哪页是 Ephemeris 推的」在后端做（icon 💬 + 裸日期标题 + notion_pages 表）。
// 这里只管把结果摆出来，以及起/盯后台任务。

let NTPOLL = null;

function ntRow(label, n, hint) {
  return `<div class="row"><span class="tt">${label}<span class="m" style="margin-left:10px">${hint || ''}</span></span><span class="m">${n} 天</span></div>`;
}

function renderNotion(s) {
  const body = $('#ntBody'), sub = $('#ntSub');

  if (s.computing && !s.connected) {      // 首次扫远端要 90s+，后台算着，这里等一拍
    sub.textContent = '在核对…';
    body.innerHTML = '<div class="reflect-loading">在核对 Notion（远端上千页，头一次要一分多钟）…</div>';
    clearTimeout(NTPOLL); NTPOLL = setTimeout(pollNotion, 3000);
    return;
  }
  if (!s.connected) {
    sub.textContent = '没连上';
    body.innerHTML = `<div class="reflect-sec">
      <p>还没配 Notion token。在 Notion 里建一个 <b>internal integration</b>（不是 personal access token），
      把 secret 写进 <code>ephemeris/.env</code> 的 <code>NOTION_TOKEN=</code>，再把 Notes Database 共享给它，然后重启。</p>
      ${s.error ? `<p class="m" style="color:var(--coral-deep)">${esc(s.error)}</p>` : ''}
    </div>`;
    return;
  }

  const j = s.job || {};
  sub.textContent = `已连接 · ${esc(s.integration || '')} · 远端 ${s.remote_pages} 页`;

  const pending = (s.create || 0) + (s.append || 0) + (s.update || 0) + (s.replace || 0);
  const running = j.running;
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;

  body.innerHTML = `
    <div class="reflect-sec">
      <div class="reflect-sec-head"><span class="reflect-sec-label">${s.diaries} 篇日记</span>
        <span class="reflect-sub">${pending ? `${pending} 天待同步` : '全部已同步'}</span></div>
      <div class="dlist">
        ${ntRow('已同步', s.skip || 0, '内容没变，不动')}
        ${ntRow('新建', s.create || 0, '这天你没写过日记')}
        ${ntRow('追加', s.append || 0, '追到你自己那页末尾')}
        ${(s.update || 0) ? ntRow('重推', s.update, '日记重跑过，或是误建的重复页') : ''}
        ${(s.replace || 0) ? ntRow('换新', s.replace, '日记重跑过，内容在你自己那页里 — 只换掉分隔线之后属于 Ephemeris 的那段') : ''}
      </div>
    </div>
    <div class="reflect-sec">
      ${running
        ? `<div class="r"><span class="pulse"></span>${esc(j.action || '')} ${esc(j.current || '')} — ${j.done}/${j.total}（${pct}%）</div>`
        : pending
          ? `<button id="ntSync" class="btn-primary">同步这 ${pending} 天</button>`
          : `<div class="m">没有待办。生成了新日记之后回来这里推。</div>`}
      ${j.finished && !running ? `<div class="m" style="margin-top:10px">上次跑完 ${rel(j.finished)}${j.failed ? ` · 失败 ${j.failed}` : ''}</div>` : ''}
      ${(j.errors || []).length ? `<div class="dlist" style="margin-top:12px">${j.errors.map(e =>
          `<div class="row bad"><span class="tt">${esc(e.date)}</span><span class="m">${esc(e.error)}</span></div>`).join('')}</div>` : ''}
    </div>
    <div class="reflect-sec">
      <div class="reflect-sec-head"><span class="reflect-sec-label">约定</span></div>
      <p class="m">标题是裸日期（<code>2025-8-21</code>，不补零），属性全部留空交给 Notion AI。
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

$('#ntRefresh').onclick = async () => { $('#ntBody').innerHTML = '<div class="reflect-loading">在重新核对 Notion…</div>';
  await fetch('/api/notion/status?refresh=1'); pollNotion(); };
// Notion 面板的首次加载同样收进 openModal()
$('#notionOpen').onclick = () => navigate('panel/notion');

// ---------------------------------------------------------------- 按窗口回顾
// 和日记是转置关系：日记把一天里所有窗口合成一篇；这里挑一个窗口，看它自己的时间轴。
// 只有跨天的窗口才值得看（1657 个里 165 个跨天），列表默认只给这些。

let WINS = [], WSEL = null, WPOLL = null, WIN_SHOWN = 80;

function winRowHtml(w) {
  const title = (w.title || w.title_fallback || '').trim() || '(无标题)';
  return `<a class="winrow ${w.id === WSEL ? 'sel' : ''}" data-w="${w.id}">
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
  if (j.running) WPOLL = setTimeout(() => openWindow(WSEL, true), 2000);
  // 已经做过回顾的窗口，又多聊了几天：自动只补这几天，旧小结不动
  else if (r.arc && missing && !WAUTO[WSEL]) { WAUTO[WSEL] = true; startWinGen(false); }
}

const WAUTO = {};      // 每个窗口每次打开页面只自动补一次，补失败了不会死循环
async function startWinGen(refresh) {
  if (!WSEL) return;
  $('#winArc').innerHTML = '<div class="r"><span class="pulse"></span>起来了…</div>';
  await fetch(`/api/window/${WSEL}${refresh ? '?refresh=1' : ''}`, { method: 'POST' });
  openWindow(WSEL, true);
}

async function openWindow(id, quiet) {
  WSEL = id;
  if (!quiet) {
    document.querySelectorAll('.winrow.sel').forEach(x => x.classList.remove('sel'));
    document.querySelector(`.winrow[data-w="${id}"]`)?.classList.add('sel');
    $('#win').classList.remove('hidden');
    $('#main').scrollTop = 0;
  }
  const r = await fetch(`/api/window/${id}`).then(r => r.json());
  if (WSEL !== id || r.error) return;
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
    const r = await fetch(`/api/window/${WSEL}/day/${date}`).then(r => r.json());
    box.innerHTML = `<div class="tbar"><label><input type="checkbox" class="aitog"> 显示 AI 回复</label></div>`
      + r.messages.map(m => msgHtml(m)).join('');
    box.querySelector('.aitog').onchange = ev => box.classList.toggle('showai', ev.target.checked);
  }
  box.classList.remove('hidden');
  op.textContent = '收起';
});

// ---------------------------------------------------------------- 模式切换

function setMode(mode) {
  MODE = mode;
  document.querySelectorAll('.ms-item').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  const win = mode === 'win';
  $('#cal').classList.toggle('hidden', win);
  $('#batch').classList.toggle('hidden', win || !$('#batch').dataset.want);
  document.querySelector('.legend').classList.toggle('hidden', win);
  $('#winpane').classList.toggle('hidden', !win);
  $('#day').classList.toggle('hidden', win || !SEL);
  $('#win').classList.toggle('hidden', !win || !WSEL);
  if (win && !WINS.length) loadWindows();
}
document.querySelectorAll('.ms-item').forEach(b => b.onclick = () =>
  navigate(b.dataset.mode === 'win' ? (WSEL ? 'win/' + WSEL : 'win') : (SEL ? 'day/' + SEL : 'day')));

// ---------------------------------------------------------------- Routing
// 跟 Verbatim 同一套：用 hash（#/...）而不是 History API + 服务端路由。
// hash 从不发到服务器，刷新一个带 hash 的 URL 照样命中 Flask 唯一的 GET /，
// index.html 正常渲染，状态恢复全在这一段客户端代码里。app.py 完全不用改。
//
// openDay / openWindow / openModal 这些函数本身不动 —— 它们本来就是「传个 id 进去，
// 现查现渲染」。路由层只是把原来直接调用它们的地方换成 navigate(hash)，
// 由 hashchange → applyRoute() 统一分发：单一入口，不会重复渲染。
//
//   #/day/2026-09-19                        某一天
//   #/day/2026-09-19/conv/<id>[/msg/<id>]   展开某个窗口 / 滚到某条消息（搜索结果用）
//   #/day/2026-09-19/journal                那天的手写日记，展开全文
//   #/win                                   窗口模式，还没选
//   #/win/<conv_id>                         某个窗口的时间轴
//   #/panel/reflect|themes|river|data|convs|notion  浮层里的面板
//   #/panel/themes/<id>                     展开某个主题
//   /checkin[?date=]                         打卡：单独的全屏页（旧的 #/panel/checkin 会跳过去）

function navigate(hash, { replace = false } = {}) {
  if (replace) { history.replaceState(null, '', '#/' + hash); applyRoute(); }
  else if (location.hash === '#/' + hash) applyRoute();   // 点同一个链接也要有反应
  else location.hash = '/' + hash;                        // 触发 hashchange → applyRoute
}

// 浮层关掉之后该回到哪 —— 回当前模式下正看着的东西
function mainRoute() {
  if (MODE === 'win') return WSEL ? 'win/' + WSEL : 'win';
  return SEL ? 'day/' + SEL : 'day';
}

function applyRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  // 兼容老的 #2026-09-19（改版前的格式）
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return navigate('day/' + raw, { replace: true });

  const parts = raw ? raw.split('/').map(decodeURIComponent) : [];
  const head = parts[0];

  if (head === 'panel' && parts[1] === 'checkin') {      // 老链接：打卡已经挪到单独的页面
    location.href = '/checkin' + (parts[2] ? '?date=' + parts[2] : '');
    return;
  }
  if (head === 'panel') {
    openModal(parts[1] || 'reflect', parts[2]);
    return;
  }
  closeModal();                       // 非 panel 路由：确保浮层是关着的

  if (head === 'win') {
    setMode('win');
    if (parts[1] && parts[1] !== WSEL) openWindow(parts[1]);
    return;
  }

  if (head === 'day') {
    setMode('day');
    const date = parts[1];
    if (!date) return LATEST ? navigate('day/' + LATEST, { replace: true }) : undefined;
    if (!BYDATE[date]) return LATEST ? navigate('day/' + LATEST, { replace: true }) : undefined;
    const focus = {};
    if (parts[2] === 'journal') focus.journal = true;
    for (let i = 2; i < parts.length; i += 2) {
      if (parts[i] === 'conv') focus.conv = parts[i + 1];
      if (parts[i] === 'msg') focus.msg = parts[i + 1];
    }
    const hasFocus = focus.conv || focus.msg || focus.journal;
    if (date === SEL && !hasFocus) return;      // 已经在看这天了（比如刚关掉浮层），别重拉
    openDay(date, hasFocus ? focus : undefined);
    return;
  }

  if (LATEST) navigate('day/' + LATEST, { replace: true });
}

// ---------------------------------------------------------------- claude.ai 自动同步
// 状态条放在「窗口与导入」面板顶上——那一栏本来就是讲「数据从哪来」的。

let CSPOLL = null;

function renderCSync(s) {
  const box = $('#syncBox'); if (!box) return;
  const j = s.job || {}, last = s.last_run;

  if (!s.configured) {
    box.innerHTML = `<div class="sync-row"><span class="sync-dot off"></span>
      <span>没配 claude.ai 自动同步。在 <code>ephemeris/.env</code> 里设 <code>CLAUDE_SESSION_KEY</code> 就能每天自动拉新对话。</span></div>`;
    return;
  }
  if (j.running) {
    box.innerHTML = `<div class="sync-row"><span class="pulse"></span>
      <span>正在从 claude.ai 同步：${esc(j.stage || '')} ${j.total ? `${j.done}/${j.total}` : ''}</span></div>`;
    clearTimeout(CSPOLL); CSPOLL = setTimeout(pollCSync, 2000);
    return;
  }

  const bad = j.error || (last && !last.ok);
  const when = last?.finished_at ? rel(last.finished_at) : '还没跑过';
  const detail = last?.ok
    ? `拉了 ${last.n_fetched || 0} 个对话${last.n_msgs_new ? `，新消息 ${last.n_msgs_new}` : '，没有新内容'}`
    : esc(j.error || last?.note || '失败');
  box.innerHTML = `<div class="sync-row">
      <span class="sync-dot ${bad ? 'bad' : 'ok'}"></span>
      <span><b>claude.ai 自动同步</b>　每天 ${s.at}，拉完自动写日记 · 上次 ${when} · ${detail}</span>
      <a id="csyncNow">${bad ? '重试' : '立刻同步'}</a>
    </div>`;
  $('#csyncNow').onclick = async () => {
    box.querySelector('.sync-row').innerHTML = '<span class="pulse"></span><span>起来了…</span>';
    await fetch('/api/claude/sync', { method: 'POST' });
    pollCSync();
  };
  clearTimeout(CSPOLL);
}

async function pollCSync() {
  const s = await fetch('/api/claude/sync').then(r => r.json()).catch(() => null);
  if (s) renderCSync(s);
}
