// 聊天页（/chat、/chat/<id>）：「问」单独一个全屏页，照 claude.ai 的样子（2026-10-13 用户要的）。
// 后端跟浮层「问」栏是同一套：/api/qa（对话列表 + 这段的记录）、/api/qa/ask（后台问）、/api/qa/live（写到哪了）、/api/qa/stop（停下）。
// 一段对话一个网址；/chat 是还没开的新对话（问第一句时才建）。写的时候发送键变成圆圈停止键，停下后写出来的那段留着。
// 别的对话在写时（一次只问一个）：发送键禁用，输入框上面一条提示「「标题」还在写… 去看看 · 停下」。
// 左下角「N 张卡」点开是做卡的小弹层（进度、做上、停下）；老的浮层「问」栏 10-13 删了，做卡只在这里。
// 右下角「Opus 5.5 High」点开是模型菜单（10-14）：选的模型和 effort 记在本机浏览器，每次问都带上（/api/qa/ask 的 model / effort）。
import { ask, usd } from './js/util.js';
import { pull, place, mount } from './viz.js';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const B = document.body;
let THREAD = +B.dataset.thread || null, BUSY = false, POLL = null, LIVE_THREAD = null, THREADS = [], CARDS = null, CARD_POLL = null;
// 模型和 effort（右下角的菜单，照 claude.ai；10-14：自己选，Fable 太贵不放）。列表来自后端 llm.choices()，第一个是默认：
// 有 Claude 列 Claude 的，只有 DeepSeek 列 DeepSeek 的（那时没有 Effort 这一项，EFFORTS 是空的）
const MODELS = [...document.querySelectorAll('#chSubModels [data-model]')].map(b => [b.dataset.model, b.textContent.trim()]);
const EFFORTS = [...document.querySelectorAll('#chSubEffort [data-effort]')].map(b => [b.dataset.effort, b.textContent.trim()]);
let MODEL = MODELS[0]?.[0], EFFORT = 'high';
try {
  const m = localStorage.getItem('qaModel'), e = localStorage.getItem('qaEffort');
  if (MODELS.some(x => x[0] === m)) MODEL = m;
  if (EFFORTS.some(x => x[0] === e)) EFFORT = e;
} catch { /* 隐私模式：不记 */ }

// ---------- 正文：[[id]] → 上标数字，**粗体**、换行、「- 」列表；```html 块是图（viz.js） ----------
function body(text, cites) {
  const n = new Map(cites.map((c, i) => [c.id, i + 1]));
  const [t, codes] = pull(text);
  let h = esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/\[\[([^\]]+)\]\]/g, (_, id) => n.has(id) ? `<sup><a class="ch-cite" data-n="${n.get(id)}">${n.get(id)}</a></sup>` : '');
  return place(h.split(/\n{2,}/).filter(p => p.trim()).map(p => {
    const lines = p.split('\n');
    return lines.every(l => /^\s*[-•]\s/.test(l))
      ? `<ul>${lines.map(l => `<li>${l.replace(/^\s*[-•]\s/, '')}</li>`).join('')}</ul>`
      : `<p>${lines.join('<br>')}</p>`;
  }).join(''), codes);
}

const isJournal = c => (c.conv_id || '').startsWith('journal-');     // Notion 手写日记做的卡（cards.JOURNAL）

function sources(cites) {
  if (!cites.length) return '';
  return `<details class="ch-src"><summary>出处 ${cites.length} 条</summary>${cites.map((c, i) => `
    <div class="ch-src-row" data-n="${i + 1}"><span class="ch-src-n">${i + 1}</span>
      <span class="ch-src-main"><span class="ch-src-meta">${c.date} · ${isJournal(c) ? '你写的' : c.who === 'me' ? '你说' : 'AI 说'} · ${esc(c.title || '')}</span>
        ${c.kind === '原话' ? '' : esc(c.text) + ' '}<span class="ch-src-q">「${esc(c.quote)}」</span></span>
      <a class="ch-go" href="${isJournal(c) ? `/#/day/${c.date}/journal` : `/#/day/${c.date}/conv/${encodeURIComponent(c.conv_id)}/msg/${encodeURIComponent(c.msg_id)}`}">${isJournal(c) ? '去日记' : '去原话'} →</a></div>`).join('')}</details>`;
}

