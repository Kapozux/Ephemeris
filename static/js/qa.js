// 浮层「问」栏：一个聊天窗口，只凭证据卡回答（后端 qa.py / cards.py）；勾上「问 Claude」走 qa.answer_deep（它自己查原文）。
// 回答里的 [[卡 id]] 换成上标数字；下面列出处，每条带原话，点「去原话」回到那天那条消息。
import { $, esc, usd, ask } from './util.js';
import { navigate } from './nav.js';

let POLL = null, LIVE_POLL = null, BUSY = false, THREAD = null;

// 回答正文：先转义，再认 [[id]]、**粗体**、换行和「- 」列表
function body(text, cites) {
  const n = new Map(cites.map((c, i) => [c.id, i + 1]));
  let h = esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/\[\[([^\]]+)\]\]/g, (_, id) => n.has(id) ? `<sup><a class="qa-cite" data-n="${n.get(id)}">${n.get(id)}</a></sup>` : '');
  return h.split(/\n{2,}/).map(p => {
    const lines = p.split('\n');
    return lines.every(l => /^\s*[-•]\s/.test(l))
      ? `<ul>${lines.map(l => `<li>${l.replace(/^\s*[-•]\s/, '')}</li>`).join('')}</ul>`
      : `<p>${lines.join('<br>')}</p>`;
  }).join('');
}

function sources(cites) {
  if (!cites.length) return '';
  return `<details class="qa-src"><summary>出处 ${cites.length} 条</summary>${cites.map((c, i) => `
    <div class="qa-src-row" data-n="${i + 1}">
      <span class="qa-src-n">${i + 1}</span>
      <span class="qa-src-main"><span class="qa-src-meta">${c.date} · ${c.who === 'me' ? '你说' : 'AI 说'} · ${esc(c.title || '')}</span>
        ${c.kind === '原话' ? '' : esc(c.text)}<span class="qa-src-q">「${esc(c.quote)}」</span></span>
      <a class="qa-go" data-d="${c.date}" data-c="${c.conv_id}" data-m="${c.msg_id}">去原话 →</a></div>`).join('')}</details>`;
}

function turnHtml(t) {
  if (t.role === 'user') return `<div class="qa-q">${esc(t.text)}</div>`;
  return `<div class="qa-a">${body(t.text, t.cites || [])}${sources(t.cites || [])}</div>`;
}

