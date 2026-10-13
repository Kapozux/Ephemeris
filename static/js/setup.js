// 「开始用」页：新装的 Ephemeris（没有 Gemini key，或者还一条聊天都没有）才出现，配好就不再出现。后端是 setup.py。
// 写给不写代码的人看：一步一件事，每步说清楚去哪点什么；填的值只写进本机的 .env，页面拿不回来。
import { $, esc } from './util.js';

let POLL = null;

const STEP = (n, done, title, body) => `
  <section class="su-step${done ? ' done' : ''}" data-step="${n}">
    <div class="su-num">${done ? '✓' : n}</div>
    <div class="su-main"><h3>${title}</h3>${body}</div>
  </section>`;

// 一个「贴进来 → 保存」的框。设置栏的「账号与密钥」也用它（settings.js）
export function field(id, name, placeholder, type = 'password') {
  return `<div class="su-field"><input id="${id}" type="${type}" placeholder="${placeholder}" autocomplete="off" spellcheck="false">
    <button class="btn-secondary btn-small" data-save="${id}" data-name="${name}">保存</button><span class="su-msg" id="${id}Msg"></span></div>`;
}

function render(s) {
  const j = s.sync || {}, last = s.last_sync;
  const syncing = !!j.running;
  const syncLine = syncing
    ? `<span class="pulse"></span> ${esc(j.stage || '在拉')}${j.total ? ` ${j.done} / ${j.total}` : ''}…`
    : last && !last.ok ? `<span class="su-bad">上次没拉成：${esc(last.note || last.error || '不知道为什么')}</span>`
    : s.msgs ? `已经有 ${s.msgs.toLocaleString()} 条聊天了。` : '';
  // 拉的时候每 3 秒重画一次：贴了还没存的值先记下，画完放回去
  const keep = [...$('#setup').querySelectorAll('.su-field input')].map(i => [i.id, i.value]);
  $('#setup').innerHTML = `
    <header class="pagehead"><div class="pagehead-l">
      <h1 class="serif">开始用 Ephemeris</h1>
      <div class="pagehead-meta">它会把你跟 Claude 的聊天，按天写成日记。所有东西都只存在这台电脑上。照着下面做，弄好之后这一页就不再出现。</div>
    </div></header>
    <div class="su-steps">
    ${STEP(1, s.gemini, 'Gemini key（写日记用，必须有）', `
      <ol><li>打开 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>，用 Google 账号登录。</li>
        <li>点「Create API key」，把出来的那一串复制下来。</li>
        <li>贴到下面，点保存。</li></ol>
      <p class="su-note">写一天的日记大约 1 美分，Google 按用量收。${s.gemini ? '已经填好了；想换一个就再贴一次。' : ''}</p>
      ${field('suGemini', 'GEMINI_API_KEY', 'AIza 开头的一串')}`)}
    ${STEP(2, s.claude, 'claude.ai 的登录（自动下载以前的聊天）', `
      ${s.chrome ? '' : '<p class="su-bad">这台电脑上没找到 Chrome 或 Edge（自动下载要用其中一个）。装一个再回来：<a href="https://www.google.com/chrome/" target="_blank" rel="noopener">Chrome</a> 或 <a href="https://www.microsoft.com/edge" target="_blank" rel="noopener">Edge</a>。</p>'}
      <ol><li>用 Chrome 或 Edge 打开 <a href="https://claude.ai" target="_blank" rel="noopener">claude.ai</a>，确认已经登录了。</li>
        <li>在页面空白处点右键 →「检查」。</li>
        <li>在弹出来的那一栏，上面一排找「Application」（中文叫「应用」或「应用程序」），点它。一排里没有的话，点那一排最右边的「»」或「+」找。</li>
        <li>左边找「Cookies」→「https://claude.ai」。</li>
        <li>右边列表里找名字叫 <b>sessionKey</b> 的那一行，双击它的值（sk-ant- 开头），全选、复制，贴到下面。</li></ol>
      <p class="su-note">这一串等于你的登录，别发给别人。它过一段时间会失效，到时候再来贴一次新的。${s.claude ? '已经填好了。' : ''}</p>
      ${field('suClaude', 'CLAUDE_SESSION_KEY', 'sk-ant- 开头的一串')}
      <p class="su-note">不想用自动下载也行：claude.ai 左下角头像 → 设置 → 隐私 →「导出数据」，邮件里下载那个 .zip，拖进 <a href="#/panel/convs">数据来源</a>（不用解压）。这样只有那一次的，以后不会自己更新。</p>`)}
    ${STEP(3, !!s.name, '日记里怎么称呼你（可以不填）', `
      <p class="su-note">${s.name ? `现在是「${esc(s.name)}」。` : '不填就叫「用户」。'}</p>
      ${field('suName', 'DIARY_NAME', '比如：小明', 'text')}`)}
    ${STEP(4, s.msgs > 0, '把聊天拉下来', `
      <p>第一次会把 claude.ai 上以前所有的聊天都拉下来，要几分钟到半个小时。拉完会自动写最近 7 天的日记（约 $0.07）。
        以后每天早上 8:30 自动拉一次（电脑开着、Ephemeris 在运行就行）。</p>
      <div class="su-field"><button class="btn-primary" id="suSync" ${!s.gemini || !s.claude || syncing ? 'disabled' : ''}>开始下载</button>
        <span class="su-msg">${syncLine}</span></div>`)}
    </div>`;

  keep.forEach(([id, v]) => { const i = document.getElementById(id); if (i) i.value = v; });
  bindSave($('#setup'), (r, name) => {
    if (!r.need) return show(r);       // 都好了：show 会刷新回正常的样子
    // 只更新存的这一步和「开始下载」能不能点 —— 不整页重画，别的框里贴了没存的值还在
    const n = { GEMINI_API_KEY: 1, CLAUDE_SESSION_KEY: 2, DIARY_NAME: 3 }[name];
    const done = { 1: r.gemini, 2: r.claude, 3: !!r.name }[n];
    const step = $('#setup').querySelector(`.su-step[data-step="${n}"]`);
    if (step) { step.classList.toggle('done', !!done); step.querySelector('.su-num').textContent = done ? '✓' : n; }
    const go = $('#suSync'); if (go) go.disabled = !r.gemini || !r.claude || !!r.sync?.running;
  });
  const go = $('#suSync');
  if (go) go.onclick = async () => { go.disabled = true; await fetch('/api/claude/sync', { method: 'POST' }); refresh(); };
}

