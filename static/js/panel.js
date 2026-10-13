// 浮层：看（回顾 / 数据 / 主题 / 河流）和管（数据来源 / Notion / 设置）两组七栏。开哪栏由路由（#/panel/...）决定。
// 浮层里只切栏，不跳页；关掉它用右上角的 ×（#modalClose）、点外面或 Esc。
import { $ } from './util.js';
import { navigate, closeLayer } from './nav.js';
import { ensureReflect, stopReflect } from './reflect.js';
import { loadThemes } from './themes.js';
import { loadRiver } from './river.js';
import { ensureConvs, pollCSync } from './convs.js';
import { ensureNotion } from './notion.js';
import { pollExport } from './settings.js';
import './qa.js';                                       // 浮层里那栏的代码还在（老的「问」栏），入口都跳 /chat 了

// 「问」2026-10-13 起是单独的全屏聊天页（/chat）：侧栏按钮、老链接 #/panel/qa 都跳过去
const toChat = () => { location.href = '/chat'; };

export function openModal(pane, arg) {
  if (pane === 'qa') return toChat();
  $('#overlay').classList.remove('hidden');
  showPane(pane);
  if (pane === 'themes') loadThemes(arg);
  if (pane === 'river') loadRiver();
  ensureReflect();                     // 「回顾」「数据」两栏共用，哪栏进来都先拿着
  if (pane === 'convs') { pollCSync(); ensureConvs(); }
  if (pane === 'notion') ensureNotion();
  if (pane === 'settings') pollExport();
}
export function closeModal() { $('#overlay').classList.add('hidden'); stopReflect(); }
$('#reflectOpen').onclick = () => navigate('panel/reflect');
$('#convsOpen').onclick = () => navigate('panel/convs');
$('#notionOpen').onclick = () => navigate('panel/notion');
$('#settingsOpen').onclick = () => navigate('panel/settings');
$('#qaOpen').onclick = toChat;
$('#modalClose').onclick = closeLayer;
$('#overlay').addEventListener('click', e => { if (e.target.id === 'overlay') closeLayer(); });
// 面板切换本身只管样式；进哪个面板由路由决定（下面 .modal-nav-item 的 onclick 走 navigate）
function showPane(pane) {
  document.querySelectorAll('.modal-nav-item[data-pane]').forEach(x => {
    x.classList.toggle('active', x.dataset.pane === pane);
    if (x.dataset.pane === pane) x.scrollIntoView({ inline: 'center', block: 'nearest' });   // 窄屏导航横排时，当前栏滚到中间
  });
  document.querySelectorAll('.modal-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === pane));
}
document.querySelectorAll('.modal-nav-item[data-pane]').forEach(b =>
  b.onclick = () => navigate('panel/' + b.dataset.pane));
// Esc 在 main.js 统一处理（只关最上面那一层）