// ---------- 怎么查的（10-17，照 claude.ai：查的时候一步一行、带计时；答完折成回答上面一行「查了 N 步 · 用时」，点开看全部） ----------
// 一步是 {k: look 的命令（挑图标）, t: 在干什么, d: 细节}；k = 'say' 是它查的中途说的话。老记录是一行字。
const ICON = {
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.2-4.2"/>',
  cards: '<rect x="4" y="8" width="12" height="12" rx="2"/><path d="M8 4.5h9.5A2.5 2.5 0 0 1 20 7v9.5"/>',
  doc: '<path d="M7 3.5h7l4 4V20.5H7z"/><path d="M10 12h5M10 16h5"/>',
  chat: '<path d="M4.5 5.5h15v10h-9l-4 3.5v-3.5h-2z"/>',
  calc: '<path d="M5 20V12M12 20V5M19 20v-6"/>',
  day: '<rect x="4" y="5.5" width="16" height="14.5" rx="2"/><path d="M4 10.5h16M9 3.5v4M15 3.5v4"/>',
  pen: '<path d="M14.5 5.5l4 4L9 19H5v-4z"/>',
};
const KIND_ICON = { msgs: 'search', look: 'search', cards: 'cards', read: 'doc', conv: 'chat', convs: 'chat', sum: 'calc', stats: 'calc',
  sql: 'calc', schema: 'calc', day: 'day', diaries: 'day', journal: 'pen' };
const icon = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[KIND_ICON[k] || 'search']}</svg>`;
const asStep = s => typeof s === 'string' ? { k: 'look', t: s.replace(/^🔎\s*/, ''), d: '' } : s;
const clock = ms => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const took = s => s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分${s % 60 ? ` ${s % 60} 秒` : '钟'}`;

function stepHtml(s, live) {
  s = asStep(s);
  if (s.k === 'say') return `<li class="ch-step say"><span class="ch-step-i"><i></i></span><span class="ch-step-b">${esc(s.t)}</span></li>`;
  return `<li class="ch-step${live ? ' live' : ''}"><span class="ch-step-i">${icon(s.k)}</span><span class="ch-step-b">`
    + `<span class="ch-step-t">${esc(s.t)}</span>${s.d ? `<span class="ch-step-d">${esc(s.d)}</span>` : ''}</span></li>`;
}

function trailHtml(steps, running) {
  const SHOW = 8, hidden = running ? Math.max(0, steps.length - SHOW) : 0, shown = steps.slice(hidden);
  return `<ol class="ch-trail">${hidden ? `<li class="ch-step more"><span class="ch-step-i"><i></i></span><span class="ch-step-b">更早的 ${hidden} 步</span></li>` : ''}`
    + shown.map((s, i) => stepHtml(s, running && i === shown.length - 1)).join('') + '</ol>';
}

const nSteps = steps => steps.filter(s => asStep(s).k !== 'say').length;

function traceHtml(trace) {
  const steps = (trace && trace.steps) || [], n = nSteps(steps);
  if (!n) return '';
  return `<details class="ch-trace"><summary><span class="ch-trace-i">${icon('search')}</span>`
    + `<span>查了 ${n} 步${trace.secs ? ` · 用时 ${took(trace.secs)}` : ''}</span>`
    + `<svg class="ch-trace-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 6l6 6-6 6"/></svg></summary>`
    + trailHtml(steps, false) + '</details>';
}

const qHtml = t => `<div class="ch-q"><div>${esc(t)}</div></div>`;
const lostHtml = last => `<div class="ch-lost"><span>这个问题没答完（出错了，或者那时服务重启了）</span>`
  + (last ? '<button type="button" class="ch-lost-go">重新问</button>' : '') + '</div>';
const aHtml = (t, cites, stopped, trace) => `<div class="ch-a${stopped ? ' stopped' : ''}">${traceHtml(trace)}${body(t, cites || [])}${sources(cites || [])}</div>`;
const turnHtml = t => t.role === 'user' ? qHtml(t.text) : aHtml(t.text, t.cites, false, t.trace);

