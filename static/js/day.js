// 某一天：两个指标、手写日记、AI 日记、当天的窗口和消息。
import { $, esc, rel, feelColor, toContent, scrollWithin, markHits, usd, ask } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';
import { markDiary, viewMonth } from './calendar.js';
import { refreshBatchButton } from './batch.js';
import { journalHtml, imageCount, mdBlocks } from './md.js';

const CLIP = 300, CLIP_LINES = 8;   // 长消息先折到这么多字 / 这么多行（空行多的消息按字数折了还有一屏高）
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
// 我自己在 Notion 里写的，放在 AI 日记上面。只读；带格式和图片（md.js）；长的、图多的先收起来。

const J_FOLD = 700;

function renderJournal(list, open, q) {
  const box = $('#journal');
  const pages = list.filter(j => (j.text || '').trim());
  if (!pages.length) { box.className = 'journal hidden'; box.innerHTML = ''; return; }
  box.className = 'journal card-shell';
  box.innerHTML = `<div class="j-head"><span class="j-tag">我当天写的</span>
      <span class="dim tnum">${pages.reduce((a, j) => a + j.chars, 0).toLocaleString()} 字</span>
      <span class="sp"></span>${pages.map(j => j.gone_at
        ? `<span class="dim" title="Notion 里这页已经删了；Ephemeris 是备份，这里照样留着">Notion 里已删 · 这里留着</span>`
        : `<a href="${j.url}" target="_blank" rel="noopener">在 Notion 打开</a>`).join(' · ')}</div>` +
    pages.map((j, i) => {
      const long = (j.chars > J_FOLD || imageCount(j.text) > 1) && !open;
      return `<div class="j-body${long ? ' folded' : ''}" data-i="${i}">${markHits(journalHtml(j.text), q)}</div>` +
             (long ? `<a class="j-more" data-i="${i}">展开全文</a>` : '');
    }).join('');
  box.querySelectorAll('.j-more').forEach(a => a.onclick = () => {
    box.querySelector(`.j-body[data-i="${a.dataset.i}"]`).classList.remove('folded'); a.remove();
  });
  if (!open) return;
  // 从搜索点过来：先回到这天开头（日期标题在视野里），第一处命中还在屏幕下面才把它滚到中间
  toContent($('#day'));
  const hit = box.querySelector('mark');
  if (hit && hit.getBoundingClientRect().bottom > innerHeight - 40) hit.scrollIntoView({ block: 'center' });
}

// ---------------------------------------------------------------- 日记

