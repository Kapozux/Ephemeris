// 「开始用」页：新装的 Ephemeris（一家能用的模型都没有，或者还一条聊天都没有）才出现，配好就不再出现。后端是 setup.py。
// 写给不写代码的人看：一步一件事，每步说清楚去哪点什么；填的值只写进本机的 .env，页面拿不回来。
// 10-14：模型能挑一家（DeepSeek / Claude Code / Gemini / OpenRouter / 百炼），聊天记录也有好几种来法（自动同步、拖导出文件）。
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

const LINK = (href, text) => `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;

// 每家模型：名字、一句话、怎么拿 key（或者怎么装）。key 为 null 的是 Claude Code：不用 key，看本机装没装
const PROVIDERS = [
  { id: 'deepseek', name: 'DeepSeek', tag: '推荐：只有 DeepSeek 选这个', key: 'DEEPSEEK_API_KEY', ph: 'sk- 开头的一串', how: `
    <ol><li>打开 ${LINK('https://platform.deepseek.com/api_keys', 'platform.deepseek.com/api_keys')}，用手机号或微信登录。</li>
      <li>点「创建 API key」，名字随便填，把出来的 sk- 开头那一串复制下来（只显示一次）。</li>
      <li>左边「充值」充一点（十块钱够用很久），没有余额 key 用不了。</li>
      <li>贴到下面，点保存（会先跟 DeepSeek 验一下，不花钱）。</li></ol>
    <p class="su-note">写日记、做证据卡、「问」都走它；深挖时它自己翻你的聊天记录。写一天的日记不到一毛钱。没有按意思找（DeepSeek 没有那个模型），「问」按字面找。</p>` },
  { id: 'claude', name: 'Claude Code', tag: '有 Claude 订阅', key: null, how: `
    <ol><li>装 Claude Code：Mac 打开「终端」粘贴 <code>curl -fsSL https://claude.ai/install.sh | bash</code>；
        Windows 打开 PowerShell 粘贴 <code>irm https://claude.ai/install.ps1 | iex</code>。</li>
      <li>装好后在同一个窗口输入 <code>claude</code> 回车，按提示登录你的 Claude 账号，看到对话框就可以关掉。</li>
      <li>关掉 Ephemeris 再打开（让它重新找一下 claude 在哪），回到这一页。</li></ol>
    <p class="su-note">用的是你的订阅额度，不另外花钱。</p>` },
  { id: 'gemini', name: 'Gemini', tag: 'Google', key: 'GEMINI_API_KEY', ph: 'AIza 开头的一串', how: `
    <ol><li>打开 ${LINK('https://aistudio.google.com/apikey', 'aistudio.google.com/apikey')}，用 Google 账号登录（网络要能打开 Google）。</li>
      <li>点「Create API key」，把出来的那一串复制下来。</li>
      <li>贴到下面，点保存。</li></ol>
    <p class="su-note">写一天的日记大约 1 美分，Google 按用量收。</p>` },
  { id: 'openrouter', name: 'OpenRouter', tag: '一把 key 多家模型', key: 'OPENROUTER_API_KEY', ph: 'sk-or- 开头的一串', how: `
    <ol><li>打开 ${LINK('https://openrouter.ai/keys', 'openrouter.ai/keys')}，登录后点「Create Key」，复制。</li>
      <li>在 ${LINK('https://openrouter.ai/credits', 'Credits')} 充几美元。</li>
      <li>贴到下面，点保存。</li></ol>
    <p class="su-note">日常走它上面的 DeepSeek，按意思找用它上面 Google 的向量模型。</p>` },
  { id: 'dashscope', name: '百炼 / 通义', tag: '阿里云', key: 'DASHSCOPE_API_KEY', ph: 'sk- 开头的一串', how: `
    <ol><li>打开 ${LINK('https://bailian.console.aliyun.com/?apiKey=1', '阿里云百炼控制台')}，用阿里云账号登录（第一次要开通百炼）。</li>
      <li>右上角「API-KEY」→「创建 API-KEY」，复制。</li>
      <li>贴到下面，点保存。</li></ol>
    <p class="su-note">走百炼上的 DeepSeek，按量从阿里云账户扣。</p>` },
];

function modelStep(s) {
  const have = s.providers || {}, cur = PROVIDERS.find(p => p.id === s.provider) || PROVIDERS[0];
  const chips = PROVIDERS.map(p => `
    <button type="button" class="su-prov${p.id === cur.id ? ' on' : ''}" data-prov="${p.id}" role="radio" aria-checked="${p.id === cur.id}">
      <b>${p.name}${have[p.id] ? ' <i class="su-ok">✓</i>' : ''}</b><span>${p.tag}</span></button>`).join('');
  const ready = cur.key ? have[cur.id] : have.claude;
  const state = cur.key
    ? (ready ? `<p class="su-note">已经填好了；想换一把就再贴一次。</p>` : '')
    : `<p class="${ready ? 'su-note' : 'su-bad'}">${ready ? '找到了这台电脑上的 Claude Code。' : '这台电脑上还没找到 Claude Code（装好、登录后重新打开 Ephemeris）。'}</p>`;
  return STEP(1, s.model, '选一家写日记的模型（挑一家就够）', `
    <div class="su-provs" role="radiogroup" aria-label="用哪家模型">${chips}</div>
    <div class="su-pane">${cur.how}${state}${cur.key ? field('suKey', cur.key, cur.ph) : ''}</div>`);
}

function dataStep(s, syncing, syncLine) {
  return STEP(3, s.msgs > 0, '把聊天记录放进来（用哪个 AI 就选哪个，可以都弄）', `
    <details class="su-src"${s.msgs ? '' : ' open'}><summary><b>从文件导入</b>：Claude、ChatGPT、DeepSeek、Gemini 的导出文件</summary>
      <ul>
        <li><b>DeepSeek</b>：${LINK('https://chat.deepseek.com/', 'chat.deepseek.com')} → 左下角头像 → 设置 → 数据管理 →「导出所有历史对话」，下载 .zip。</li>
        <li><b>ChatGPT</b>：chatgpt.com → 头像 → 设置 → 数据控制 →「导出数据」，邮件里下载 .zip。</li>
        <li><b>Claude</b>：claude.ai → 头像 → 设置 → 隐私 →「导出数据」，邮件里下载 .zip。</li>
        <li><b>Gemini</b>：${LINK('https://takeout.google.com/', 'Google Takeout')} 只勾「我的活动 → Gemini Apps」，格式选 JSON。</li>
      </ul>
      <p class="su-note">.zip 不用解压，一次可以选好几个。已经有的消息不会重复。</p>
      <div class="su-field"><input type="file" id="suFiles" multiple accept=".zip,.json,.md" hidden>
        <button class="btn-secondary btn-small" id="suPick">选文件导入</button><span class="su-msg" id="suFilesMsg"></span></div>
    </details>
    <details class="su-src"><summary><b>claude.ai 自动下载</b>：以后每天早上自己拉新的</summary>
      ${s.chrome ? '' : '<p class="su-bad">这台电脑上没找到 Chrome 或 Edge（自动下载要用其中一个）。装一个再回来：<a href="https://www.google.com/chrome/" target="_blank" rel="noopener">Chrome</a> 或 <a href="https://www.microsoft.com/edge" target="_blank" rel="noopener">Edge</a>。</p>'}
      <ol><li>用 Chrome 或 Edge 打开 ${LINK('https://claude.ai', 'claude.ai')}，确认已经登录了。</li>
        <li>在页面空白处点右键 →「检查」。</li>
        <li>在弹出来的那一栏，上面一排找「Application」（中文叫「应用」或「应用程序」），点它。一排里没有的话，点那一排最右边的「»」或「+」找。</li>
        <li>左边找「Cookies」→「https://claude.ai」。</li>
        <li>右边列表里找名字叫 <b>sessionKey</b> 的那一行，双击它的值（sk-ant- 开头），全选、复制，贴到下面。</li></ol>
      <p class="su-note">这一串等于你的登录，别发给别人。它过一段时间会失效，到时候再来贴一次新的。${s.claude ? '已经填好了。' : ''}</p>
      ${field('suClaude', 'CLAUDE_SESSION_KEY', 'sk-ant- 开头的一串')}
      <p>第一次会把以前所有的聊天都拉下来，要几分钟到半个小时。以后每天早上 8:30 自动拉一次（电脑开着、Ephemeris 在运行就行）。</p>
      <div class="su-field"><button class="btn-primary" id="suSync" ${!s.model || !s.claude || syncing ? 'disabled' : ''}>开始下载</button>
        <span class="su-msg">${syncLine}</span></div>
    </details>
    <details class="su-src"><summary><b>写代码的 AI</b>：Claude Code、Cursor、Codex</summary>
      <p class="su-note">装在这台电脑上的话不用设置：每天早上自动读它们存在本机的对话（只读，不改）。</p>
    </details>`);
}

function render(s) {
  const j = s.sync || {}, last = s.last_sync;
  const syncing = !!j.running;
  const syncLine = syncing
    ? `<span class="pulse"></span> ${esc(j.stage || '在拉')}${j.total ? ` ${j.done} / ${j.total}` : ''}…`
    : last && !last.ok ? `<span class="su-bad">上次没拉成：${esc(last.note || last.error || '不知道为什么')}</span>`
    : s.msgs ? `已经有 ${s.msgs.toLocaleString()} 条聊天了。` : '';
  // 拉的时候每 3 秒重画一次：贴了还没存的值、展开着的那几栏先记下，画完放回去
  const box = $('#setup');
  const keep = [...box.querySelectorAll('.su-field input:not([type=file])')].map(i => [i.id, i.value]);
  const open = [...box.querySelectorAll('details.su-src')].map(d => d.open);
  box.innerHTML = `
    <header class="pagehead"><div class="pagehead-l">
      <h1 class="serif">开始用 Ephemeris</h1>
      <div class="pagehead-meta">它会把你跟 AI 的聊天，按天写成日记。所有东西都只存在这台电脑上。照着下面做，弄好之后这一页就不再出现。</div>
    </div></header>
    <div class="su-steps">
    ${modelStep(s)}
    ${STEP(2, !!s.name, '日记里怎么称呼你（可以不填）', `
      <p class="su-note">${s.name ? `现在是「${esc(s.name)}」。` : '不填就叫「用户」。'}</p>
      ${field('suName', 'DIARY_NAME', '比如：小明', 'text')}`)}
    ${dataStep(s, syncing, syncLine)}
    </div>`;

  keep.forEach(([id, v]) => { const i = document.getElementById(id); if (i) i.value = v; });
  if (open.length) box.querySelectorAll('details.su-src').forEach((d, i) => { if (i in open) d.open = open[i]; });

  box.querySelectorAll('[data-prov]').forEach(b => b.onclick = async () => {
    const r = await post({ EPHEMERIS_PROVIDER: b.dataset.prov });
    if (r) render(r);
  });
  bindSave(box, r => (r.need ? render(r) : show(r)));
  const go = $('#suSync');
  if (go) go.onclick = async () => { go.disabled = true; await fetch('/api/claude/sync', { method: 'POST' }); refresh(); };
  const pick = $('#suPick'), files = $('#suFiles');
  if (pick) pick.onclick = () => files.click();
  if (files) files.onchange = () => importFiles(files.files);
}

// 选的导出文件 → /api/import（跟「数据来源 → 导入」同一个接口），说一声导进来多少，再刷新
async function importFiles(list) {
  const msg = $('#suFilesMsg');
  if (!list || !list.length) return;
  msg.classList.remove('su-bad');
  msg.textContent = `在导入 ${list.length} 个文件…`;
  const fd = new FormData();
  [...list].forEach(f => fd.append('file', f));
  const r = await fetch('/api/import', { method: 'POST', body: fd }).then(r => r.json()).catch(() => null);
  if (!r || !r.results) { msg.textContent = '没导进去：Ephemeris 还开着吗？'; msg.classList.add('su-bad'); return; }
  const ok = r.results.filter(x => x.ok), bad = r.results.filter(x => !x.ok);
  const n = ok.reduce((a, x) => a + (x.n_msgs_new || 0), 0);
  msg.textContent = `导进来 ${n.toLocaleString()} 条新消息` + (bad.length ? `；${bad.length} 个没认出来：${bad.map(x => x.file).join('、')}` : '');
  if (bad.length) msg.classList.add('su-bad');
  setTimeout(refresh, 1200);
}

async function post(body) {
  return fetch('/api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json()).catch(() => null);
}

// 给 root 里所有 [data-save] 按钮接上保存：只走 /api/setup（setup.save 写 .env），存好了清空框、说一声，再交给 onSaved(新状态, 存的是哪个)
export function bindSave(root, onSaved) {
  root.querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
    const input = $('#' + b.dataset.save), msg = $('#' + b.dataset.save + 'Msg'), v = input.value.trim();
    msg.classList.remove('su-bad');
    if (!v) { msg.textContent = '先贴进来再保存'; return; }
    b.disabled = true; msg.textContent = '在验、在存…';
    const r = await post({ [b.dataset.name]: v });
    b.disabled = false;
    if (!r) { msg.textContent = '没存上：Ephemeris 还开着吗？'; msg.classList.add('su-bad'); return; }
    const err = r.key_error || r.gemini_error;
    if (err) { msg.textContent = err; msg.classList.add('su-bad'); return; }
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
