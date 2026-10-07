// 入口：模式切换（按天 / 按窗口）、路由分发、启动，以及跨块的事（数据变了统一重拉）。
// 各块在自己的文件里：
//   calendar 侧栏日历 · batch 补全日记 · day 某一天 · search 搜索 · flashback 回忆卡 · windows 按窗口回顾
//   panel 浮层（reflect 回顾/数据 · themes 主题 · river 河流 · convs 窗口与导入 · notion）
// 共用的状态在 state.js（S），路由约定在 nav.js 开头。
import { $, typing, topLayer } from './util.js';
import { S, setDays, daysChanged } from './state.js';
import { navigate, setRouter, closeLayer } from './nav.js';
import { buildCalendar } from './calendar.js';
import { refreshBatchButton, watchBatch } from './batch.js';
import { openDay, stepDay } from './day.js';
import { closeResults } from './search.js';
import { openModal, closeModal } from './panel.js';
import { invalidateReflect } from './reflect.js';
import { openFlashback, closeFlashback } from './flashback.js';
import { openWindow, ensureWindows } from './windows.js';

let LATEST = null;      // 冷启动落在哪天：最近一篇写好的日记

// ---------------------------------------------------------------- 模式切换

function setMode(mode) {
  S.mode = mode;
  document.querySelectorAll('.sb-mode').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  const win = mode === 'win';
  $('#cal').classList.toggle('hidden', win);
  $('#batch').classList.toggle('hidden', win || !$('#batch').dataset.want);
  document.querySelector('.sb-legend').classList.toggle('hidden', win);
  $('#winpane').classList.toggle('hidden', !win);
  $('#day').classList.toggle('hidden', win || !S.sel);
  $('#win').classList.toggle('hidden', !win || !S.wsel);
  if (win) ensureWindows();
}
document.querySelectorAll('.sb-mode').forEach(b => b.onclick = () =>
  navigate(b.dataset.mode === 'win' ? (S.wsel ? 'win/' + S.wsel : 'win') : (S.sel ? 'day/' + S.sel : 'day')));

// ---------------------------------------------------------------- 打卡入口（单独的全屏页 /checkin）
// 今天还没打卡，侧栏「打卡」上亮个小点。打开页面时看一次，切回这个标签页时再看一次。
// （以前只在补全日记的进度轮询里顺手刷新，平时根本不亮。）

async function refreshCkDot() {
  const r = await fetch('/api/checkin').then(r => r.json());
  $('#ckDot').classList.toggle('hidden', r.items.length > 0);
}
$('#ckOpen').onclick = () => { location.href = '/checkin'; };
window.addEventListener('focus', () => { refreshCkDot(); refreshJournal(); });

// ---------------------------------------------------------------- 数据变了（补全跑完、导入了新文件）

document.addEventListener('days-changed', async e => {
  const d = await fetch('/api/days').then(r => r.json());
  setDays(d.days);
  buildCalendar(); refreshBatchButton(); invalidateReflect();
  if (S.sel && (!e.detail || e.detail.includes(S.sel))) openDay(S.sel);
  else if (S.sel) document.querySelector(`.cal-day[data-d="${S.sel}"]`)?.classList.add('sel');
});

// ---------------------------------------------------------------- 手写日记：打开 / 切回页面时看一眼 Notion
// 只看最近改过的几页、不调模型；服务端两分钟内最多一次。晚上在 Notion 写完，切回来就有了