function renderDiary(r) {
  const box = $('#diary');
  box.className = 'diary card-shell';           // 有日记：跟右边的窗口卡同一层白卡；空的 / 正在写的换成虚线框
  if (!r.threads.length && !r.diary) { box.className = 'hidden'; box.innerHTML = ''; return; }
  if (r.generating) {
    box.className = 'diary gen';
    box.innerHTML = `<span class="pulse"></span>正在读这天的 ${r.threads.length} 个窗口，写日记…`;
    return;
  }
  const d = r.diary, w = r.write || {}, chars = (S.byDate[S.sel]?.chars || 0).toLocaleString();
  if (!d) {
    box.className = 'diary empty';
    if (w.worth === false) box.classList.add('thin');
    // 字太少的天（侧栏补全也跳过它们）：不放大按钮，只留一个小链接
    box.innerHTML = w.worth === false
      ? `<p class="dim">这天只聊了 ${chars} 字，不到 ${w.min_chars} 字的天一般不写日记。<a id="genBtn">仍然写一篇（${usd(w.cost)}）</a></p>`
      : `<p>这天还没有日记。${r.threads.length} 个窗口、${chars} 字，压成 300 字大概要 20 秒。</p>
         <button class="btn-primary" id="genBtn">生成这天的日记（${usd(w.cost)}）</button>`;
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
      <span title="${esc(d.generated_at || '')}">${r.date === w.today && d.generated_at
        ? `写于 ${d.generated_at.slice(11, 16)} · 之后再聊的，明早同步后会重写` : rel(d.generated_at)}</span>
      ${d.added_later ? `<span title="导入旧聊天时，不会自动改写当时写好的日记">写好之后又补进了 ${d.added_later} 条消息</span>` : ''}
      <a id="genBtn">重新生成</a>
    </div>`;
  $('#genBtn').onclick = () => ask($('#genBtn'), `会替换这篇，当时那版就没了（${usd(w.cost)}）`, generateCurrent);
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

// 一条消息（按窗口回顾的时间轴也用）。从搜索跳过来的那条（target）不折，搜的词标出来。
// 我说的原样显示；AI 回的是 Markdown，按块渲染（md.js），折叠也按块切
export function msgHtml(m, target, q) {
  const text = m.text || '';
  if (m.role !== 'human') return aiMsgHtml(m, text, m.id && m.id === target, q);
  if (m.id && m.id === target) {
    return `<div class="msg target" id="msg-${m.id}">
            <span class="msg-time">${m.hhmm}</span><span class="msg-text">${markHits(esc(text), q)}</span></div>`;
  }
  let cut = CLIP;
  for (let i = 0, n = 0; i < Math.min(text.length, CLIP); i++) if (text[i] === '\n' && ++n === CLIP_LINES) { cut = i; break; }
  const body = text.length > cut
    ? `${esc(text.slice(0, cut))}<span class="msg-clip">…</span><a class="msg-more" data-full="1">展开 ${text.length} 字</a><span class="msg-full hidden">${esc(text.slice(cut))}</span>`
    : esc(text);
  return `<div class="msg ${m.role === 'human' ? '' : 'ai'}" id="msg-${m.id}">
            <span class="msg-time">${m.hhmm}</span><span class="msg-text">${body}</span></div>`;
}

function aiMsgHtml(m, text, isTarget, q) {
  const blocks = mdBlocks(text);
  let k = blocks.length;
  if (!isTarget) {
    for (let i = 0, n = 0, lines = 0; i < blocks.length; i++) {
      n += blocks[i].n; lines += blocks[i].lines;
      if (i && (n > CLIP || lines > CLIP_LINES)) { k = i; break; }
    }
  }
  const html = list => list.map(b => b.html).join('');
  const body = k < blocks.length
    ? `${html(blocks.slice(0, k))}<span class="msg-clip">…</span><a class="msg-more" data-full="1">展开 ${text.length} 字</a><div class="msg-full hidden">${html(blocks.slice(k))}</div>`
    : isTarget ? markHits(html(blocks), q) : html(blocks);
  return `<div class="msg ai${isTarget ? ' target' : ''}" id="msg-${m.id}">
            <span class="msg-time">${m.hhmm}</span><div class="msg-text msg-md j-body">${body}</div></div>`;
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
        <div class="thread-bar"><label><input type="checkbox" class="thread-aitog"> 显示 AI 回复</label>
          ${t.multi_day ? `<a class="thread-link" href="#/win/${t.conv_id}" title="这个窗口每天聊了什么（按窗口看）">按窗口看全程 →</a>` : ''}
          <a class="thread-link" href="/api/conversation/${t.conv_id}/export.md" download title="整个窗口（所有天、我和 AI 的）存成一个 Markdown 文件">导出整个窗口 .md</a></div>
        ${t.messages.map(m => msgHtml(m, focus?.msg, focus?.q)).join('')}
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
    if (el?.classList.contains('ai')) {                // 搜到的是 AI 的话（「窗口」栏搜全文）：把 AI 回复打开，不然看不见
      const body = el.closest('.thread-body');
      body.classList.add('showai'); body.querySelector('.thread-aitog').checked = true;
    }
    // 有搜的词：滚到那个词（长消息里它可能在很后面）；没有：消息比半屏还长就对齐开头，不然居中
    const mk = el?.querySelector('mark');
    if (el) setTimeout(() => (mk || el).scrollIntoView({
      block: mk || el.offsetHeight < innerHeight / 2 ? 'center' : 'start', behavior: 'smooth' }), 60);
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
      `<a data-d="${d.date}" class="${d.date === S.sel ? 'cur' : ''}" title="${d.msgs} 条 · ${d.chars.toLocaleString()} 字">${d.date.slice(5)}</a>`).join('')}</div>
    <a class="pop-win" href="#/win/${convId}">按窗口看全程 →</a>`;
  const b = anchor.getBoundingClientRect();
  pop.style.left = Math.min(b.left, window.innerWidth - 360) + 'px';
  pop.style.top = (b.bottom + 6) + 'px';
  pop.classList.remove('hidden');
  pop.onclick = e => {
    if (e.target.closest('.pop-win')) { pop.classList.add('hidden'); return; }    // 链接自己去 #/win/…
    const a = e.target.closest('a[data-d]');
    if (a) { pop.classList.add('hidden'); navigate(`day/${a.dataset.d}/conv/${convId}`); }
  };
}
document.addEventListener('click', e => {
  if (!e.target.closest('#pop') && !e.target.closest('.thread-span')) $('#pop').classList.add('hidden');
});
// 弹层是按点击时的位置摆的，列表一滚就对不上了：滚动就收起（弹层自己里面滚不算）
window.addEventListener('scroll', e => { if (!e.target.closest?.('#pop')) $('#pop').classList.add('hidden'); }, true);

// ---------------------------------------------------------------- 一天

export async function openDay(date, focus) {
  const first = !S.sel, moved = date !== S.sel;     // 数据变了重开同一天：别把人滚走
  S.sel = date;
  document.querySelectorAll('.cal-day.sel').forEach(c => c.classList.remove('sel'));
  viewMonth(date);                                   // 窄屏日历只摊开一个月：跟着这天走
  const c = document.querySelector(`.cal-day[data-d="${date}"]`);
  if (c) { c.classList.add('sel'); scrollWithin($('#cal'), c, first); }      // 刚打开：选中的那天摆在日历中间

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
  renderJournal(r.journal || [], focus && focus.journal, focus && focus.q);
  renderDiary(r);
  renderThreads(r.threads, focus);
  requestAnimationFrame(() => {
    const card = document.querySelector('.threads-card');
    if (card) card.style.setProperty('--list-top', card.getBoundingClientRect().top + 'px');
  });
  if (!focus && moved && (!first || coldScroll())) toContent($('#day'));
  if (r.generating) poll(date);
}

// 冷启动要不要滚到正文。宽屏本来就在顶上。窄屏侧栏叠在正文上面：
// 主导航是固定底栏（≤700，看 CSS 算出来的 position，不在 JS 里再写一遍断点）时滚了也看得见「问 / 打卡」，直接滚；
// 主导航在顶栏（701–1079）时，日记开头（日期下面约 300px）已经在首屏就不滚，免得把顶栏的「问 / 打卡」滚走
function coldScroll() {
  if (innerWidth >= 1080) return false;
  if (getComputedStyle(document.querySelector('.sb-nav')).position === 'fixed') return true;
  return $('#day').getBoundingClientRect().top > innerHeight - 300;
}

$('#prevBtn').onclick = () => NAV.prev && navigate('day/' + NAV.prev);
$('#nextBtn').onclick = () => NAV.next && navigate('day/' + NAV.next);
// ←/→（main.js 的键盘处理调它）
export function stepDay(dir) {
  const d = dir < 0 ? NAV.prev : NAV.next;
  if (d) navigate('day/' + d);
}
