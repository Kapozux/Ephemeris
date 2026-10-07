// 回忆卡：翻一张过去的某一天（随便一天 / 去年今天）。侧栏骰子或快捷键 R。
import { $, esc, feelColor } from './util.js';
import { navigate, closeLayer } from './nav.js';

let FB = null, FB_MODE = 'random';
const FB_ZH = n => n >= 365 ? `${Math.floor(n / 365)} 年${n % 365 >= 30 ? ` ${Math.floor(n % 365 / 30)} 个月` : ''}前` : n >= 60 ? `${Math.floor(n / 30)} 个月前` : `${n} 天前`;

export async function openFlashback(mode) {
  FB_MODE = mode || FB_MODE;
  $('#fb').classList.remove('hidden');
  $('#fbBody').innerHTML = '<div class="fb-loading">在翻…</div>';
  const r = await fetch('/api/flashback?mode=' + FB_MODE).then(r => r.json());
  FB = r;
  document.querySelectorAll('.fb-seg button').forEach(b => {
    b.classList.toggle('on', b.dataset.m === r.mode);
    if (b.dataset.m === 'year') { b.disabled = !r.has_year; b.title = r.has_year ? '' : '往年的今天没有记录'; }
  });
  $('#fbAgain').textContent = r.mode === 'year' ? '随便翻一张' : '再翻一张';
  const c = r.card;
  if (!c) { $('#fbBody').innerHTML = '<div class="fb-loading">还没有能翻的日子</div>'; return; }
  const feel = c.feel ? `<span class="fb-feel"><i style="background:${feelColor(c.feel.v)}"></i>${esc(c.feel.word)}</span>` : '';
  const quote = c.quote
    ? `<blockquote class="fb-q">${esc(c.quote)}</blockquote>${c.headline ? `<div class="fb-sub">那天的日记：${esc(c.headline)}</div>` : ''}`
    : c.headline ? `<blockquote class="fb-q">${esc(c.headline)}</blockquote>` : '';
  const th = c.threads.length ? `<div class="fb-sec"><h5>那时候你在弄</h5>${c.threads.map(t =>
      `<div class="fb-t"><b>${esc(t.name)}</b><span class="dim">第 ${t.nth} 天</span></div>`).join('')}</div>
    <div class="fb-sec"><h5>后来</h5>${c.threads.map(t =>
      `<div class="fb-t"><span>${esc(t.name)}：${esc(t.later)}</span></div>`).join('')}</div>` : '';
  $('#fbBody').innerHTML = `
    <div class="fb-head"><span class="fb-ago">${r.mode === 'year' ? '去年今天 · ' : ''}${FB_ZH(c.ago)}</span>
      <span class="fb-date">${c.date} 周${c.weekday}</span>${feel}</div>
    ${quote}${th}`;
}
// 开、关、换一张都走路由（#/flashback[/year]），地址和后退键跟着对得上
export function closeFlashback() { $('#fb').classList.add('hidden'); }
$('#fbOpen').onclick = () => navigate('flashback');
$('#fbClose').onclick = closeLayer;
$('#fbAgain').onclick = () => navigate('flashback');
$('#fbGo').onclick = () => { if (FB?.card) navigate('day/' + FB.card.date); };
document.querySelectorAll('.fb-seg button').forEach(b => b.onclick = () => navigate(b.dataset.m === 'year' ? 'flashback/year' : 'flashback'));
$('#fb').addEventListener('click', e => { if (e.target.id === 'fb') closeLayer(); });
// 键盘（R、Enter、Esc）在 main.js 统一处理
