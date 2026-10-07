// 路由：跟 Verbatim 同一套，用 hash（#/...）而不是 History API + 服务端路由。
// hash 从不发到服务器，刷新一个带 hash 的 URL 照样命中 Flask 唯一的 GET /，状态恢复全在前端。
//
// openDay / openWindow / openModal 这些函数本身不管路由 —— 它们「传个 id 进去，现查现渲染」。
// 别处一律调 navigate(hash)，由 hashchange → main.js 的 applyRoute() 统一分发：单一入口，不会重复渲染。
//
//   #/day/2026-09-19                        某一天
//   #/day/2026-09-19/conv/<id>[/msg/<id>[/q/<词>]]   展开某个窗口 / 滚到某条消息，标出搜的词（搜索结果用）
//   #/day/2026-09-19/journal                那天的手写日记，展开全文
//   #/flashback[/year]                      回忆卡（随便一天 / 去年今天），快捷键 R
//   #/win                                   窗口模式，还没选
//   #/win/<conv_id>                         某个窗口的时间轴
//   #/panel/reflect|themes|river|data|convs|notion|settings  浮层里的面板
//   #/panel/themes/<id>                     展开某个主题
//   /checkin[?date=]                         打卡：单独的全屏页（旧的 #/panel/checkin 会跳过去）
import { S } from './state.js';

let apply = () => {};
const current = () => location.hash.replace(/^#\/?/, '');
const isLayer = h => /^(panel|flashback)(\/|$)/.test(h);
// 浮层 / 回忆卡是在应用里点开的（占了一条历史）：关的时候退回去，这样按后退不会又把它打开
let layerPushed = false;

// main.js 启动时把 applyRoute 交进来
export function setRouter(fn) {
  apply = fn;
  window.addEventListener('hashchange', () => { if (!isLayer(current())) layerPushed = false; fn(); });
}

export function navigate(hash, { replace = false } = {}) {
  const cur = current();
  if (isLayer(hash) && isLayer(cur)) replace = true;                 // 浮层里切栏、回忆卡换一张：不另占历史
  else if (isLayer(hash) && !replace) layerPushed = true;
  if (replace) { history.replaceState(null, '', '#/' + hash); apply(); }
  else if (cur === hash) apply();                                    // 点同一个链接也要有反应
  else location.hash = '/' + hash;                                   // 触发 hashchange → apply
}

// 关掉浮层 / 回忆卡（关闭按钮、点外面、Esc 都走这里）
export function closeLayer() {
  if (layerPushed) { layerPushed = false; history.back(); }
  else navigate(mainRoute(), { replace: true });
}

// 浮层关掉之后该回到哪 —— 回当前模式下正看着的东西
export function mainRoute() {
  if (S.mode === 'win') return S.wsel ? 'win/' + S.wsel : 'win';
  return S.sel ? 'day/' + S.sel : 'day';
}
