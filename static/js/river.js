// 浮层「河流」栏：话题河流图。
import { $, esc } from './util.js';

// 每周各大类占了多少「主题·天」。颜色 = 类别身份，固定顺序（已过配色校验）；叠放顺序 = 颜色顺序。
const RV_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948', '#b3aea3'];
let RV = null, RV_SCALE = 'share', RV_FROM = 'year', RV_HI = -1;
// 「近一年」= 今天往前 365 天（本地日期），每次加载现算
const fromDate = v => {
  if (v !== 'year') return v;
  const d = new Date(); d.setDate(d.getDate() - 365);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export async function loadRiver() {
  $('#rvChart').innerHTML = '<div class="pane-loading">在数…</div>';
  RV = await fetch('/api/river?from=' + fromDate(RV_FROM)).then(r => r.json());
  renderRiver();
}

// 单调三次插值（Fritsch–Carlson）：平滑但不过冲。返回分段的贝塞尔 [p0, c1, c2, p1]，
// 这样同一条边界既能正着画（上面那条带子的下沿）也能倒着画（下面那条的上沿），两边严丝合缝。
// 以前是把点倒过来再插值一次，陡的地方两次插值对不上，会裂出白缝。
function monoSegs(pts) {
  const n = pts.length, dx = [], m = [], t = [], segs = [];
  if (n < 2) return segs;
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
    if (h > 9) { const k = 3 / Math.sqrt(h); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], h = dx[i] / 3;
    segs.push([[x0, y0], [x0 + h, y0 + t[i] * h], [x1 - h, y1 - t[i + 1] * h], [x1, y1]]);
  }
  return segs;
}
const _f = p => p[0].toFixed(1) + ',' + p[1].toFixed(1);
const segsFwd = segs => segs.map(s => `C${_f(s[1])} ${_f(s[2])} ${_f(s[3])}`).join('');
const segsRev = segs => segs.slice().reverse().map(s => `C${_f(s[2])} ${_f(s[1])} ${_f(s[0])}`).join('');

function rvValues() {
  return RV.smooth.map(row => {
    if (RV_SCALE === 'count') return row;
    const s = row.reduce((a, b) => a + b, 0);
    return s < 0.34 ? null : row.map(v => v / s);      // 这周（平滑后）基本没记录：留空档，不假装各类都归零
  });
}

