// 浮层「问」栏：一个聊天窗口，只凭证据卡回答（后端 qa.py / cards.py）。
// 回答里的 [[卡 id]] 换成上标数字；下面列出处，每条带原话，点「去原话」回到那天那条消息。
import { $, esc, usd, ask } from './util.js';
import { navigate } from './nav.js';

let POLL = null, BUSY = false;

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
        ${esc(c.text)}<span class="qa-src-q">「${esc(c.quote)}」</span></span>
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
    box.innerHTML = `<span class="pulse"></span> 正在做证据卡 ${j.done} / ${j.total} 天${j.current ? ` · ${j.current}` : ''} · 已花 $${(j.cost || 0).toFixed(2)} · <a id="qaStop">停下</a>`;
    $('#qaStop').onclick = async () => { await fetch('/api/qa/cards/stop', { method: 'POST' }); };
    POLL = setTimeout(loadQA, 3000);
    return;
  }
  const made = c.days ? `证据卡：${c.days} 天 · ${c.n.toLocaleString()} 张（${c.first} → ${c.last}）` : '还没有证据卡';
  const todo = c.todo ? ` · 还有 ${c.todo} 天没做 · <a id="qaMake">做上（${usd(c.todo_estimate)}）</a>` : ' · 都做好了';
  box.innerHTML = made + todo + (j.error ? ` · <span class="qa-bad">上次没做完：${esc(j.error)}</span>` : '');
  const mk = $('#qaMake');
  if (mk) mk.onclick = () => ask(mk, `把这 ${c.todo} 天的聊天（你说的和 AI 说的）做成证据卡，送 Gemini，${usd(c.todo_estimate)}`, async () => {
    await fetch('/api/qa/cards', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    loadQA();
  });
}

export async function loadQA() {
  const r = await fetch('/api/qa').then(r => r.json()).catch(() => null);
  if (!r) return;
  renderCards(r.cards);
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
  $('#qaLog').insertAdjacentHTML('beforeend', `<div class="qa-q">${esc(q)}</div><div class="qa-a qa-wait"><span class="pulse"></span> 在翻卡…</div>`);
  $('#qaInput').value = '';
  $('#qaLog').lastElementChild.scrollIntoView({ block: 'nearest' });
  const r = await fetch('/api/qa/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) })
    .then(r => r.json()).catch(() => ({ error: '连不上 Ephemeris' }));
  const wait = $('#qaLog .qa-wait');
  if (r.error) {
    wait.outerHTML = `<div class="qa-a qa-bad">没答上：${esc(r.error)}</div>`;
    $('#qaInput').value = q;                         // 问题还给你，改一下再问
  } else {
    wait.outerHTML = turnHtml({ role: 'assistant', text: r.answer, cites: r.cites });
  }
  BUSY = false;
  $('#qaSend').disabled = false;
  $('#qaInput').focus();
}

$('#qaForm').addEventListener('submit', send);
$('#qaInput').addEventListener('keydown', e => {           // 回车发送，Shift+回车换行（输入法选字时不算）
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#qaForm').requestSubmit(); }
});
$('#qaClear').onclick = e => ask(e.currentTarget, '把这段对话清掉（证据卡不动）', async () => {
  await fetch('/api/qa/clear', { method: 'POST' });
  loadQA();
});
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
