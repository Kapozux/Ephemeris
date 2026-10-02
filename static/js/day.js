// 某一天：两个指标、手写日记、AI 日记、当天的窗口和消息。
import { $, esc, rel, feelColor } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';
import { markDiary } from './calendar.js';
import { refreshBatchButton } from './batch.js';

const CLIP = 300;   // 长消息先折到这么多字
let NAV = { prev: null, next: null }, POLL = null;

// 两个指标：过得怎么样（手写）、卡得多狠（聊天）。分开放，不合成一个数 —— 它们量的是一天里不同的部分
function renderSignals(sg) {
  const f = sg.mood, k = sg.friction, out = [];
  if (f) out.push(`<span class="day-sig" title="${esc(f.note || '')}"><i style="background:${feelColor(f.v)}"></i>过得 <b>${f.v > 0 ? '+' : ''}${+f.v.toFixed(1)}</b> ${esc(f.words || '')}<em>${f.src === 'checkin' ? '打卡' : '手写'}</em></span>`);
  if (k) out.push(`<span class="day-sig" title="${esc(k.evidence || '')}"><i style="background:var(--s${k.valence})"></i>卡得 <b>${k.valence}/4</b> ${esc(k.label || '')}<em>聊天</em></span>`);
  out.push(`<a class="day-sig day-sig-add" href="/checkin?date=${S.sel}">${(sg.checkins || []).length ? '改打卡' : '补打卡'}</a>`);
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
    box.innerHTML = `<p>这天还没有日记。${r.threads.length} 个窗口、${(S.byDate[S.sel]?.chars || 0).toLocaleString()} 字，压成 300 字大概要 20 秒。</p>
                     <button class="btn-primary" id="genBtn">生成这天的日记</button>`;
    $('#genBtn').onclick = generateCurrent;
    return;
  }
  const list = (t, arr) => (arr && arr.length)
    ? `<div><h5>${t}</h5><ul>${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '';
  // 这天接着哪几条线：第几天、隔了多久。点进去看那条线的全程
  const chips = (r.themes || []).map(t => {
    const extra = t.nth === 1 ? '新' : `第 ${t.nth} 天${t.gap > 7 ? ` · 隔 ${t.gap} 天` : ''}`;
    return `<a class="diary-chip${t.nth === 1 ? ' new' : ''}${t.gap > 7 ? ' back' : ''}" data-theme="${t.id}" title="${esc(t.note || '')}">${esc(t.name)}<em>${extra}</em></a>`;
  }).join('');
  box.innerHTML = `
    <h2>${esc(d.headline || '')}</h2>
    ${chips ? `<div class="diary-chips">${chips}</div>` : ''}
    ${(d.narrative || '').split('\n\n').filter(Boolean).map(p => `<p>${esc(p)}</p>`).join('')}
    <div class="diary-lists">${list('做出来的', d.artifacts)}${list('没做完的', d.open_loops)}</div>
    <div class="diary-foot">
      <span class="diary-topics">${(d.topics || []).map(esc).join(' · ')}</span>
      ${d.mood ? `<span>${esc(d.mood)}</span>` : ''}
      <span class="sp"></span>
      <span title="${esc(d.generated_at || '')}">${rel(d.generated_at)}</span>
      <a id="genBtn">重新生成</a>
    </div>`;
  $('#genBtn').onclick = generateCurrent;
  box.querySelectorAll('[data-theme]').forEach(a => a.onclick = () => navigate('panel/themes/' + a.dataset.theme));
}

async function generateCurrent() {
  if (!S.sel) return;
  await fetch(`/api/diary/${S.sel}`, { method: 'POST' });
  renderDiary({ generating: true, threads: { length: S.byDate[S.sel]?.threads || 0 } });
  poll(S.sel);
}

function poll(date) {
  clearInterval(POLL);
  POLL = setInterval(async () => {
    const r = await fetch(`/api/day/${date}`).then(r => r.json());
    if (!r.generating) {
      clearInterval(POLL);
      if (r.diary) { markDiary(date); refreshBatchButton(); }
      if (S.sel === date) openDay(date);
    }
  }, 2500);
}

// ---------------------------------------------------------------- 窗口列表

// 一条消息（按窗口回顾的时间轴也用）
export function msgHtml(m, target) {
  const long = m.text.length > CLIP;
  const body = long
    ? `${esc(m.text.slice(0, CLIP))}<span class="msg-clip">…</span><a class="msg-more" data-full="1">展开 ${m.text.length} 字</a><span class="msg-full hidden">${esc(m.text.slice(CLIP))}</span>`
    : esc(m.text);
  return `<div class="msg ${m.role === 'human' ? '' : 'ai'} ${m.id === target ? 'target' : ''}" id="msg-${m.id}">
            <span class="msg-time">${m.hhmm}</span><span class="msg-text">${body}</span></div>`;
}

function renderThreads(threads, focus) {
  $('#threadCount').textContent = `${threads.length}`;
  if (!threads.length) { $('#threads').innerHTML = '<div class="threads-empty">这天没有和 AI 聊天</div>'; return; }
  $('#threads').innerHTML = threads.map((t, i) => {
    const open = focus && t.conv_id === focus.conv;
    return `<div class="thread ${open ? 'hl' : ''}" data-conv="${t.conv_id}">
      <div class="thread-head" data-i="${i}">
        <span class="thread-time">${t.first}</span>
        <span class="src ${t.source}"></span>
        <span class="thread-title">${esc(t.title)}</span>
        <span class="thread-right">${t.multi_day ? `<span class="thread-span" data-conv="${t.conv_id}" title="这个窗口横跨了 ${t.span_from} → ${t.span_to}">${t.span_from.slice(5)} → ${t.span_to.slice(5)}</span>` : ''}<span class="thread-count">${t.n_msgs} 条 · ${(t.chars || 0).toLocaleString()} 字</span></span>
      </div>
      <div class="thread-body ${open ? '' : 'hidden'}" id="tb${i}">
        <div class="thread-bar"><label><input type="checkbox" class="thread-aitog"> 显示 AI 回复</label></div>
        ${t.messages.map(m => msgHtml(m, focus?.msg)).join('')}
      </div></div>`;
  }).join('');

  $('#threads').onclick = e => {
    const sp = e.target.closest('.thread-span');
    if (sp) { e.stopPropagation(); showSpan(sp.dataset.conv, sp); return; }
    const more = e.target.closest('.msg-more');
    if (more) {
      const x = more.parentElement;
      x.querySelector('.msg-full').classList.remove('hidden');
      x.querySelector('.msg-clip').remove(); more.remove(); return;
    }
    const tog = e.target.closest('.thread-aitog');
    if (tog) { tog.closest('.thread-body').classList.toggle('showai', tog.checked); return; }
    const h = e.target.closest('.thread-head');
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
    <div class="pop-sub">${c.first_ts.slice(0, 10)} → ${c.last_ts.slice(0, 10)} · 活跃 ${r.days.length} 天 · ${c.n_msgs} 条</div>
    <div class="pop-days">${r.days.map(d =>
      `<a data-d="${d.date}" class="${d.date === S.sel ? 'cur' : ''}" title="${d.msgs} 条 · ${d.chars.toLocaleString()} 字">${d.date.slice(5)}</a>`).join('')}</div>`;
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
  if (!e.target.closest('#pop') && !e.target.closest('.thread-span')) $('#pop').classList.add('hidden');
});