function renderRiver() {
  const box = $('#rvChart');
  if (!RV.weeks.length) { box.innerHTML = '<div class="pane-loading">还没有数据</div>'; return; }
  const vals = rvValues(), n = vals.length, K = RV.cats.length;
  const W = Math.max(box.clientWidth, 300), H = 400, L = 38, R = 10, T = 10, B = 26;
  const stacks = vals.map(row => { if (!row) return null; let acc = 0; return row.map(v => { const y0 = acc; acc += v; return [y0, acc]; }); });
  const max = RV_SCALE === 'share' ? 1 : Math.max(...stacks.filter(Boolean).map(s => s[K - 1][1]), 1);
  const x = i => L + (n === 1 ? 0 : i / (n - 1)) * (W - L - R);
  const y = v => T + (1 - v / max) * (H - T - B);

  // 连续有数据的几段分开画；段与段之间是没记录的空档
  const segs = [];
  stacks.forEach((s, i) => { if (!s) return; const g = segs[segs.length - 1]; if (g && g[g.length - 1] === i - 1) g.push(i); else segs.push([i]); });
  const half = (W - L - R) / Math.max(n - 1, 1) / 2;
  // 每段先算出 K+1 条边界曲线（第 k 条 = 第 k 类的下沿 = 第 k-1 类的上沿），带子用相邻两条围出来
  const bands = segs.map(g => {
    const xs = g.length === 1 ? [x(g[0]) - half, x(g[0]) + half] : g.map(x);   // 孤零零一周：画成一小截
    const at = j => g.length === 1 ? g[0] : g[j];
    const edges = [];
    for (let k = 0; k <= K; k++)
      edges.push(monoSegs(xs.map((xx, j) => [xx, y(k === 0 ? stacks[at(j)][0][0] : stacks[at(j)][k - 1][1])])));
    return RV.cats.map((c, k) => {
      const lo = edges[k], hi = edges[k + 1];
      return `<path class="rv-band" data-k="${k}" d="M${_f(hi[0][0])}${segsFwd(hi)}L${_f(lo[lo.length - 1][3])}${segsRev(lo)}Z" fill="${RV_COLORS[k]}"/>`;
    }).join('');
  }).join('');
  const gaps = segs.slice(1).map((g, j) => {
    const a = x(segs[j][segs[j].length - 1]), b = x(g[0]);
    return `<rect x="${a}" y="${T}" width="${b - a}" height="${H - T - B}" class="rv-gap"/>` +
      (b - a > 40 ? `<text x="${(a + b) / 2}" y="${T + 16}" text-anchor="middle" class="rv-ax">没记录</text>` : '');
  }).join('');

  // 纵轴：占比 0/50/100%，数量取 3 个整数刻度
  const yt = RV_SCALE === 'share' ? [0, .5, 1] : [0, Math.round(max / 2), Math.round(max)];
  const grid = yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="rv-grid"/>
    <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="rv-ax">${RV_SCALE === 'share' ? Math.round(v * 100) + '%' : v}</text>`).join('');
  // 横轴：每月第一周标一下，太挤就隔几个月
  const months = [];
  RV.weeks.forEach((w, i) => { const m = w.slice(0, 7); if (!months.length || months[months.length - 1].m !== m) months.push({ m, i }); });
  const every = Math.ceil(months.length / Math.max(1, Math.floor((W - L - R) / 46)));
  let lastX = -99;
  const xt = months.filter((_, j) => j % every === 0).filter(({ i }) => { const ok = x(i) - lastX >= 40; if (ok) lastX = x(i); return ok; }).map(({ m, i }) =>
    `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" class="rv-ax">${m.slice(2).replace('-', '.')}</text>`).join('');

  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="各类事情每周占比的变化">
    ${grid}${gaps}<g class="rv-bands">${bands}</g>${xt}
    <line id="rvCross" class="rv-cross hidden" y1="${T}" y2="${H - B}"/>
    <rect id="rvHit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;

  $('#rvLegend').innerHTML = RV.cats.map((c, k) =>
    `<span class="rv-li" data-k="${k}"><i style="background:${RV_COLORS[k]}"></i>${c}</span>`).join('');
  $('#rvLegend').querySelectorAll('.rv-li').forEach(el => {
    el.onmouseenter = () => rvEmph(+el.dataset.k); el.onmouseleave = () => rvEmph(-1);
  });
  rvEmph(RV_HI);
  $('#rvSub').textContent = `${RV.weeks[0]} → ${RV.weeks[n - 1]}，${n} 周。每周算各类事情占了多少：一件事做了一天算一份，AI 聊天和手写日记都算；前后 3 周平滑过。`;

  const hit = $('#rvHit'), tip = $('#rvTip'), cross = $('#rvCross');
  hit.onmousemove = e => {
    const r = box.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round((e.clientX - r.left - L) / (W - L - R) * (n - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.classList.remove('hidden');
    const raw = RV.raw[i], v = vals[i] || RV.cats.map(() => 0);
    const rows = RV.cats.map((c, k) => ({ c, k, v: v[k], raw: raw[k], top: RV.top[i][k] }))
      .filter(r => r.v > 0.004).reverse();
    tip.innerHTML = `<b>${RV.weeks[i]} 那周</b>` + (rows.length ? rows.map(r =>
      `<div class="rv-tr"><i style="background:${RV_COLORS[r.k]}"></i><span>${r.c}</span>
         <em>${RV_SCALE === 'share' ? Math.round(r.v * 100) + '%' : r.v.toFixed(1)}</em></div>` +
      (r.top.length ? `<div class="rv-top">${r.top.map(esc).join('、')}</div>` : '')).join('') : '<div class="dim">这周没记录</div>');
    tip.classList.remove('hidden');
    const pane = tip.parentElement.getBoundingClientRect();
    let left = e.clientX - pane.left + 14;
    if (left + 260 > pane.width) left = e.clientX - pane.left - 274;
    tip.style.left = Math.max(0, left) + 'px';
    tip.style.top = (box.offsetTop + 8) + 'px';
  };
  hit.onmouseleave = () => { tip.classList.add('hidden'); cross.classList.add('hidden'); };
  if (!$('#rvTableBox').classList.contains('hidden')) renderRiverTable();
}

function rvEmph(k) {
  RV_HI = k;
  document.querySelectorAll('.rv-band').forEach(p => p.style.opacity = k < 0 || +p.dataset.k === k ? 1 : .18);
}

// 表格视图：按月合计的占比（低对比度的几个颜色靠它补上可读性）
function renderRiverTable() {
  const by = {};
  RV.weeks.forEach((w, i) => { const m = w.slice(0, 7); by[m] = by[m] || RV.cats.map(() => 0); RV.raw[i].forEach((v, k) => by[m][k] += v); });
  $('#rvTableBox').innerHTML = `<table class="rv-table"><tr><th>月份</th>${RV.cats.map(c => `<th>${c}</th>`).join('')}</tr>` +
    Object.entries(by).map(([m, row]) => { const s = row.reduce((a, b) => a + b, 0) || 1;
      return `<tr><td>${m}</td>${row.map(v => `<td>${v ? Math.round(v / s * 100) + '%' : ''}</td>`).join('')}</tr>`; }).join('') + '</table>';
}

document.querySelectorAll('#rvScale button').forEach(b => b.onclick = () => {
  RV_SCALE = b.dataset.v; document.querySelectorAll('#rvScale button').forEach(x => x.classList.toggle('on', x === b)); if (RV) renderRiver();
});
document.querySelectorAll('#rvRange button').forEach(b => b.onclick = () => {
  RV_FROM = b.dataset.v; document.querySelectorAll('#rvRange button').forEach(x => x.classList.toggle('on', x === b)); loadRiver();
});
$('#rvTable').onclick = () => { const t = $('#rvTableBox'); t.classList.toggle('hidden'); if (!t.classList.contains('hidden') && RV) renderRiverTable(); };
window.addEventListener('resize', () => { if (RV && !$('#overlay').classList.contains('hidden')) renderRiver(); });
