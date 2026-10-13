// 聊天页（/chat、/chat/<id>）：「问」单独一个全屏页，照 claude.ai 的样子（2026-10-13 用户要的）。
// 后端跟浮层「问」栏是同一套：/api/qa（对话列表 + 这段的记录）、/api/qa/ask（后台问）、/api/qa/live（写到哪了）、/api/qa/stop（停下）。
// 一段对话一个网址；/chat 是还没开的新对话（问第一句时才建）。写的时候发送键变成圆圈停止键，停下后写出来的那段留着。
// 别的对话在写时（一次只问一个）：发送键禁用，输入框上面一条提示「「标题」还在写… 去看看 · 停下」。
// 左下角「N 张卡」点开是做卡的小弹层（进度、做上、停下）；老的浮层「问」栏 10-13 删了，做卡只在这里。
import { ask, usd } from './js/util.js';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const B = document.body;
let THREAD = +B.dataset.thread || null, BUSY = false, POLL = null, LIVE_THREAD = null, THREADS = [], CARDS = null, CARD_POLL = null;
let DEEP = true;
try { DEEP = localStorage.getItem('qaDeep') !== '0'; } catch { /* 隐私模式：不记 */ }

// ---------- 正文：[[id]] → 上标数字，**粗体**、换行、「- 」列表 ----------
function body(text, cites) {
  const n = new Map(cites.map((c, i) => [c.id, i + 1]));
  let h = esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/\[\[([^\]]+)\]\]/g, (_, id) => n.has(id) ? `<sup><a class="ch-cite" data-n="${n.get(id)}">${n.get(id)}</a></sup>` : '');
  return h.split(/\n{2,}/).map(p => {
    const lines = p.split('\n');
    return lines.every(l => /^\s*[-•]\s/.test(l))
      ? `<ul>${lines.map(l => `<li>${l.replace(/^\s*[-•]\s/, '')}</li>`).join('')}</ul>`
      : `<p>${lines.join('<br>')}</p>`;
  }).join('');
}

function sources(cites) {
  if (!cites.length) return '';
  return `<details class="ch-src"><summary>出处 ${cites.length} 条</summary>${cites.map((c, i) => `
    <div class="ch-src-row" data-n="${i + 1}"><span class="ch-src-n">${i + 1}</span>
      <span class="ch-src-main"><span class="ch-src-meta">${c.date} · ${c.who === 'me' ? '你说' : 'AI 说'} · ${esc(c.title || '')}</span>
        ${c.kind === '原话' ? '' : esc(c.text) + ' '}<span class="ch-src-q">「${esc(c.quote)}」</span></span>
      <a class="ch-go" href="/#/day/${c.date}/conv/${encodeURIComponent(c.conv_id)}/msg/${encodeURIComponent(c.msg_id)}">去原话 →</a></div>`).join('')}</details>`;
}

const qHtml = t => `<div class="ch-q"><div>${esc(t)}</div></div>`;
const aHtml = (t, cites, stopped) => `<div class="ch-a${stopped ? ' stopped' : ''}">${body(t, cites || [])}${sources(cites || [])}</div>`;
const turnHtml = t => t.role === 'user' ? qHtml(t.text) : aHtml(t.text, t.cites);

// 写到一半：出处还是模型的记号（[c12]、[[c:id]]），先按出现顺序临时编号
function draftHtml(text) {
  const n = new Map(), cite = /\[\[[^\]]*\]\]|\[c\d+[^\[\]\n]{0,80}\]/g;
  const t = (text || '').replace(/\[\[?[^\]\n]*$/, '');
  let html = '', at = 0;
  for (const m of t.matchAll(cite)) {
    if (!n.has(m[0])) n.set(m[0], n.size + 1);
    html += esc(t.slice(at, m.index)) + `<sup class="ch-cite">${n.get(m[0])}</sup>`;
    at = m.index + m[0].length;
  }
  html += esc(t.slice(at));
  return html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}