function renderCards(c) {
  const j = c.job || {}, box = $('#qaCards');
  clearTimeout(POLL);
  if (j.running) {
    box.innerHTML = `<span class="pulse"></span> ` + (j.stage === '算向量'
      ? `在给卡算向量 ${j.vec_done || 0} / ${j.vec_total || '…'} 批`
      : `正在做证据卡 ${j.done} / ${j.total} 个窗口${j.current ? ` · ${j.current} 个在做` : ''} · 已花 $${(j.cost || 0).toFixed(2)}`
        + (j.failed ? ` · ${j.failed} 个没做全（限流 / 断线），下次再补` : '')) + ` · <a id="qaStop">停下</a>`;
    $('#qaStop').onclick = async () => { await fetch('/api/qa/cards/stop', { method: 'POST' }); };
    POLL = setTimeout(loadQA, 3000);
    return;
  }
  const made = c.n ? `证据卡：${c.convs} 个窗口 · ${c.n.toLocaleString()} 张（${c.first} → ${c.last}）` : '还没有证据卡';
  const todo = c.todo ? ` · 还有 ${c.todo} 个窗口要做 · <a id="qaMake">做上（${usd(c.todo_estimate)}）</a>` : ' · 都做好了';
  box.innerHTML = made + todo + (j.error ? ` · <span class="qa-bad">上次没做完：${esc(j.error)}</span>` : '');
  const mk = $('#qaMake');
  if (mk) mk.onclick = () => ask(mk, `把这 ${c.todo} 个窗口的聊天（你说的和 AI 说的）做成证据卡，走 DeepSeek，${usd(c.todo_estimate)}`, async () => {
    await fetch('/api/qa/cards', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    loadQA();
  });
}

// 问（快的 / 问 Claude）都在后台跑：每 0.6 秒看一眼 /api/qa/live，把查找进度和写到一半的回答画进等待的气泡；
// 写到一半时出处还是模型的记号（[c12]、[[c:id]]），按出现先后临时编号；写完换成认好出处的最终版
function draftHtml(text) {
  const n = new Map(), cite = /\[\[[^\]]*\]\]|\[c\d+[^\[\]\n]{0,80}\]/g;
  const t = (text || '').replace(/\[\[?[^\]\n]*$/, '');      // 正写到一半的记号先不显示
  let html = '', at = 0;
  for (const m of t.matchAll(cite)) {
    if (!n.has(m[0])) n.set(m[0], n.size + 1);
    html += esc(t.slice(at, m.index)) + `<sup class="qa-cite">${n.get(m[0])}</sup>`;
    at = m.index + m[0].length;
  }
  html += esc(t.slice(at));
  return html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}

function waitHtml(live) {
  const steps = live.steps || [], text = live.partial || '';
  const head = text ? '' : `<span class="pulse"></span> ${live.deep ? 'Claude 在查…' : '在翻卡…'}`;
  return head + (steps.length && !text ? `<ul class="qa-steps">${steps.slice(-6).map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : '')
    + (text ? draftHtml(text) : '');
}

function pollLive() {
  clearTimeout(LIVE_POLL);
  LIVE_POLL = setTimeout(async () => {
    const d = await fetch('/api/qa/live').then(r => r.json()).catch(() => null);
    const wait = $('#qaLog .qa-wait');
    if (!d) return pollLive();
    if (d.running) {
      if (wait) wait.innerHTML = waitHtml(d);
      return pollLive();
    }
    if (wait) wait.outerHTML = d.error || !d.result ? `<div class="qa-a qa-bad">没答上：${esc(d.error || '没有结果')}</div>`
      : turnHtml({ role: 'assistant', text: d.result.answer, cites: d.result.cites });
    doneSending();
    loadQA();                                              // 下拉框里这段对话的标题 / 时间跟着更新
  }, 600);
}

function doneSending() {
  BUSY = false;
  $('#qaSend').disabled = false;
  $('#qaInput').focus();
}

// 以前的对话：下拉框里列出来（第一个问题 · 日期），选一段就打开它、接着问
function renderThreads(r) {
  THREAD = r.thread;
  const list = r.threads.some(t => t.id === r.thread) ? r.threads : [{ id: r.thread, title: '', updated: '' }, ...r.threads];
  $('#qaThreads').innerHTML = list.map(t => `<option value="${t.id}"${t.id === r.thread ? ' selected' : ''}>`
    + `${esc((t.title || '新对话').slice(0, 24))}${t.updated ? ' · ' + t.updated.slice(5, 10) : ''}</option>`).join('');
}

export async function loadQA(thread) {
  const r = await fetch('/api/qa' + (thread || THREAD ? `?thread=${thread || THREAD}` : '')).then(r => r.json()).catch(() => null);
  if (!r) return;
  renderCards(r.cards);
  renderThreads(r);
  if (r.live && r.live.running && !BUSY) {           // 关掉浮层再打开时还在写：接着显示
    BUSY = true;
    $('#qaSend').disabled = true;
    $('#qaLog').innerHTML = r.history.map(turnHtml).join('') + `<div class="qa-a qa-wait">${waitHtml(r.live)}</div>`;
    pollLive();
    return;
  }
  if (!BUSY) {
    $('#qaLog').innerHTML = r.history.length ? r.history.map(turnHtml).join('')
      : '<div class="qa-empty">问一句试试。它只看证据卡，答案里每句话后面的小数字就是出处。</div>';
  }
}

async function send(e) {
  e.preventDefault();
  const q = $('#qaInput').value.trim();
  if (!q || BUSY) return;
  BUSY = true;
  $('#qaSend').disabled = true;
  $('#qaLog').querySelector('.qa-empty')?.remove();
  const deep = $('#qaDeep').checked;
  $('#qaLog').insertAdjacentHTML('beforeend', `<div class="qa-q">${esc(q)}</div><div class="qa-a qa-wait">${waitHtml({ deep })}</div>`);
  $('#qaInput').value = '';
  $('#qaLog').lastElementChild.scrollIntoView({ block: 'nearest' });
  const r = await fetch('/api/qa/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q, deep, thread: THREAD }) })
    .then(r => r.json()).catch(() => ({ error: '连不上 Ephemeris' }));
  if (!r.error && !r.started) r.error = '上一个问题还没答完';
  if (!r.error) return pollLive();
  $('#qaLog .qa-wait').outerHTML = `<div class="qa-a qa-bad">没答上：${esc(r.error)}</div>`;
  $('#qaInput').value = q;                         // 问题还给你，改一下再问
  doneSending();
}

$('#qaForm').addEventListener('submit', send);
$('#qaDeep').checked = true;                               // 默认问 Claude；去掉过勾就记住
try { if (localStorage.getItem('qaDeep') === '0') $('#qaDeep').checked = false; } catch { /* 隐私模式：不记 */ }
$('#qaDeep').addEventListener('change', e => { try { localStorage.setItem('qaDeep', e.target.checked ? '1' : '0'); } catch { /* 同上 */ } });
$('#qaInput').addEventListener('keydown', e => {           // 回车发送，Shift+回车换行（输入法选字时不算）
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#qaForm').requestSubmit(); }
});
$('#qaNew').onclick = async () => {                       // 开一段新的；以前的在下拉框里，不删
  if (BUSY) return;
  const r = await fetch('/api/qa/new', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (r) loadQA(r.thread);
  $('#qaInput').focus();
};
$('#qaThreads').addEventListener('change', e => { if (!BUSY) loadQA(+e.target.value); });
$('#qaLog').addEventListener('click', e => {
  const sup = e.target.closest('.qa-cite');
  if (sup) {                                                 // 点上标：展开出处，标出那一条
    const a = sup.closest('.qa-a'), d = a.querySelector('.qa-src');
    d.open = true;
    a.querySelectorAll('.qa-src-row').forEach(x => x.classList.toggle('hl', x.dataset.n === sup.dataset.n));
    a.querySelector(`.qa-src-row[data-n="${sup.dataset.n}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return;
  }
  const go = e.target.closest('.qa-go');
  if (go) navigate(`day/${go.dataset.d}/conv/${go.dataset.c}/msg/${go.dataset.m}`);
});
