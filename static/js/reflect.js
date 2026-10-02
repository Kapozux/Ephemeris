// 浮层里的「回顾」和「数据」两栏：同一个时间范围（/api/reflect），一个有模型写的叙事，一个纯统计。
import { $, esc, fmtW } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';

const SHADES = ['#A9502B', '#C4764F', '#D99A78', '#E9BFA6', '#B7AEA0', '#D3CEC4', '#E5E1D8', '#EFECE5', '#F4F2ED'];
const WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
let RDATA = null, RMETRIC = 'msgs', RPOLL = null, RREQ = 0;

function niceCeil(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const k of [1, 2, 3, 4, 5, 6, 8, 10]) if (k * p >= v) return k * p;
  return 10 * p;
}
function monotonePath(xs, ys) {
  const n = xs.length; if (n < 2) return '';
  const d = [], m = [];
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = (d[i - 1] * d[i] <= 0) ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], h = Math.hypot(a, b);
    if (h > 3) { m[i] = 3 * a / h * d[i]; m[i + 1] = 3 * b / h * d[i]; }
  }
  let p = `M${xs[0].toFixed(1)},${ys[0].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const dx = (xs[i + 1] - xs[i]) / 3;
    p += ` C${(xs[i] + dx).toFixed(1)},${(ys[i] + m[i] * dx).toFixed(1)} ${(xs[i + 1] - dx).toFixed(1)},${(ys[i + 1] - m[i + 1] * dx).toFixed(1)} ${xs[i + 1].toFixed(1)},${ys[i + 1].toFixed(1)}`;
  }
  return p;
}
const rDate = iso => { const [y, m, d] = iso.split('-').map(Number); return `${m}月${d}日`; };

// 长区间按周汇总：90 天按天画是一排锯齿，看不出走势（Claude 自己的 Reflect 也是按周）
const WEEKLY_AFTER = 60;
function toWeeks(series) {
  const out = [];
  series.forEach(p => {
    const d = new Date(p.date + 'T12:00:00'), wd = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - wd);
    const wk = d.toISOString().slice(0, 10);
    const last = out[out.length - 1];
    if (last && last.date === wk) { last.msgs += p.msgs || 0; last.chars += p.chars || 0; last.threads += p.threads || 0; }
    else out.push({ date: wk, msgs: p.msgs || 0, chars: p.chars || 0, threads: p.threads || 0 });
  });
  return out;
}
const chartWeekly = series => series.length > WEEKLY_AFTER;

function reflectChart(series, prev, metric) {
  const weekly = chartWeekly(series);
  if (weekly) { series = toWeeks(series); prev = toWeeks(prev); }
  const W = 900, H = 250, L = 46, R = 12, T = 18, B = 34;
  const cur = series.map(p => p[metric]); const pv = prev.map(p => p[metric]);
  if (cur.length < 2) return '<div class="pane-empty">数据不够画图</div>';
  const maxV = niceCeil(Math.max(...cur, ...pv, 1));
  const X = i => L + i / (cur.length - 1) * (W - L - R), Y = v => H - B - v / maxV * (H - T - B);
  const xs = cur.map((_, i) => X(i)), pxs = pv.map((_, i) => X(i + (cur.length - pv.length)));
  const grid = [maxV, maxV * 0.6].map(v => `<line class="chart-grid" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="chart-ylab" x="${L - 12}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${fmtW(v)}</text>`).join('');
  const base = `<line class="chart-base" x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}"/><text class="chart-ylab" x="${L - 12}" y="${Y(0) + 4}" text-anchor="end">0</text>`;
  const n = cur.length - 1, idxs = [0, Math.round(n / 3), Math.round(n * 2 / 3), n];
  const xl = idxs.map((i, k) => `<text class="chart-xlab" x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="${k === 0 ? 'start' : k === 3 ? 'end' : 'middle'}">${rDate(series[i].date)}${weekly ? '那周' : ''}</text>`).join('');
  const pp = pv.length >= 2 ? `<path class="chart-prev" d="${monotonePath(pxs, pv.map(Y))}"/>` : '';
  return `<svg class="reflect-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${base}${xl}${pp}<path class="chart-cur" d="${monotonePath(xs, cur.map(Y))}"/></svg>`;
}

function delta(cur, prev) {
  if (!prev) return '';
  const p = Math.round((cur - prev) / prev * 100);
  if (!p) return '<small>持平</small>';
  return `<small class="${p > 0 ? 'up' : 'down'}">${p > 0 ? '+' : ''}${p}%</small>`;
}