// 写到一半：出处还是模型的记号（[c12]、[[c:id]]），先按出现顺序临时编号
function draftHtml(text) {
  const n = new Map(), cite = /\[\[[^\]]*\]\]|\[c\d+[^\[\]\n]{0,80}\]/g;
  const [raw, codes] = pull(text);
  const t = raw.replace(/\[\[?[^\]\n]*$/, '');
  let html = '', at = 0;
  for (const m of t.matchAll(cite)) {
    if (!n.has(m[0])) n.set(m[0], n.size + 1);
    html += esc(t.slice(at, m.index)) + `<sup class="ch-cite">${n.get(m[0])}</sup>`;
    at = m.index + m[0].length;
  }
  html += esc(t.slice(at));
  return place(html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split(/\n{2,}/).filter(p => p.trim())
    .map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join(''), codes, true);
}

const SPIN = '<svg class="ch-spin" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="12" rx="9.4" ry="5.4" stroke="currentColor" stroke-width="1.6" transform="rotate(-32 12 12)"/><circle cx="17.4" cy="7.2" r="3" fill="var(--accent)"/></svg>';
// 写的时候：顶上一行（转圈、在干什么、计时 · 几步），下面是步骤；开始写回答了就只留顶上那行，下面是正在写的字
function waitHtml(live) {
  const steps = live.steps || [], text = live.partial || '', n = nSteps(steps);
  const since = (live.started && Date.parse(live.started)) || Date.now();
  const what = text ? '在写回答' : live.deep === false ? '在翻证据卡' : n ? '在查你的聊天记录' : '在想先查什么';
  return `<div class="ch-run${text ? ' writing' : ''}"><div class="ch-run-h">${SPIN}<span class="ch-run-t">${what}</span>`
    + `<span class="ch-run-meta"><span data-since="${since}">${clock(Date.now() - since)}</span>${n ? ` · ${n} 步` : ''}</span></div>`
    + (!text && steps.length ? trailHtml(steps, true) : '') + '</div>'
    + (text ? draftHtml(text) : '');
}
setInterval(() => document.querySelectorAll('[data-since]').forEach(e => { e.textContent = clock(Date.now() - +e.dataset.since); }), 1000);

// ---------- 页面状态 ----------
function greet() {
  const h = new Date().getHours(), name = B.dataset.name || '';
  const hi = h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : h < 23 ? '晚上好' : '夜深了';
  $('#chGreet').textContent = `${hi}，${name}`;
}

function setBusy(on) {
  BUSY = on;
  B.classList.toggle('ch-busy', on);
  $('#chSend').setAttribute('aria-label', on ? '停止' : '发送');
  $('#chSend').title = on ? '停止' : '发送（回车）';
  syncSend();
}
const other = () => !!LIVE_THREAD && LIVE_THREAD !== THREAD;      // 别的对话正在写
function syncSend() { $('#chSend').disabled = !BUSY && (other() || !$('#chInput').value.trim()); }

// 别的对话在写：提示条（标题、去看看、停下）
function renderOther() {
  const bar = $('#chOther');
  if (!other()) { bar.hidden = true; return; }
  const t = THREADS.find(x => x.id === LIVE_THREAD);
  bar.innerHTML = `<span class="ch-dot"></span><span class="ch-other-t">「${esc((t && t.title) || '上一段对话')}」还在写…</span>`
    + `<a href="/chat/${LIVE_THREAD}" data-act="look">去看看</a><span class="ch-other-sep">·</span><a href="#" data-act="stop">停下</a>`;
  bar.hidden = false;
  syncSend();
}

function label(list, id) { return (list.find(x => x[0] === id) || [id, id])[1]; }

function renderModel() {
  $('#chModel').innerHTML = esc(label(MODELS, MODEL)) + (EFFORTS.length ? ` <i>${esc(label(EFFORTS, EFFORT))}</i>` : '');
  $('#chMenuCur').textContent = label(MODELS, MODEL);
  if ($('#chMenuEffort')) $('#chMenuEffort').textContent = label(EFFORTS, EFFORT);
  document.querySelectorAll('#chSubModels [data-model]').forEach(b => { b.classList.toggle('on', b.dataset.model === MODEL); b.setAttribute('aria-checked', b.dataset.model === MODEL); });
  document.querySelectorAll('#chSubEffort [data-effort]').forEach(b => { b.classList.toggle('on', b.dataset.effort === EFFORT); b.setAttribute('aria-checked', b.dataset.effort === EFFORT); });
}

