// 浮层：回顾 / 问 / 主题 / 河流 / 数据 / 窗口与导入 / Notion / 设置 八栏。开哪栏由路由（#/panel/...）决定。
import { $ } from './util.js';
import { navigate, closeLayer } from './nav.js';
import { ensureReflect, stopReflect } from './reflect.js';
import { loadThemes } from './themes.js';
import { loadRiver } from './river.js';
import { ensureConvs, pollCSync } from './convs.js';
import { ensureNotion } from './notion.js';
import { pollExport } from './settings.js';
import { loadQA } from './qa.js';

export function openModal(pane, arg) {
  $('#overlay').classList.remove('hidden');
  showPane(pane);
  if (pane === 'themes') loadThemes(arg);
  if (pane === 'river') loadRiver();
  ensureReflect();                     // 「回顾」「数据」两栏共用，哪栏进来都先拿着
  if (pane === 'convs') { pollCSync(); ensureConvs(); }
  if (pane === 'notion') ensureNotion();
  if (pane === 'settings') pollExport();
  if (pane === 'qa') { loadQA(); setTimeout(() => $('#qaInput').focus(), 50); }
}
export function closeModal() { $('#overlay').classList.add('hidden'); stopReflect(); }
$('#reflectOpen').onclick = () => navigate('panel/reflect');
$('#convsOpen').onclick = () => navigate('panel/convs');
$('#notionOpen').onclick = () => navigate('panel/notion');
$('#settingsOpen').onclick = () => navigate('panel/settings');
$('#qaOpen').onclick = () => navigate('panel/qa');
$('#modalClose').onclick = closeLayer;
$('#overlay').addEventListener('click', e => { if (e.target.id === 'overlay') closeLayer(); });
// 面板切换本身只管样式；进哪个面板由路由决定（下面 .modal-nav-item 的 onclick 走 navigate）
function showPane(pane) {
  document.querySelectorAll('.modal-nav-item[data-pane]').forEach(x =>
    x.classList.toggle('active', x.dataset.pane === pane));
  document.querySelectorAll('.modal-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === pane));
}
document.querySelectorAll('.modal-nav-item[data-pane]').forEach(b =>
  b.onclick = () => navigate('panel/' + b.dataset.pane));
// Esc 在 main.js 统一处理（只关最上面那一层）