// ---------------------------------------------------------------- 一天

export async function openDay(date, focus) {
  S.sel = date;
  document.querySelectorAll('.cal-day.sel').forEach(c => c.classList.remove('sel'));
  const c = document.querySelector(`.cal-day[data-d="${date}"]`);
  if (c) { c.classList.add('sel'); c.scrollIntoView({ block: 'nearest' }); }

  const r = await fetch(`/api/day/${date}`).then(r => r.json());
  if (S.sel !== date) return;            // 用户已经点了别的
  $('#day').classList.remove('hidden');
  NAV = { prev: r.prev, next: r.next };
  $('#prevBtn').disabled = !r.prev;
  $('#nextBtn').disabled = !r.next;

  const info = S.byDate[date] || {};
  const wd = '日一二三四五六'[new Date(date + 'T12:00:00').getDay()];
  $('#dayTitle').innerHTML = `<span class="pagehead-date">${date}</span><span class="pagehead-weekday">周${wd}</span>`;
  const jChars = (r.journal || []).reduce((a, j) => a + (j.chars || 0), 0);
  $('#dayMeta').textContent = r.threads.length
    ? `${r.threads.length} 个对话窗口 · ${info.msgs || 0} 条 · ${(info.chars || 0).toLocaleString()} 字 · ${info.first_at || ''}–${info.last_at || ''}` +
      (jChars ? ` · 手写 ${jChars.toLocaleString()} 字` : '')
    : jChars ? `这天没有 AI 聊天，只有手写日记 · ${jChars.toLocaleString()} 字`
    : '这天还没有 AI 聊天和手写日记' + ((r.signals?.checkins || []).length ? `，只有 ${r.signals.checkins.length} 条打卡` : '');

  renderSignals(r.signals || {});
  renderJournal(r.journal || [], focus && focus.journal);
  renderDiary(r);
  renderThreads(r.threads, focus);
  requestAnimationFrame(() => {
    const card = document.querySelector('.threads-card');
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