function renderReflect() {
  const d = RDATA, box = $('#reflectBody'); if (!d) return;
  const t = d.totals;
  if (!t.msgs) { box.innerHTML = '<div class="pane-empty">这段时间没有聊天记录。</div>'; return; }
  const noDiary = !t.diaries;
  const headline = noDiary ? '这段时间的日记还没生成' : (d.generated ? d.headline : (d.regenerating ? '正在写这段时间的回顾…' : ''));
  const narrative = noDiary ? '先在侧栏点「补全日记」，回顾是基于日记写的。' : (d.generated ? d.narrative : '');
  const topics = d.topics || [];
  const bar = topics.map((x, i) => `<i style="flex:${x.percent};background:${SHADES[Math.min(i, SHADES.length - 1)]}"></i>`).join('');
  const list = topics.map((x, i) => `<div class="reflect-topic"><span class="reflect-topic-dot" style="background:${SHADES[Math.min(i, SHADES.length - 1)]}"></span><span class="reflect-topic-name">${esc(x.name)}</span><span class="reflect-topic-pct">${x.percent}%</span><div class="reflect-topic-desc">${esc(x.desc || `出现在 ${x.count} 天的日记里`)}</div></div>`).join('');
  box.innerHTML = `
    ${d.regenerating ? '<div class="reflect-updating"><i></i>正在重写这段时间的回顾，先给你看上一版</div>' : ''}
    <div class="reflect-headline">${esc(headline)}</div>
    ${narrative ? `<p class="reflect-narrative${d.generated ? '' : ' draft'}">${esc(narrative)}</p>` : ''}
    ${d.generated && d.generated_at ? `<div class="reflect-when">这段话写于 ${rDate(d.generated_at.slice(0, 10))}；有新东西的话每周重写一次，下面的数字和图是实时的</div>` : ''}
    <div class="pane-kpis">
      <div class="pane-kpi"><b class="text">${d.most_active_weekday == null ? '—' : WD[d.most_active_weekday]}</b><span>最常聊的一天</span></div>
      <div class="pane-kpi"><b>${d.peak_hour == null ? '—' : d.peak_hour + ':00'}</b><span>最活跃的钟点</span></div>
      <div class="pane-kpi"><b>${t.days}</b><span>有聊天的天数</span></div>
      <div class="pane-kpi"><b>${t.msgs.toLocaleString()}${delta(t.msgs, t.prev_msgs)}</b><span>我发的消息</span></div>
      <div class="pane-kpi"><b>${fmtW(t.chars)}${delta(t.chars, t.prev_chars)}</b><span>我打的字</span></div>
      <div class="pane-kpi"><b>${t.multi_window_days}<small>/ ${t.days} 天</small></b><span>同一天跨多个窗口</span></div>
    </div>
    <div class="pane-sec">
      <div class="pane-sec-head"><span class="pane-sec-label">${chartWeekly(d.series) ? '每周的量' : '每天的量'}</span>
        <span class="reflect-seg">${['msgs', 'chars', 'threads'].map(k => `<button type="button" data-m="${k}" class="${RMETRIC === k ? 'active' : ''}">${{ msgs: '消息', chars: '字数', threads: '窗口' }[k]}</button>`).join('')}</span></div>
      <div id="rchart">${reflectChart(d.series, d.prev_series || [], RMETRIC)}</div>
      <div class="pane-legend"><span><i></i>这段时间</span>${(d.prev_series || []).length ? '<span><i class="prev"></i>上一段同长</span>' : ''}</div>
    </div>
    ${topics.length ? `<div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">在忙什么</span><span class="pane-sub">按日记里的话题标签算</span></div><div class="reflect-bar">${bar}</div><div class="reflect-topics">${list}</div></div>` : ''}`;
  box.querySelectorAll('.reflect-seg button').forEach(b => b.onclick = () => {
    RMETRIC = b.dataset.m;
    box.querySelectorAll('.reflect-seg button').forEach(x => x.classList.toggle('active', x === b));
    $('#rchart').innerHTML = reflectChart(d.series, d.prev_series || [], RMETRIC);
  });
}

