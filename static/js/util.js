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