function openSub(name) {
  document.querySelectorAll('#chMenu .ch-sub').forEach(x => { x.hidden = x.dataset.sub !== name; x.classList.remove('left'); });
  document.querySelectorAll('#chMenu [data-sub]').forEach(x => x.classList.toggle('open', x.dataset.sub === name && x.classList.contains('ch-mi')));
  const sub = name && $(`#chMenu .ch-sub[data-sub="${name}"]`);
  if (!sub) return;
  sub.style.maxHeight = '';
  const r = sub.getBoundingClientRect();
  if (r.right > innerWidth - 8) sub.classList.add('left');
  if (r.top < 8) sub.style.maxHeight = `${r.bottom - 8}px`;            // 上面放不下（窄屏、输入框在中间时）：列表自己滚
  (sub.querySelector('.ch-mi.on') || sub.querySelector('.ch-mi')).focus({ preventScroll: true });
}

function toggleMenu(open = $('#chMenu').hidden) {
  $('#chMenu').hidden = !open;
  $('#chModel').setAttribute('aria-expanded', open);
  openSub(null);
  if (open) $('#chMenu .ch-mi').focus({ preventScroll: true });
}

function pick(kind, v) {
  if (kind === 'model') MODEL = v; else EFFORT = v;
  try { localStorage.setItem(kind === 'model' ? 'qaModel' : 'qaEffort', v); } catch { /* 同上 */ }
  renderModel();
  toggleMenu(false);
  $('#chModel').focus({ preventScroll: true });
}

function renderThreads(list) {
  THREADS = list;
  const cur = list.find(t => t.id === THREAD);
  $('#chTitle').textContent = cur ? cur.title : '';
  document.title = (cur ? cur.title + ' · ' : '') + '问 · Ephemeris';
  if (EDITING) return;                                      // 正在改名：别把输入框冲掉，改完再画
  const row = t => `<div class="ch-trow${t.id === THREAD ? ' on' : ''}" data-id="${t.id}">
    <button class="ch-thread${t.id === LIVE_THREAD ? ' live' : ''}" data-id="${t.id}" title="${esc(t.title)}">${esc(t.title || '新对话')}</button>
    <button type="button" class="ch-tmore" data-id="${t.id}" aria-label="更多" aria-haspopup="menu">${DOTS}</button></div>`;
  const pinned = list.filter(t => t.pinned), rest = list.filter(t => !t.pinned);
  $('#chThreads').innerHTML = (pinned.length ? '<div class="ch-sec">置顶</div>' + pinned.map(row).join('') : '')
    + (rest.length ? '<div class="ch-sec">最近</div>' + rest.map(row).join('') : '');
}