function renderData() {
  const d = RDATA, box = $('#dataBody'); if (!d) return;
  const t = d.totals;
  const hmax = Math.max(...d.hour_counts, 1), wmax = Math.max(...d.weekday_counts, 1);
  const hours = d.hour_counts.map((v, h) => `<i style="height:${Math.max(2, v / hmax * 100)}%" class="${v < hmax * .25 ? 'lo' : ''}" title="${h}:00  ${v} 条"></i>`).join('');
  const hx = d.hour_counts.map((_, h) => `<span>${h % 3 === 0 ? h : ''}</span>`).join('');
  const wds = d.weekday_counts.map((v, i) => `<i style="height:${Math.max(2, v / wmax * 100)}%" title="${WD[i]} ${v} 条"></i>`).join('');
  const g = t.gemini_chars || 0, c = t.chars - g;
  const moods = (d.moods || []).map(([m, n]) => `<span class="data-pill">${esc(m)}<b>${n}</b></span>`).join('') || '<span class="pane-sub">还没有日记</span>';
  const longs = (d.long_conversations || []).map(x => `<div class="pane-row"><span class="pane-row-title"><a data-open="${x.b}" data-conv="${x.id}">${esc(x.title)}</a></span><span class="pane-row-meta">${x.a.slice(5)} → ${x.b.slice(5)}</span><span class="pane-row-meta">${x.days} 天 · ${x.msgs} 条</span></div>`).join('') || '<div class="pane-sub">这段时间没有跨天的窗口</div>';
  const busy = (d.busiest || []).map(x => `<div class="pane-row"><span class="pane-row-title"><a data-open="${x.date}">${x.date}${S.byDate[x.date]?.headline ? '　' + esc(S.byDate[x.date].headline) : ''}</a></span><span class="pane-row-meta">${x.threads} 个窗口</span><span class="pane-row-meta">${fmtW(x.chars)} 字</span></div>`).join('');
  box.innerHTML = `
    <div class="pane-kpis">
      <div class="pane-kpi"><b>${t.threads}</b><span>窗口·天（同一窗口每天算一次）</span></div>
      <div class="pane-kpi"><b>${t.days ? (t.threads / t.days).toFixed(1) : '—'}</b><span>平均每天几个窗口</span></div>
      <div class="pane-kpi"><b>${t.diaries}<small>/ ${t.days}</small></b><span>写了日记的天</span></div>
      <div class="pane-kpi"><b>${t.artifacts}</b><span>日记里记的「做出来的」</span></div>
      <div class="pane-kpi"><b>${t.open_loops}</b><span>「没做完的」</span></div>
    </div>
    <div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">一天里什么时候在聊</span><span class="pane-sub">按消息条数，凌晨 4 点前算前一天</span></div>
      <div class="data-bars">${hours}</div><div class="data-bars-x">${hx}</div></div>
    <div class="pane-two" style="margin-top:34px">
      <div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">一周里哪天</span></div><div class="data-bars wd">${wds}</div><div class="data-bars-x wd">${WD.map(x => `<span>${x}</span>`).join('')}</div></div>
      <div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">日记里的情绪词</span></div><div class="data-pills">${moods}</div>
        <div class="pane-sec-head" style="margin-top:22px"><span class="pane-sec-label">来源</span></div>
        <div class="data-split"><i style="flex:${c};background:var(--claude)" title="Claude ${fmtW(c)} 字"></i><i style="flex:${g};background:var(--gemini)" title="Gemini ${fmtW(g)} 字"></i></div>
        <div class="pane-legend" style="margin-top:8px"><span><i style="border-color:var(--claude)"></i>Claude ${fmtW(c)}</span><span><i style="border-color:var(--gemini)"></i>Gemini ${fmtW(g)}</span></div></div>
    </div>
    <div class="pane-two" style="margin-top:34px">
      <div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">跨天最久的窗口</span></div><div class="pane-list">${longs}</div></div>
      <div class="pane-sec"><div class="pane-sec-head"><span class="pane-sec-label">字最多的几天</span></div><div class="pane-list">${busy}</div></div>
    </div>`;
  box.querySelectorAll('a[data-open]').forEach(a => a.onclick = () =>
    navigate('day/' + a.dataset.open + (a.dataset.conv ? '/conv/' + a.dataset.conv : '')));
}

async function loadReflect(refresh) {
  const my = ++RREQ; clearTimeout(RPOLL);
  const rk = $('#reflectRange').value;
  const d = await fetch(`/api/reflect?range=${rk}${refresh ? '&refresh=1' : ''}`).then(r => r.json());
  if (my !== RREQ) return;
  RDATA = d; renderReflect(); renderData();
  if (d.regenerating) RPOLL = setTimeout(() => loadReflect(false), 4000);
}

export const ensureReflect = () => { if (!RDATA) loadReflect(false); };
export const invalidateReflect = () => { RDATA = null; };       // 数据变了：下次打开重拿
export const stopReflect = () => clearTimeout(RPOLL);            // 浮层关了就别再轮询

$('#reflectRange').onchange = () => { RDATA = null; loadReflect(false); };
$('#reflectRefresh').onclick = () => loadReflect(true);
