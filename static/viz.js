// 回答里的图（2026-10-17，照 claude.ai 的内嵌图表）：深挖时 Claude 用 sql 算出数，在回答里写一个 ```html … ``` 代码块（SVG 或 Chart.js），
// 这里放进沙盒 iframe 跑。图跟着回答存在 qa_chat 里，翻回这段对话还在。
// 安全：sandbox 只给 allow-scripts（不同源：碰不到 Ephemeris 的接口、cookie 和锁）；里面再加一条 CSP：不许联网，
// 脚本只认内联的和本机的 Chart.js（static/vendor/，不从 CDN 拉）。
// 颜色：聊天页 :root 的 --text / --muted / --line / --viz-1… 读进去，系统换深浅色时整个重画；高度由 iframe 用 postMessage 报回来。
const BLOCK = /```html[^\n]*\n([\s\S]*?)(?:```|$)/g;
export const MARK = '\u0000';                                   // 正文里图的位置（esc 不碰它）

// 正文 → [去掉图的正文（图的位置是 MARK + 序号 + MARK）, 图的代码]；没写完的块（正在写）也拿出来，done=false
export function pull(text) {
  const codes = [];
  const t = (text || '').replace(BLOCK, (all, code) => {
    codes.push({ code, done: /```\s*$/.test(all) });
    return `\n\n${MARK}${codes.length - 1}${MARK}\n\n`;
  });
  return [t, codes];
}

// 拼好的 HTML 里，把记号换成图的占位（mount 再把它变成 iframe）；写到一半的只放「在画图」
export function place(html, codes, draft) {
  const one = i => {
    const c = codes[+i];
    return draft || !c.done ? '<div class="ch-viz wait">在画图…</div>'
      : `<div class="ch-viz" data-code="${c.code.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')}"></div>`;
  };
  const re = new RegExp(`<p>\\s*${MARK}(\\d+)${MARK}\\s*</p>|${MARK}(\\d+)${MARK}`, 'g');
  return html.replace(re, (_, a, b) => one(a ?? b));
}

const VARS = ['--text', '--muted', '--faint', '--line', '--box', '--accent', '--viz-1', '--viz-2', '--viz-3', '--viz-4', '--viz-5', '--viz-6'];

function doc(code) {
  const cs = getComputedStyle(document.documentElement), o = location.origin;
  const vars = VARS.map(v => `${v}:${cs.getPropertyValue(v).trim()}`).join(';');
  const chart = /\bChart\b/.test(code) ? `<script src="${o}/static/vendor/chart.umd.min.js"></script>` : '';
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' ${o}; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'">
<style>:root{${vars};color-scheme:${matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'}}
html,body{margin:0;background:transparent;color:var(--text);font:400 14px/1.5 "PingFang SC",-apple-system,sans-serif}
body{padding:2px 0}svg{max-width:100%;height:auto}</style>${chart}
<script>
window.EPH={colors:[1,2,3,4,5,6].map(i=>getComputedStyle(document.documentElement).getPropertyValue('--viz-'+i).trim()),
  css:v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim()};
if(window.Chart){Chart.defaults.color=EPH.css('--muted');Chart.defaults.borderColor=EPH.css('--line');
  Chart.defaults.font.family='"PingFang SC",-apple-system,sans-serif';Chart.defaults.animation=false;
  Chart.defaults.elements.line.borderWidth=2;Chart.defaults.elements.bar.borderRadius=4;}
const h=()=>parent.postMessage({ephViz:Math.ceil(document.documentElement.getBoundingClientRect().height)},'*');
addEventListener('load',()=>{h();new ResizeObserver(h).observe(document.body)});
addEventListener('error',e=>parent.postMessage({ephVizErr:String(e.message||e)},'*'));
</script></head><body>${code}</body></html>`;
}

// CSP 管不住 iframe 自己跳走（location=、meta refresh、点链接）—— 跳走时可以把数据拼在网址里带出去（10-19 体检）。
// 画图用不着这些：代码里有跳转 / 链接 / 嵌别的页面的一律不画；万一还是跳了（第二次 load），当场把 iframe 拿掉。
const RISKY = /location|http-equiv|\bopen\s*\(|<a\b|<form\b|<base\b|<i?frame\b|<embed\b|<object\b|<link\b|<meta\b|\bhref\s*=\s*(?!["']?#)|\bsrc\s*=/i;
const refuse = (box, why) => { delete box.dataset.code; box.classList.add('on', 'wait'); box.textContent = why; };

// 把 root 里还没跑起来的图变成 iframe；again=true 全部重画（换了深浅色）
export function mount(root, again) {
  root.querySelectorAll(again ? '.ch-viz[data-code]' : '.ch-viz[data-code]:not(.on)').forEach(box => {
    if (RISKY.test(box.dataset.code)) return refuse(box, '这张图的代码里有链接或跳转，没画（图里用不着这些）。');
    const f = document.createElement('iframe');
    f.setAttribute('sandbox', 'allow-scripts');
    f.setAttribute('title', '图');
    let loads = 0;
    f.addEventListener('load', () => { if (++loads > 1) refuse(box, '这张图想跳到别的页面，拦下了。'); });
    f.srcdoc = doc(box.dataset.code);
    box.replaceChildren(f);
    box.classList.add('on');
  });
}

addEventListener('message', e => {
  if (!e.data || typeof e.data !== 'object') return;
  const f = [...document.querySelectorAll('.ch-viz iframe')].find(x => x.contentWindow === e.source);
  if (!f) return;
  if (typeof e.data.ephViz === 'number') f.style.height = Math.min(Math.max(e.data.ephViz, 40), 1200) + 'px';
  if (e.data.ephVizErr) f.parentElement.dataset.err = e.data.ephVizErr;
});
