// 浮层「设置」栏：账号与密钥（写 .env，跟「开始用」页同一套：setup.save），导出（export.py）。
// 完整导出：所有聊天原文 + 每周日记 + 图片 + 主题，不调模型；交接包：请大模型（Claude Opus 4.6）写给新 AI 账号的三份文件。
import { $, esc, rel, ask } from './util.js';
import { field, bindSave } from './setup.js';

// ---------------------------------------------------------------- 账号与密钥
// 页面拿不回原值，只显示「填了没有」。sessionKey 过期是最常见的：数据来源栏的同步报错会链到这里。
const KEYS = [
  ['setClaude', 'CLAUDE_SESSION_KEY', 'claude.ai 的 sessionKey', 'sk-ant- 开头的一串', 'password',
    '每天自动拉新对话用，等于你的登录，别发给别人。过一段时间会失效：同步报错时在这里贴一个新的。'
    + '取法：Chrome 打开 claude.ai（已登录）→ 右键「检查」→ Application → Cookies → https://claude.ai → sessionKey 那一行的值。',
    s => s.claude ? '已填' : '没填：claude.ai 不会自动同步', s => s.claude],
  ['setDeepseek', 'DEEPSEEK_API_KEY', 'DeepSeek API key', 'sk- 开头的一串', 'password',
    '只有 DeepSeek 时它就是主力；有 Claude 时是省钱的后备。在 <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener">platform.deepseek.com</a> 拿，存之前先跟 DeepSeek 验一下（不花钱）。',
    s => s.providers?.deepseek ? '已填' : '没填', s => s.providers?.deepseek],
  ['setGemini', 'GEMINI_API_KEY', 'Gemini API key', 'AIza 开头的一串', 'password',
    'Claude 用不了时的后备模型；涉政的内容只给它。在 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a> 拿，存之前先跟 Google 验一下（不花钱）。',
    s => s.providers?.gemini ? '已填' : '没填', s => s.providers?.gemini],
  ['setName', 'DIARY_NAME', '日记里怎么称呼你', '比如：小明', 'text',
    '不填就叫「用户」。只影响以后写的日记。',
    s => s.name ? `现在是「${esc(s.name)}」` : '没填', s => s.name],
];

function keysStatus(s) {
  for (const [id, , , , , , st, ok] of KEYS) {
    const el = $(`#${id}St`); if (!el) continue;
    el.innerHTML = st(s);
    el.classList.toggle('ok', !!ok(s));
  }
}

export async function pollKeys() {
  const box = $('#keysBox'); if (!box) return;
  if (!box.firstElementChild) {          // 骨架只画一次：之后只改状态，贴了没存的值不会被清掉
    box.innerHTML = KEYS.map(([id, name, title, ph, type, what]) => `
      <div class="set-row">
        <span class="set-text"><b>${title}</b><span class="set-key-st" id="${id}St"></span>
          <span class="set-what">${what}</span></span>
        ${field(id, name, ph, type)}
      </div>`).join('') +
      '<div class="set-note">只写进这台电脑上的 ephemeris/.env，页面上看不到原值。Notion 的 token 也在 .env 里（NOTION_TOKEN=），见 Notion 栏。</div>';
    bindSave(box, keysStatus);
  }
  const s = await fetch('/api/setup').then(r => r.json()).catch(() => null);
  if (s) keysStatus(s);
}

