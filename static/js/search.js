// 侧栏的搜索框：搜我说过的话（聊天全文 + 手写日记）。
import { $, esc } from './util.js';
import { navigate } from './nav.js';

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