const SPIN = '<svg class="ch-spin" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="12" rx="9.4" ry="5.4" stroke="currentColor" stroke-width="1.6" transform="rotate(-32 12 12)"/><circle cx="17.4" cy="7.2" r="3" fill="var(--accent)"/></svg>';
function waitHtml(live) {
  const steps = live.steps || [], text = live.partial || '';
  if (text) return draftHtml(text);
  return `<div class="ch-wait-head">${SPIN}${live.deep ? '在翻你的聊天记录…' : '在翻证据卡…'}</div>`
    + (steps.length ? `<ul class="ch-steps">${steps.slice(-6).map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : '');
}

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

function setMode(deep) {
  DEEP = deep;
  document.querySelectorAll('.ch-mode').forEach(b => {
    const on = (b.dataset.deep === '1') === deep;
    b.classList.toggle('on', on);
    b.setAttribute('aria-checked', on);
  });
  try { localStorage.setItem('qaDeep', deep ? '1' : '0'); } catch { /* 同上 */ }
}

function renderThreads(list) {
  THREADS = list;
  $('#chThreads').innerHTML = list.map(t => `<button class="ch-thread${t.id === THREAD ? ' on' : ''}${t.id === LIVE_THREAD ? ' live' : ''}" data-id="${t.id}"
    title="${esc(t.title)}">${esc(t.title || '新对话')}</button>`).join('');
  const cur = list.find(t => t.id === THREAD);
  $('#chTitle').textContent = cur ? cur.title : '';
  document.title = (cur ? cur.title + ' · ' : '') + '问 · Ephemeris';
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
  $('#chLog').innerHTML = hist.map(turnHtml).join('');
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
      if (wait && d.thread === THREAD) { wait.innerHTML = waitHtml(d); scrollEnd(); }
      if (d.thread !== LIVE_THREAD) { LIVE_THREAD = d.thread; renderOther(); }
      return poll();
    }
    if (wait) {
      wait.outerHTML = d.error || !d.result ? `<div class="ch-a bad">没答上：${esc(d.error || '没有结果')}</div>`
        : aHtml(d.result.answer, d.result.cites, d.result.stopped);
    }
    LIVE_THREAD = null;
    setBusy(false);
    renderOther();
    $('#chInput').focus();
    const r = await fetch('/api/qa' + (THREAD ? `?thread=${THREAD}` : '')).then(r => r.json()).catch(() => null);
    if (r) renderThreads(r.threads);                        // 新对话的标题、顺序跟着变
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
  $('#chLog').insertAdjacentHTML('beforeend', qHtml(q) + `<div class="ch-a ch-wait">${waitHtml({ deep: DEEP })}</div>`);
  $('#chInput').value = '';
  grow();
  setBusy(true);
  scrollEnd(true);
  const r = await fetch('/api/qa/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q, deep: DEEP, thread: THREAD }) }).then(r => r.json()).catch(() => ({ error: '连不上 Ephemeris' }));
  if (!r.error && !r.started) r.error = '上一个问题还没答完';
  if (!r.error) { LIVE_THREAD = THREAD; return poll(); }
  $('#chLog .ch-wait').outerHTML = `<div class="ch-a bad">没答上：${esc(r.error)}</div>`;
  $('#chInput').value = q;                                  // 问题还给你，改一下再问
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
document.querySelectorAll('.ch-mode').forEach(b => b.addEventListener('click', () => setMode(b.dataset.deep === '1')));
$('#chNew').addEventListener('click', () => { closeSide(); go(null); $('#chInput').focus(); });
$('#chThreads').addEventListener('click', e => {
  const t = e.target.closest('.ch-thread');
  if (t) { closeSide(); go(+t.dataset.id); }
});
$('#chLog').addEventListener('click', e => {
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
});
const narrow = () => matchMedia('(max-width: 760px)').matches;
function closeSide() { B.classList.remove('ch-side-open'); }
$('#chScrim').addEventListener('click', closeSide);
document.addEventListener('keydown', e => {                 // Esc：先收弹层，再关抽屉
  if (e.key !== 'Escape') return;
  if (!$('#chPop').hidden) togglePop(false);
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

greet();
setMode(DEEP);
load(THREAD);
$('#chInput').focus();