// ---------------------------------------------------------------- 隐私锁（lock.py / static/lock.js）
// 设了密码：页面一切走就锁上，回来要输密码。改 / 去掉都要先输原来的密码。
export async function pollLock() {
  const box = $('#lockBox'); if (!box) return;
  const s = await fetch('/api/lock/status').then(r => r.json()).catch(() => null);
  if (!s) return;
  const pw = (id, ph, ac) => `<input id="${id}" class="set-pw" type="password" placeholder="${ph}" autocomplete="${ac}">`;
  box.innerHTML = `
    <div class="set-row">
      <span class="set-text"><b>切走就上锁</b><span class="set-key-st${s.on ? ' ok' : ''}">${s.on ? '开着' : '没开'}</span>
        <span class="set-what">设了密码以后，这个页面一被切走（换标签、最小化、点了别的应用或窗口、新开一个 Ephemeris）就锁上，回来要输密码或按 Touch ID；一直开着看就不打扰。
          锁着的时候接口也不给数据。忘了密码：在 ephemeris 目录运行 <code>python3 lock.py --reset</code>。</span>
        <span class="set-res" id="lockMsg" role="status"></span></span>
      <span class="set-act">${s.on ? pw('lockOld', '原来的密码', 'current-password') : ''}${pw('lockNew', s.on ? '新密码（换一个）' : '设一个密码', 'new-password')}
        <button class="btn-secondary btn-small" data-lk="set">${s.on ? '改密码' : '设好'}</button>
        ${s.on ? '<button class="btn-secondary btn-small" data-lk="off">去掉锁</button>' : ''}</span>
    </div>${s.on && s.native ? `
    <div class="set-row">
      <span class="set-text"><b>用 Touch ID 解锁</b><span class="set-key-st ok">开着</span>
        <span class="set-what">用这台 Mac 自己的指纹框（跟 sudo 那个一样），不用登记：锁屏一出来、从别处回来就弹，按一下就开；取消了照样能输密码。</span></span>
    </div>` : s.on ? `
    <div class="set-row">
      <span class="set-text"><b>用 Touch ID 解锁</b><span class="set-key-st${s.touch ? ' ok' : ''}">${s.touch ? '开着' : '没开'}</span>
        <span class="set-what">锁屏上点指纹（或者密码框空着按回车）才弹系统的指纹框，不会自己弹；取消了照样能输密码。要从 <code>localhost</code> 打开（127.0.0.1 用不了）。</span>
        <span class="set-res" id="touchMsg" role="status"></span></span>
      <span class="set-act">${s.touch ? '<button class="btn-secondary btn-small" data-tid="off">去掉 Touch ID</button>'
        : '<button class="btn-secondary btn-small" data-tid="on">加上 Touch ID</button>'}</span>
    </div>` : ''}`;
  const msg = $('#lockMsg');
  box.querySelectorAll('button[data-lk]').forEach(b => b.onclick = async () => {
    const off = b.dataset.lk === 'off', nw = off ? '' : $('#lockNew').value;
    msg.classList.remove('bad');
    if (!off && !nw) { msg.textContent = '先输一个密码'; $('#lockNew').focus(); return; }
    b.disabled = true;
    const r = await fetch('/api/lock/password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old: $('#lockOld')?.value || '', new: nw }) }).then(r => r.json()).catch(() => null);
    b.disabled = false;
    if (!r || !r.ok) { msg.textContent = r?.error || '没存上：Ephemeris 还开着吗？'; msg.classList.add('bad'); return; }
    window.ephLock?.set(r.on);
    await pollLock();
    $('#lockMsg').textContent = r.on ? '设好了：切到别的标签再回来试试' : '锁去掉了';
  });
  box.querySelectorAll('button[data-tid]').forEach(b => b.onclick = async () => {
    const tm = $('#touchMsg'), lk = window.ephLock;
    tm.classList.remove('bad');
    if (b.dataset.tid === 'on' && !lk?.canTouch()) { tm.textContent = '要从 http://localhost:5055 打开才能用 Touch ID'; tm.classList.add('bad'); return; }
    b.disabled = true;
    const r = b.dataset.tid === 'on' ? await lk.addTouch() : await lk.removeTouch();
    b.disabled = false;
    if (!r.ok) { tm.textContent = r.error; tm.classList.add('bad'); return; }
    await pollLock();
    $('#touchMsg').textContent = b.dataset.tid === 'on' ? '加好了：点一下别的窗口再回来，在锁屏上点指纹试试' : 'Touch ID 去掉了';
  });
}

// ---------------------------------------------------------------- 导出

let POLL = null, ESTMR = null, LAST = null;
const nice = m => (m || '').split('/').pop().replace('[1m]', ' 1M').replace(/-/g, ' ').replace(/\b\w/g, x => x.toUpperCase());
const local = m => !String(m || '').includes('/');      // 交接包走本机 Claude（订阅额度）还是 OpenRouter（按量花钱）

function skeleton(box) {
  box.innerHTML = `
    <div class="set-row">
      <span class="set-text"><b>完整导出</b>
        <span class="set-what">所有聊天原文（每个窗口一个 .md，带目录）、每周的日记（AI 写的 + 手写 + 打卡）、日记图片、主题时间线。不调模型，几秒钟。</span>
        <span class="set-res" id="setFullRes"></span></span>
      <span class="set-act" id="setFullAct"><button class="btn-secondary btn-small" data-k="full">导出</button></span>
    </div>
    <div class="set-row">
      <span class="set-text"><b>给新账号的交接包</b>
        <span class="set-what" id="setHoWhat"></span>
        <span class="set-res" id="setHoRes"></span></span>
      <span class="set-act" id="setHoAct"><input id="exportSkip" class="set-skip" placeholder="跳过的话题，如：感情"><button class="btn-secondary btn-small" data-k="handoff">请 Opus 写</button></span>
    </div>
    <div class="set-note">导到「下载」里的 Ephemeris导出-日期 文件夹，同一天再导会另起一个，不覆盖。交接包里有私事，上传前先看一遍、删改。</div>`;
  const run = async k => {
    const skip = k === 'handoff' ? $('#exportSkip').value : '';
    await fetch(`/api/export/${k}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skip }) });
    pollExport();
  };
  box.querySelectorAll('button[data-k]').forEach(b => b.onclick = () => {
    if (b.dataset.k !== 'handoff' || !LAST) return run(b.dataset.k);
    // 唯一一处把日记发给 Gemini 以外的服务，又要花差不多一美元：点之前说清楚发什么、发给谁
    const n = LAST.sends;
    ask(b, local(LAST.model) ? `把 ${n.diaries} 篇日记、${n.themes} 个主题交给本机的 ${nice(LAST.model)}（用 Claude 订阅额度，不另花钱）`
      : `把 ${n.diaries} 篇日记、${n.themes} 个主题发给 OpenRouter 上的 ${nice(LAST.model)}（约 $${LAST.estimate.toFixed(2)}）`, () => run('handoff'));
  });
  $('#exportSkip').addEventListener('input', () => { clearTimeout(ESTMR); ESTMR = setTimeout(pollExport, 400); });   // 跳过的多了，估价跟着变
  box.addEventListener('click', e => {
    const a = e.target.closest('a[data-open]');
    if (a) fetch(`/api/export/${a.dataset.open}/open`, { method: 'POST' });
  });
}

function result(k, j) {
  if (j.running) {
    const n = j.total ? ` ${j.done.toLocaleString()} / ${j.total.toLocaleString()}` : '';
    return `<span class="pulse"></span> 正在导出：${esc(j.stage || '')}${n}…`;
  }
  if (j.error) return `没导完：${esc(j.error)}`;
  if (!j.finished || !j.path) return '';
  return `上次 ${rel(j.finished)}${j.cost != null ? ` · 花了 $${j.cost.toFixed(2)}` : ''} · <a data-open="${k}">打开文件夹</a>`;
}

export async function pollExport() {
  const box = $('#exportBox'); if (!box) return;
  if (!box.firstElementChild) skeleton(box);
  const s = await fetch('/api/export?skip=' + encodeURIComponent($('#exportSkip').value)).then(r => r.json()).catch(() => null);
  if (!s) return;
  clearTimeout(POLL);
  LAST = s;
  const n = s.sends, skip = $('#exportSkip').value.trim();
  $('#setHoWhat').textContent = `请 ${nice(s.model)} 读你一年的日记和主题账本，写「关于我」「过去一年」「正在进行的事」三份，`
    + `再附「怎么用」。` + (local(s.model) ? '用 Claude 订阅额度，几分钟。' : `约 $${s.estimate.toFixed(2)}，按实际收，两三分钟。`)
    + (skip ? ` 跳过「${skip}」：标题或正文提到它的 ${n.dropped_diaries} 篇日记、${n.dropped_themes} 个主题不发出去。` : '');
  for (const [k, res, act] of [['full', '#setFullRes', '#setFullAct'], ['handoff', '#setHoRes', '#setHoAct']]) {
    const j = s[k];
    $(res).innerHTML = result(k, j);
    $(res).classList.toggle('bad', !!j.error && !j.running);
    $(act).querySelector('button').disabled = !!j.running;
  }
  if (s.full.running || s.handoff.running) POLL = setTimeout(pollExport, 1500);
}

// ---------------------------------------------------------------- 长聊（longchats.py，10-23）
// 换账号时要带走哪些：聊过 5 天以上的窗口，按聊过的天数排；还在聊的（最近 30 天）摊开，聊完了的折起来。
// 标「要迁 / 不迁」（再点一下取消）；续集并进它接着的那个（标题带 _2 /（3）的会猜一个，点了才并）。
const SRC = { chatgpt: 'ChatGPT', gemini: 'Gemini', lobehub: 'LobeHub' };

function longRow(l, all) {
  const others = all.filter(x => x.id !== l.id);
  return `<div class="set-row set-lc" data-id="${esc(l.id)}">
    <span class="set-text"><b><a class="set-lc-t" href="#/win/${encodeURIComponent(l.id)}">${esc(l.title || '（没标题）')}</a></b>${SRC[l.source] ? ` <span class="set-lc-src">${SRC[l.source]}</span>` : ''}
      <span class="set-what tnum">聊过 ${l.days} 天 · ${l.first.slice(5)} → ${l.last.slice(5)} · ${l.msgs.toLocaleString()} 条</span>
      ${l.parts.length ? `<span class="set-what">含续集：${l.parts.map(p => `${esc(p.title || '')}<button type="button" class="set-lc-x" data-split="${esc(p.id)}" title="拆开" aria-label="拆开">×</button>`).join('、')}</span>` : ''}
      ${l.guess ? `<span class="set-what">像是接着「${esc(l.guess.title || '')}」开的 <a class="set-lc-a" data-join="${esc(l.guess.id)}">并进去</a></span>` : ''}
      <span class="set-what"><a class="set-lc-a" data-pick>接着哪个开的…</a><select class="set-lc-sel hidden" aria-label="接着哪个窗口开的">
        <option value="">选一个</option>${others.map(x => `<option value="${esc(x.id)}">${esc((x.title || '').slice(0, 40))}</option>`).join('')}</select></span></span>
    <span class="set-act"><button class="btn-secondary btn-small set-lc-m${l.move === true ? ' on' : ''}" data-move="1">要迁</button>
      <button class="btn-secondary btn-small set-lc-m${l.move === false ? ' on' : ''}" data-move="0">不迁</button></span>
  </div>`;
}

function renderLong(box, lines) {
  const live = lines.filter(l => l.active), done = lines.filter(l => !l.active);
  const n = lines.filter(l => l.move === true).length, left = live.filter(l => l.move == null).length;
  box.innerHTML = `<div class="set-note set-lc-head">聊过 5 天以上的 ${lines.length} 个，最近 30 天还在聊的 ${live.length} 个。`
    + `已标要迁 ${n} 个${left ? `，还在聊的里 ${left} 个没标` : ''}。要迁的不是原文（这里都备份着），是它知道你哪些事、推进到哪了、你给它定的规矩。</div>`
    + live.map(l => longRow(l, lines)).join('')
    + (done.length ? `<details class="set-lc-done"><summary>聊完了的 ${done.length} 个（最近 30 天没聊过）</summary>${done.map(l => longRow(l, lines)).join('')}</details>` : '');
}

async function saveLong(box, id, body) {
  const r = await fetch(`/api/longchats/${encodeURIComponent(id)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) }).then(r => r.json()).catch(() => null);
  if (r && r.lines) {
    const open = box.querySelector('.set-lc-done')?.open;
    renderLong(box, r.lines);
    if (open) box.querySelector('.set-lc-done').open = true;
  }
}

export async function pollLong() {
  const box = $('#longBox'); if (!box) return;
  const r = await fetch('/api/longchats').then(r => r.json()).catch(() => null);
  if (!r) return;
  renderLong(box, r.lines);
  if (box.dataset.bound) return;
  box.dataset.bound = '1';
  box.addEventListener('click', e => {
    const row = e.target.closest('.set-lc'); if (!row) return;
    const id = row.dataset.id, b = e.target.closest('[data-move], [data-join], [data-split], [data-pick]');
    if (!b) return;
    if (b.dataset.move) {
      const want = b.dataset.move === '1';
      return saveLong(box, id, { move: b.classList.contains('on') ? null : want });    // 再点一下 = 取消
    }
    if (b.dataset.join) return saveLong(box, id, { after: b.dataset.join });
    if (b.dataset.split) return saveLong(box, b.dataset.split, { after: null });
    b.classList.add('hidden');
    row.querySelector('.set-lc-sel').classList.remove('hidden');
  });
  box.addEventListener('change', e => {
    const sel = e.target.closest('.set-lc-sel');
    if (sel && sel.value) saveLong(box, sel.closest('.set-lc').dataset.id, { after: sel.value });
  });
}