let J_LAST = 0;
async function refreshJournal() {
  if (Date.now() - J_LAST < 120000) return;
  J_LAST = Date.now();
  const r = await fetch('/api/journal/refresh', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (r?.changed?.length) daysChanged(r.changed);
}

// ---------------------------------------------------------------- 路由分发（约定见 nav.js）

function applyRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  // 兼容老的 #2026-09-19（改版前的格式）
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return navigate('day/' + raw, { replace: true });

  const parts = raw ? raw.split('/').map(decodeURIComponent) : [];
  const head = parts[0];

  if (head === 'flashback') {                          // 回忆卡：#/flashback 或 #/flashback/year
    closeModal();
    if (!S.sel && LATEST) openDay(LATEST);
    openFlashback(parts[1] === 'year' ? 'year' : 'random');
    return;
  }
  if (head === 'panel' && parts[1] === 'checkin') {      // 老链接：打卡已经挪到单独的页面
    location.href = '/checkin' + (parts[2] ? '?date=' + parts[2] : '');
    return;
  }
  closeFlashback();                   // 不是回忆卡的路由：回忆卡关着
  if (head === 'panel') {
    openModal(parts[1] || 'reflect', parts[2]);
    return;
  }
  closeModal();                       // 非 panel 路由：确保浮层是关着的

  if (head === 'win') {
    setMode('win');
    if (parts[1] && parts[1] !== S.wsel) openWindow(parts[1]);
    return;
  }

  if (head === 'day') {
    setMode('day');
    const date = parts[1];
    if (!date) return LATEST ? navigate('day/' + LATEST, { replace: true }) : undefined;
    if (!S.byDate[date]) return LATEST ? navigate('day/' + LATEST, { replace: true }) : undefined;
    const focus = {};
    if (parts[2] === 'journal') focus.journal = true;
    for (let i = 2; i < parts.length; i += 2) {
      if (parts[i] === 'conv') focus.conv = parts[i + 1];
      if (parts[i] === 'msg') focus.msg = parts[i + 1];
    }
    const hasFocus = focus.conv || focus.msg || focus.journal;
    if (date === S.sel && !hasFocus) return;      // 已经在看这天了（比如刚关掉浮层），别重拉
    openDay(date, hasFocus ? focus : undefined);
    return;
  }

  if (LATEST) navigate('day/' + LATEST, { replace: true });
}

// ---------------------------------------------------------------- 键盘（全应用只有这一处）
// 以前 day.js / flashback.js / panel.js / search.js 各挂一个，互相不知道：
// 浮层开着按 ← 会翻天并把浮层关掉，回忆卡里按 Enter 会同时「打开这天」又「再翻一张」。

function onEscape(e) {
  const t = e.target;
  if (topLayer() === 'fb') return closeLayer();
  if (!$('#results').classList.contains('hidden')) return closeResults();      // 搜索结果：第一下收起
  if (!$('#pop').classList.contains('hidden')) return $('#pop').classList.add('hidden');
  if (t.tagName === 'INPUT' && t.value) {                                        // 框里有字：先清空，不关浮层
    e.preventDefault();
    t.value = ''; t.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  if (topLayer() === 'overlay') closeLayer();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') return onEscape(e);
  if (typing(e)) return;
  const layer = topLayer(), k = e.key;
  if (layer === 'fb') {
    if (k === 'r' || k === 'R') { e.preventDefault(); navigate('flashback'); }
    if (k === 'Enter' && !e.target.closest?.('button, a')) $('#fbGo').click();     // 焦点在按钮上：让按钮自己处理
    return;
  }
  if (layer) return;                                                              // 浮层开着：←/→/R 都不管
  if (k === 'r' || k === 'R') { e.preventDefault(); navigate('flashback'); return; }
  if (S.mode === 'day' && (k === 'ArrowLeft' || k === 'ArrowRight')) { e.preventDefault(); stepDay(k === 'ArrowLeft' ? -1 : 1); }
});

// ---------------------------------------------------------------- 启动

(async function init() {
  const d = await fetch('/api/days').then(r => r.json());
  setDays(d.days);
  buildCalendar();
  refreshBatchButton();
  refreshCkDot();
  await watchBatch();
  // 冷启动交给路由：hash 里有什么就恢复什么，没有就落到最近一天
  LATEST = d.latest;
  setRouter(applyRoute);
  // 刚打开页面时，地址里如果是浮层（面板 / 回忆卡），不要直接弹出来：
  // 那是上次关页面时停在那儿留下的（浏览器恢复标签页也会带着），打开应用第一眼应该是日记。
  // 应用里面点过去的面板链接不受影响 —— 这里只管冷启动这一次。
  const head0 = location.hash.replace(/^#\/?/, '').split('/')[0];
  if (head0 === 'panel' || head0 === 'flashback') history.replaceState(null, '', '#/');
  applyRoute();
  refreshJournal();                    // 先把页面画出来，再悄悄看一眼 Notion
})();
