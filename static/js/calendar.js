// 侧栏的日历：一个月一格子。颜色三档：活跃（窗口数）/ 过得（-2..+2）/ 卡得（0..4）。
// 「活跃」编码的是窗口数，不是字数：字数最高的天往往是复制粘贴日，没信息量。
import { $, esc, feelColor } from './util.js';
import { S } from './state.js';
import { navigate } from './nav.js';

function heat(threads) {
  if (!threads) return 'var(--h0)';
  return threads < 2 ? 'var(--h1)' : threads < 5 ? 'var(--h2)' : threads < 10 ? 'var(--h3)' : 'var(--h4)';
}

let CMODE = 'act';
try { CMODE = localStorage.getItem('cmode') || 'act'; } catch (e) { /* 无痕模式 */ }
function cellColor(info) {
  if (CMODE === 'feel') return info.feel == null ? 'transparent' : feelColor(info.feel);
  if (CMODE === 'stuck') return info.stuck == null ? 'transparent' : `var(--s${info.stuck})`;
  return heat(info.threads);
}
function renderLegend() {
  const sw = c => `<i class="sb-swatch" style="background:${c}"></i>`;
  $('#legend').innerHTML = CMODE === 'feel'
    ? `<span>糟</span>${['--f-2', '--f-1', '--f0', '--f1', '--f2'].map(v => sw(`var(${v})`)).join('')}<span>好</span><span class="sp"></span><i class="sb-swatch empty"></i><span>没手写</span>`
    : CMODE === 'stuck'
    ? `<span>顺</span>${[0, 1, 2, 3, 4].map(v => sw(`var(--s${v})`)).join('')}<span>崩</span><span class="sp"></span><i class="sb-swatch empty"></i><span>没聊天</span>`
    : `<span>少</span>${[1, 2, 3, 4].map(v => sw(`var(--h${v})`)).join('')}<span>多</span><span class="sp"></span><i class="sb-swatch jr"></i><span>手写</span><i class="sb-swatch faded"></i><span>还没日记</span>`;
}
document.querySelectorAll('.sb-color').forEach(b => {
  b.classList.toggle('active', b.dataset.c === CMODE);
  b.onclick = () => {
    CMODE = b.dataset.c;
    try { localStorage.setItem('cmode', CMODE); } catch (e) { /* 无痕模式 */ }
    document.querySelectorAll('.sb-color').forEach(x => x.classList.toggle('active', x === b));
    buildCalendar(); if (S.sel) document.querySelector(`.cal-day[data-d="${S.sel}"]`)?.classList.add('sel');
  };
});

// ---------------------------------------------------------------- 窄屏（≤1079）：只摊开一个月
// 侧栏在窄屏叠在正文上面，整本日历摊开要一千多像素，日记被压到很下面。
// 所以窄屏默认只摊开一个月（正看着的那天所在的月），‹ › 换月，点月份摊开全部 / 收起。
// 宽屏不受影响：.cal.folded、.cal-month.cur、.cal-bar 的样式都只写在 1079 的断点里。
// 摊开还是收着是每个人自己的界面偏好，记在 localStorage（读写都包 try）。
let MONTHS = [];          // 有数据的月，从早到晚
let VIEW = null;          // 收着时摊开的是哪个月
let ALL = false;
try { ALL = localStorage.getItem('cal-all') === '1'; } catch (e) { /* 无痕模式 */ }

function renderBar() {
  $('#cal').classList.toggle('folded', !ALL);
  document.querySelectorAll('.cal-month').forEach(m => m.classList.toggle('cur', m.id === 'm-' + VIEW));
  const i = MONTHS.indexOf(VIEW);
  $('#calBarM').innerHTML = ALL ? `${MONTHS.length} 个月<span>收起</span>` : `${VIEW || ''}<span>全部月份</span>`;
  $('#calBarM').title = ALL ? '只摊开一个月' : '摊开全部月份';
  document.querySelectorAll('.cal-bar-step').forEach(b => {
    b.hidden = ALL;                                  // 全部摊开时没有「上个月 / 下个月」可言
    b.disabled = b.dataset.step < 0 ? i <= 0 : i < 0 || i >= MONTHS.length - 1;
  });
}
// 收着时摊开哪个月：打开某天时跟着那天走
export function viewMonth(date) {
  const ym = date && date.slice(0, 7);
  if (ym && MONTHS.includes(ym) && ym !== VIEW) { VIEW = ym; renderBar(); }
}
$('#calBar').onclick = e => {
  const b = e.target.closest('button'); if (!b || b.disabled) return;
  if (b.dataset.step) VIEW = MONTHS[MONTHS.indexOf(VIEW) + Number(b.dataset.step)] || VIEW;
  else {
    ALL = !ALL;
    try { localStorage.setItem('cal-all', ALL ? '1' : '0'); } catch (e) { /* 无痕模式 */ }
  }
  renderBar();
};

export function buildCalendar() {
  renderLegend();
  const months = [];
  S.days.forEach(d => {
    const ym = d.date.slice(0, 7);
    if (!months.length || months[months.length - 1] !== ym) months.push(ym);
  });
  MONTHS = [...months].sort();
  if (!MONTHS.includes(VIEW)) VIEW = (S.sel && MONTHS.includes(S.sel.slice(0, 7))) ? S.sel.slice(0, 7) : MONTHS[MONTHS.length - 1] || null;
  $('#cal').innerHTML = months.map(ym => {
    const [y, mo] = ym.split('-').map(Number);
    const pad = (new Date(y, mo - 1, 1).getDay() + 6) % 7;   // 周一开头
    const last = new Date(y, mo, 0).getDate();
    let cells = '<i class="cal-day"></i>'.repeat(pad);
    for (let d = 1; d <= last; d++) {
      const date = `${ym}-${String(d).padStart(2, '0')}`;
      const info = S.byDate[date];
      if (!info) { cells += '<i class="cal-day"></i>'; continue; }
      const onlyJ = !info.threads && info.journal;
      const tip = onlyJ ? `${date}　只有手写日记 · ${info.journal.toLocaleString()} 字`
        : `${date}　${info.threads} 个窗口 · ${info.msgs} 条` +
          (info.journal ? ` · 手写 ${info.journal.toLocaleString()} 字` : '') +
          (info.headline ? `\n${info.headline}` : '\n（还没有日记）');
      const act = CMODE === 'act';
      const cls = (!act || info.has_diary || onlyJ ? '' : 'nodiary') + (act && info.journal ? ' jr' : '') +
                  (cellColor(info) === 'transparent' ? ' blank' : '');
      const sig = (info.feel != null ? `\n过得 ${info.feel > 0 ? '+' : ''}${info.feel}` : '') +
                  (info.stuck != null ? `　卡得 ${info.stuck}/4` : '');
      cells += `<i class="cal-day on ${cls}" data-d="${date}"
                   style="background:${cellColor(info)}" title="${esc(tip + sig)}"></i>`;
    }
    return `<div class="cal-month${ym === VIEW ? ' cur' : ''}" id="m-${ym}"><h4>${ym.slice(2).replace('-', '.')}</h4>
              <div class="cal-grid">${cells}</div></div>`;
  }).join('');
  renderBar();
  $('#cal').onclick = e => { const c = e.target.closest('.cal-day.on'); if (c) navigate('day/' + c.dataset.d); };
}

// 某天的日记刚写好：格子上「还没日记」的标记去掉
export function markDiary(date) {
  const i = S.byDate[date]; if (i) i.has_diary = true;
  document.querySelector(`.cal-day[data-d="${date}"]`)?.classList.remove('nodiary');
}