// 给 root 里所有 [data-save] 按钮接上保存：只走 /api/setup（setup.save 写 .env），存好了清空框、说一声，再交给 onSaved(新状态, 存的是哪个)
export function bindSave(root, onSaved) {
  root.querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
    const input = $('#' + b.dataset.save), msg = $('#' + b.dataset.save + 'Msg'), v = input.value.trim();
    msg.classList.remove('su-bad');
    if (!v) { msg.textContent = '先贴进来再保存'; return; }
    b.disabled = true; msg.textContent = '在存…';
    const r = await fetch('/api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [b.dataset.name]: v }) }).then(r => r.json()).catch(() => null);
    b.disabled = false;
    if (!r) { msg.textContent = '没存上：Ephemeris 还开着吗？'; msg.classList.add('su-bad'); return; }
    if (r.gemini_error) { msg.textContent = r.gemini_error; msg.classList.add('su-bad'); return; }
    input.value = ''; msg.textContent = '存好了';
    onSaved(r, b.dataset.name);
  });
}

function show(s) {
  clearTimeout(POLL);
  if (!s.need) { if (!$('#setup').classList.contains('hidden')) location.reload(); return false; }   // 都好了：回到正常的样子
  $('#setup').classList.remove('hidden');
  render(s);
  if (s.sync?.running) POLL = setTimeout(refresh, 3000);
  return true;
}

async function refresh() {
  const s = await fetch('/api/setup').then(r => r.json()).catch(() => null);
  if (s) show(s);
}

// main.js 启动时调：要不要出现。出现了返回 true
export async function maybeSetup() {
  const s = await fetch('/api/setup').then(r => r.json()).catch(() => null);
  return s ? show(s) : false;
}
