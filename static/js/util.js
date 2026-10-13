// 到处都用的小工具。
export const $ = s => document.querySelector(s);
export const esc = s => (s || '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export function rel(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}

// 大数字：一万以上写「x 万」
export const fmtW = v => v >= 10000 ? (v / 10000).toFixed(v >= 100000 ? 0 : 1).replace(/\.0$/, '') + '万' : Math.round(v);

// 「过得怎么样」（-2..+2）的五档色。打卡是多条取平均，可能不是 .5 的整数倍：就近落到五档上
export function feelColor(v) { const b = v <= -1.25 ? -2 : v <= -0.25 ? -1 : v < 0.25 ? 0 : v < 1.25 ? 1 : 2; return `var(--f${b})`; }

// 全局快捷键（←/→、R、Enter）该不该响应：在输入框 / 下拉框里打字、或按着修饰键时都不该
export const typing = e => e.metaKey || e.ctrlKey || e.altKey || e.target.isContentEditable ||
  ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
// 现在最上面盖着哪一层：回忆卡 > 浮层；都没有就是 null
export const topLayer = () => !$('#fb').classList.contains('hidden') ? 'fb'
  : !$('#overlay').classList.contains('hidden') ? 'overlay' : null;

// 换天 / 换窗口后回到内容开头。宽屏是整页在滚（不是 #main），回顶；
// 1080 以下侧栏叠在上面，回顶就看到一屏日历，所以滚到内容那块的开头
export function toContent(el) {
  if (innerWidth < 1080 && el) el.scrollIntoView({ block: 'start' });
  else document.scrollingElement.scrollTo(0, 0);
}

// 只滚某个容器（日历、窗口列表），不带着整页一起动（scrollIntoView 会连整页都滚）
export function scrollWithin(box, el, center) {
  if (!box || !el || box.scrollHeight <= box.clientHeight) return;
  const b = box.getBoundingClientRect(), r = el.getBoundingClientRect(), top = r.top - b.top + box.scrollTop;
  if (center) box.scrollTop = top - (box.clientHeight - r.height) / 2;
  else if (r.top < b.top) box.scrollTop = top;
  else if (r.bottom > b.bottom) box.scrollTop = top - box.clientHeight + r.height;
}

// 把搜索词标出来：html 是已经转义过的 HTML（可以带标签，只标标签外面的字），q 是原样的词（不分大小写）
export function markHits(html, q) {
  if (!q) return html;
  const re = new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  return html.replace(/(<[^>]*>)|([^<]+)/g, (all, tag, txt) => tag || txt.replace(re, m => `<mark>${m}</mark>`));
}

// 钱：不到一分写「不到 $0.01」
export const usd = v => !v || v < 0.01 ? '不到 $0.01' : `约 $${v.toFixed(2)}`;

// 花钱或会覆盖旧东西的按钮：先在原地问一句，点「确定」才做（全应用一个写法，不用浏览器的 confirm 弹窗）。
// text 是写死的 HTML，别把用户数据直接拼进来
export function ask(btn, text, yes) {
  if (btn.nextElementSibling?.classList.contains('ask')) return;
  const box = document.createElement('span');
  box.className = 'ask';
  box.innerHTML = `${text}<a class="ask-yes">确定</a><a class="ask-no">算了</a>`;
  btn.classList.add('hidden');
  btn.after(box);
  const done = () => { box.remove(); btn.classList.remove('hidden'); };
  box.querySelector('.ask-no').onclick = e => { e.stopPropagation(); done(); };
  box.querySelector('.ask-yes').onclick = e => { e.stopPropagation(); done(); yes(); };
}