// ---------- 左栏一段对话的「…」：置顶、改名（10-16） ----------
const DOTS = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="6" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18" cy="12" r="1.6"/></svg>';
let EDITING = false;
const saveThread = (id, body) => fetch(`/api/qa/thread/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body) }).then(r => r.json()).catch(() => null);

function threadMenu(btn) {
  const m = $('#chTMenu'), id = +btn.dataset.id, t = THREADS.find(x => x.id === id);
  if (!m.hidden && +m.dataset.id === id) return closeTMenu();
  m.dataset.id = id;
  m.innerHTML = `<button type="button" class="ch-mi" data-act="pin" role="menuitem"><span class="ch-mi-t">${t && t.pinned ? '取消置顶' : '置顶'}</span></button>
    <button type="button" class="ch-mi" data-act="rename" role="menuitem"><span class="ch-mi-t">改名</span></button>`;
  m.hidden = false;
  const r = btn.getBoundingClientRect(), h = m.offsetHeight;
  m.style.left = Math.max(8, r.right - m.offsetWidth) + 'px';   // 右边跟「…」对齐，不伸出侧栏
  m.style.top = (r.bottom + 4 + h > innerHeight ? r.top - 4 - h : r.bottom + 4) + 'px';
  btn.classList.add('open');
  m.querySelector('.ch-mi').focus();
}
function closeTMenu() {
  $('#chTMenu').hidden = true;
  document.querySelectorAll('.ch-tmore.open').forEach(b => b.classList.remove('open'));
}

function renameRow(id) {
  const row = $(`.ch-trow[data-id="${id}"]`), t = THREADS.find(x => x.id === id);
  if (!row || !t) return;
  EDITING = true;
  const inp = document.createElement('input');
  inp.className = 'ch-tedit'; inp.value = t.title; inp.maxLength = 60; inp.setAttribute('aria-label', '对话标题');
  row.classList.add('editing');
  row.querySelector('.ch-thread').replaceWith(inp);
  inp.focus(); inp.select();
  let done = false;
  const finish = async save => {
    if (done) return;
    done = true;
    const v = inp.value.trim();
    const r = save && v && v !== t.title ? await saveThread(id, { title: v }) : null;
    EDITING = false;
    renderThreads(r && r.threads ? r.threads : THREADS);
  };
  inp.addEventListener('keydown', e => {
    if (e.isComposing) return;
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  inp.addEventListener('blur', () => finish(true));
}

// 第一轮答完，服务器另起一条线起标题（几秒）：隔一会儿看一眼，起好了就换上
async function waitTitle(id, tries = 6) {
  const t = THREADS.find(x => x.id === id);
  if (!t || t.named || tries <= 0) return;
  await new Promise(ok => setTimeout(ok, 2500));
  const r = await fetch('/api/qa' + (THREAD ? `?thread=${THREAD}` : '')).then(r => r.json()).catch(() => null);
  if (r) renderThreads(r.threads);
  waitTitle(id, tries - 1);
}

// ---------- 证据卡：左下角「N 张卡」+ 点开的小弹层 ----------
function renderCards(c) {
  CARDS = c;
  const j = (c && c.job) || {}, btn = $('#chCards');
  btn.innerHTML = (j.running ? '<span class="ch-dot"></span>' : '') + (c && c.n ? `${c.n.toLocaleString()} 张卡` : '证据卡');
  btn.hidden = !c;
  if (!$('#chPop').hidden) renderPop();
  clearTimeout(CARD_POLL);
  if (j.running) CARD_POLL = setTimeout(loadCards, 3000);     // 做卡在跑：三秒看一眼进度
}

function renderPop() {
  const c = CARDS || {}, j = c.job || {}, pop = $('#chPop');
  const made = c.n ? `${c.n.toLocaleString()} 张，来自 ${c.convs.toLocaleString()} 个窗口<span class="ch-pop-dim">${c.first} → ${c.last}</span>` : '还没有证据卡';
  let act;
  if (j.running) {
    act = `<div class="ch-pop-run"><span class="ch-dot"></span>` + (j.stage === '算向量'
      ? `在给卡算向量 ${j.vec_done || 0} / ${j.vec_total || '…'} 批`
      : `在做 ${j.done} / ${j.total} 个窗口${j.current ? ` · ${j.current} 个在做` : ''} · 已花 $${(j.cost || 0).toFixed(2)}`
        + (j.failed ? ` · ${j.failed} 个没做全，下次再补` : '')) + `</div><button type="button" class="ch-pop-btn" data-act="stop">停下</button>`;
  } else if (c.todo) {
    act = `<div class="ch-pop-todo">还有 ${c.todo} 个窗口没做</div><button type="button" class="ch-pop-btn primary" data-act="make">做上（${usd(c.todo_estimate)}）</button>`;
  } else {
    act = '<div class="ch-pop-todo">都做好了</div>';
  }
  pop.innerHTML = `<div class="ch-pop-h">证据卡</div><div class="ch-pop-made">${made}</div>${act}`
    + (j.error && !j.running ? `<div class="ch-pop-bad">上次没做完：${esc(j.error)}</div>` : '');
}

async function loadCards() {
  const r = await fetch('/api/qa' + (THREAD ? `?thread=${THREAD}` : '')).then(r => r.json()).catch(() => null);
  if (r) renderCards(r.cards);
  else CARD_POLL = setTimeout(loadCards, 5000);
}

function togglePop(open) {
  const pop = $('#chPop');
  open = open ?? pop.hidden;
  pop.hidden = !open;
  $('#chCards').setAttribute('aria-expanded', open);
  if (open) renderPop();
}

function scrollEnd(force) {
  const s = $('#chScroll');
  if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 160) s.scrollTop = s.scrollHeight;
}

async function load(thread) {
  THREAD = thread || null;
  const r = await fetch('/api/qa' + (THREAD ? `?thread=${THREAD}` : '')).then(r => r.json()).catch(() => null);
  if (!r) { $('#chLog').innerHTML = '<div class="ch-a bad">连不上 Ephemeris（5055）。</div>'; return; }
  renderCards(r.cards);
  LIVE_THREAD = r.live && r.live.running ? r.live.thread : null;
  renderThreads(r.threads);
  const hist = THREAD ? r.history : [];
  B.classList.toggle('ch-empty', !hist.length && !(LIVE_THREAD && LIVE_THREAD === THREAD));
  // 问了没答上的（问到一半服务重启了 / 出错了）：问题下面一行说明；是最后一句的话带「重新问」
  const live = LIVE_THREAD && LIVE_THREAD === THREAD, last = hist.length - 1;
  $('#chLog').innerHTML = hist.map((t, i) => turnHtml(t)
    + (t.role === 'user' && hist[i + 1]?.role !== 'assistant' && !(live && i === last) ? lostHtml(i === last) : '')).join('');
  if (LIVE_THREAD && LIVE_THREAD === THREAD) {           // 关了页面再打开时还在写：接着显示
    $('#chLog').insertAdjacentHTML('beforeend', `<div class="ch-a ch-wait">${waitHtml(r.live)}</div>`);
    setBusy(true);
    poll();
  } else {
    setBusy(false);                                        // 别的对话在写：这里不能问（一次只问一个），上面一条提示能去看、能停
    if (LIVE_THREAD) poll();
  }
  renderOther();
  scrollEnd(true);
}

function go(thread, replace) {
  const url = thread ? `/chat/${thread}` : '/chat';
  if (location.pathname !== url) history[replace ? 'replaceState' : 'pushState']({}, '', url);
  load(thread);
}

function poll() {
  clearTimeout(POLL);
  POLL = setTimeout(async () => {
    const d = await fetch('/api/qa/live').then(r => r.json()).catch(() => null);
    if (!d) return poll();
    const wait = $('#chLog .ch-wait');
    if (d.running) {
      // 有新的一步 / 新写的字才重画（计时自己走），不然每半秒整块重画、新的一步的淡入动画也会一直重播
      const key = `${(d.steps || []).length}|${(d.partial || '').length}`;
      if (wait && d.thread === THREAD && wait.dataset.key !== key) { wait.dataset.key = key; wait.innerHTML = waitHtml(d); scrollEnd(); }
      if (d.thread !== LIVE_THREAD) { LIVE_THREAD = d.thread; renderOther(); }
      return poll();
    }
    if (wait) {
      wait.outerHTML = d.error || !d.result ? `<div class="ch-a bad">没答上：${esc(d.error || '没有结果')}</div>`
        : aHtml(d.result.answer, d.result.cites, d.result.stopped, d.result.trace);
    }
    LIVE_THREAD = null;
    setBusy(false);
    renderOther();
    $('#chInput').focus();
    const r = await fetch('/api/qa' + (THREAD ? `?thread=${THREAD}` : '')).then(r => r.json()).catch(() => null);
    if (r) { renderThreads(r.threads); waitTitle(r.thread); }   // 新对话的标题、顺序跟着变
  }, 500);
}

async function send(e) {
  e.preventDefault();
  if (BUSY) {                                               // 写的时候这个键是「停止」
    await fetch('/api/qa/stop', { method: 'POST' }).catch(() => null);
    return;
  }
  const q = $('#chInput').value.trim();
  if (!q || other()) return;
  if (!THREAD) {                                            // 新对话：问第一句时才建
    const r = await fetch('/api/qa/new', { method: 'POST' }).then(r => r.json()).catch(() => null);
    if (!r) return;
    THREAD = r.thread;
    history.pushState({}, '', `/chat/${THREAD}`);
  }
  B.classList.remove('ch-empty');
  $('#chLog').insertAdjacentHTML('beforeend', qHtml(q) + `<div class="ch-a ch-wait">${waitHtml({ deep: true })}</div>`);
  $('#chInput').value = '';
  grow();
  launch({ question: q }, () => { $('#chInput').value = q; });   // 没发出去：问题还给你，改一下再问
}

// 发出去、等着写完：新问题（send）和「重新问」（again：服务器拿这段对话里没答完的最后那句）共用
async function launch(body, failed) {
  setBusy(true);
  scrollEnd(true);
  const r = await fetch('/api/qa/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, thread: THREAD, model: MODEL, effort: EFFORTS.length ? EFFORT : null }) }).then(r => r.json()).catch(() => ({ error: '连不上 Ephemeris' }));
  if (!r.error && !r.started) r.error = '上一个问题还没答完';
  if (!r.error) { LIVE_THREAD = THREAD; return poll(); }
  $('#chLog .ch-wait').outerHTML = `<div class="ch-a bad">没答上：${esc(r.error)}</div>`;
  failed?.();
  setBusy(false);
}

function grow() {
  const t = $('#chInput');
  t.style.height = 'auto';
  t.style.height = t.scrollHeight + 'px';
  syncSend();
}

// ---------- 事件 ----------
$('#chForm').addEventListener('submit', send);
$('#chInput').addEventListener('input', grow);
$('#chInput').addEventListener('keydown', e => {            // 回车发送，Shift+回车换行（输入法选字时不算）
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!BUSY) $('#chForm').requestSubmit(); }
});
$('#chNew').addEventListener('click', () => { closeSide(); go(null); $('#chInput').focus(); });
$('#chThreads').addEventListener('click', e => {
  const more = e.target.closest('.ch-tmore');
  if (more) return threadMenu(more);
  const t = e.target.closest('.ch-thread');
  if (t) { closeSide(); go(+t.dataset.id); }
});
$('#chThreads').addEventListener('scroll', closeTMenu);
$('#chTMenu').addEventListener('click', async e => {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const id = +$('#chTMenu').dataset.id, t = THREADS.find(x => x.id === id);
  closeTMenu();
  if (b.dataset.act === 'rename') return renameRow(id);
  const r = await saveThread(id, { pinned: !(t && t.pinned) });
  if (r && r.threads) renderThreads(r.threads);
});
$('#chLog').addEventListener('click', e => {
  const again = e.target.closest('.ch-lost-go');
  if (again) {
    if (BUSY || other()) return;
    again.closest('.ch-lost').outerHTML = `<div class="ch-a ch-wait">${waitHtml({ deep: true })}</div>`;
    launch({ again: true });
    return;
  }
  const sup = e.target.closest('.ch-cite');
  if (!sup) return;
  const a = sup.closest('.ch-a'), d = a.querySelector('.ch-src');
  if (!d) return;
  d.open = true;
  a.querySelectorAll('.ch-src-row').forEach(x => x.classList.toggle('hl', x.dataset.n === sup.dataset.n));
  a.querySelector(`.ch-src-row[data-n="${sup.dataset.n}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
});
$('#chOther').addEventListener('click', e => {
  const a = e.target.closest('a[data-act]');
  if (!a) return;
  e.preventDefault();
  if (a.dataset.act === 'look') go(LIVE_THREAD);
  else fetch('/api/qa/stop', { method: 'POST' }).catch(() => null);     // 停下后 poll 看到写完，提示条自己收起
});
$('#chCards').addEventListener('click', e => { e.stopPropagation(); togglePop(); });
$('#chModel').addEventListener('click', e => { e.stopPropagation(); toggleMenu(); });
$('#chMenu').addEventListener('click', e => {
  e.stopPropagation();
  const b = e.target.closest('.ch-mi');
  if (!b) return;
  if (b.dataset.act === 'cur') toggleMenu(false);
  else if (b.dataset.sub) openSub(b.classList.contains('open') ? null : b.dataset.sub);
  else if (b.dataset.model) pick('model', b.dataset.model);
  else if (b.dataset.effort) pick('effort', b.dataset.effort);
});
$('#chMenu').addEventListener('mouseover', e => {           // 鼠标停在「Effort ›」「更多模型 ›」上就展开（窄屏靠点）
  const b = e.target.closest('.ch-mi[data-sub]');
  if (b && matchMedia('(hover: hover) and (min-width: 761px)').matches && !b.classList.contains('open')) openSub(b.dataset.sub);
});
document.addEventListener('click', e => { if (!$('#chMenu').hidden && !e.target.closest('.ch-mm')) toggleMenu(false); });
renderModel();
$('#chPop').addEventListener('click', e => {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  if (b.dataset.act === 'retry') return renderPop();
  if (b.dataset.act === 'stop') {
    b.disabled = true;
    fetch('/api/qa/cards/stop', { method: 'POST' }).then(r => r.json()).then(renderCards).catch(() => { b.disabled = false; });
    return;
  }
  const c = CARDS;
  ask(b, `把这 ${c.todo} 个窗口的聊天做成证据卡，${usd(c.todo_estimate)}`, async () => {
    $('#chPop').innerHTML = '<div class="ch-pop-run"><span class="ch-dot"></span>起来了…</div>';
    const r = await fetch('/api/qa/cards', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`Ephemeris 出错了（${r.status}）`)))
      .catch(e => ({ __err: e.message === 'Failed to fetch' ? '连不上 Ephemeris' : e.message }));
    if (r.__err) {
      $('#chPop').innerHTML = `<div class="ch-pop-bad">没起来：${esc(r.__err)}</div><button type="button" class="ch-pop-btn" data-act="retry">再试一次</button>`;
      return;
    }
    renderCards(r);
  });
});
document.addEventListener('click', e => {                   // 点弹层外面：收起
  if (!$('#chPop').hidden && e.target.isConnected && !e.target.closest('#chPop, #chCards')) togglePop(false);
  if (!$('#chTMenu').hidden && !e.target.closest('#chTMenu, .ch-tmore')) closeTMenu();
});
const narrow = () => matchMedia('(max-width: 760px)').matches;
function closeSide() { B.classList.remove('ch-side-open'); }
$('#chScrim').addEventListener('click', closeSide);
document.addEventListener('keydown', e => {                 // Esc：先收弹层，再关抽屉
  if (e.key !== 'Escape') return;
  if (!$('#chTMenu').hidden) closeTMenu();
  else if (!$('#chMenu').hidden) {                               // 模型菜单：先收子菜单，再收菜单
    const open = $('#chMenu .ch-mi.open');
    if (open) { openSub(null); open.focus(); } else { toggleMenu(false); $('#chModel').focus(); }
  } else if (!$('#chPop').hidden) togglePop(false);
  else if (B.classList.contains('ch-side-open')) closeSide();
});
$('#chFold').addEventListener('click', () => {
  if (narrow()) return closeSide();
  B.classList.add('ch-folded');
  try { localStorage.setItem('chFolded', '1'); } catch { /* 同上 */ }
});
$('#chOpen').addEventListener('click', () => {
  if (narrow()) return B.classList.add('ch-side-open');
  B.classList.remove('ch-folded');
  try { localStorage.setItem('chFolded', '0'); } catch { /* 同上 */ }
});
try { if (localStorage.getItem('chFolded') === '1') B.classList.add('ch-folded'); } catch { /* 同上 */ }
window.addEventListener('popstate', () => load(+location.pathname.split('/')[2] || null));

// 主导航「打卡」上的小点：今天还没打卡就亮（跟主页 main.js 一样，只看一眼、不轮询）
fetch('/api/checkin').then(r => r.json()).then(r => {
  document.querySelectorAll('#mainNav .nav-dot').forEach(d => d.classList.toggle('hidden', r.items.length > 0));
}).catch(() => {});

// 回答里的图：插进来就跑起来；系统换深浅色时重画（颜色是画的时候读进去的）
new MutationObserver(() => mount($('#chLog'))).observe($('#chLog'), { childList: true, subtree: true });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => mount($('#chLog'), true));

greet();
load(THREAD);
$('#chInput').focus();
