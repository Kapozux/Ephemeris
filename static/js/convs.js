// 浮层「数据来源」栏（以前叫窗口与导入）：顶上一条紧凑的状态（claude.ai 同步、备份、各来源新到哪天），
// 「导入…」点开才是拖放区、Gemini 步骤和导入历史；下面是全部窗口一览。
import { $, esc, rel, fmtW, ask, usd } from './util.js';
import { S, daysChanged } from './state.js';
import { navigate } from './nav.js';

let CONVS = [], CONV_SHOWN = 120;
// 搜索框：两个字以上去后端搜标题和全文（search.py，跟侧栏同一套索引）；一个字只在本地筛标题
let HITS = null, HITS_Q = '', STMR = null;

function renderConvs() {
  const kw = ($('#convFilter').value || '').trim().toLowerCase();
  const rows = HITS && HITS_Q === kw ? CONVS.filter(c => HITS[c.id])
    : CONVS.filter(c => !kw || (c.title || '').toLowerCase().includes(kw) || (c.preview || '').toLowerCase().includes(kw));
  const list = rows.slice(0, CONV_SHOWN).map(c => {
    const multi = (c.n_days || 0) > 1, h = HITS && HITS_Q === kw ? HITS[c.id] : null;
    const title = c.title_fallback ? esc((c.preview || c.title || '').replace(/\s+/g, ' ').slice(0, 60)) : esc(c.title);
    return `<div class="cv-row" data-d="${h?.date || c.last_date}" data-c="${c.id}"${h?.msg_id ? ` data-m="${h.msg_id}"` : ''}>
      <span class="src ${c.source}"></span>
      <span class="cv-title ${c.title_fallback ? 'cv-untitled' : ''}">${title}${multi ? `<span class="cv-span">${c.n_days} 天</span>` : ''}${c.last_import && c.first_import !== c.last_import ? '<small>已合并</small>' : ''}</span>
      <span class="cv-meta">${multi ? c.first_date.slice(5) + ' → ' : ''}${c.last_date}</span>
      <span class="cv-meta">${c.n_msgs} 条</span>
      <span class="cv-meta">${fmtW(c.chars || 0)} 字</span>
      ${h?.hits ? `<span class="cv-snip"><b>${h.hits} 处</b>${h.snip}</span>` : ''}</div>`;
  }).join('');
  const none = kw && !rows.length ? `<div class="cv-none">标题和聊天里都没有「${esc(kw)}」</div>` : '';
  $('#convList').innerHTML = none + list + (rows.length > CONV_SHOWN ? `<a class="cv-more" id="convMore">还有 ${rows.length - CONV_SHOWN} 个，再显示 200</a>` : '');
  $('#convList').querySelectorAll('.cv-row').forEach(r => r.onclick = () =>
    navigate(`day/${r.dataset.d}/conv/${r.dataset.c}${r.dataset.m ? `/msg/${r.dataset.m}/q/${encodeURIComponent(HITS_Q)}` : ''}`));
  const m = $('#convMore'); if (m) m.onclick = () => { CONV_SHOWN += 200; renderConvs(); };
}

async function loadConvs() {
  const d = await fetch('/api/conversations').then(r => r.json());
  CONVS = d.conversations;
  // 每个来源新到哪天：小标签。太久没新数据的标红，提示怎么补（账号要是没了就导不出来了）
  const HOW = { gemini: '用「导入…」里的「复制导出脚本」在 gemini.google.com 导一份拖进来', claude: '看看上面的 claude.ai 同步是不是坏了' };
  const NAME = { claude: 'Claude', gemini: 'Gemini', ask: '问', cc: 'Claude Code' };
  $('#cvSrcs').innerHTML = Object.entries(d.by_source).map(([k, v]) => {
    const last = (d.latest || {})[k], ago = last ? Math.round((Date.now() - new Date(last + 'T12:00:00')) / 864e5) : null;
    const stale = ago > 30, tip = `${v} 个窗口${stale ? ` · ${HOW[k] || '导一份新的补上'}` : ''}`;
    return `<button type="button" class="cv-src${stale ? ' stale' : ''}" data-k="${esc(k)}" title="${esc(tip)}">${esc(NAME[k] || k)}<span>${last ? last.slice(5) : '—'}${stale ? ` · ${ago} 天前` : ''}</span></button>`;
  }).join('');
  $('#cvSrcs').querySelectorAll('.cv-src.stale').forEach(b => b.onclick = () => showImport(true));   // 过期的：点了打开导入
  $('#convSub').textContent = `${d.total.toLocaleString()} 个窗口 · ${d.multi_day} 个横跨多天`;
  renderConvs();
  const h = await fetch('/api/imports').then(r => r.json());
  const HIST = 6, all = h.imports, more = all.length - HIST;
  $('#importHist').innerHTML = all.map((x, i) => `<div class="pane-row${i >= HIST ? ' hidden' : ''}">
      <span class="pane-row-title">${esc(x.file)}</span>
      <span class="pane-row-meta">${(x.imported_at || '').replace('T', ' ').slice(0, 16)}</span>
      <span class="pane-row-meta">${x.n_convs} 窗口 / 新 ${x.n_convs_new} · ${x.n_msgs.toLocaleString()} 条 / 新 ${x.n_msgs_new.toLocaleString()}${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated}` : ''}</span>
    </div>`).join('') + (more > 0 ? `<a class="cv-more" id="importHistMore">还有 ${more} 次，全部显示</a>` : '') || '<div class="pane-sub">还没有导入记录</div>';
  const hm = $('#importHistMore');
  if (hm) hm.onclick = () => { $('#importHist').querySelectorAll('.pane-row.hidden').forEach(r => r.classList.remove('hidden')); hm.remove(); };
}
$('#convFilter').addEventListener('input', () => {
  CONV_SHOWN = 120;
  const kw = $('#convFilter').value.trim().toLowerCase();
  clearTimeout(STMR);
  if (kw.length < 2) { HITS = null; renderConvs(); return; }
  STMR = setTimeout(async () => {
    const r = await fetch('/api/conversations/search?q=' + encodeURIComponent(kw)).then(r => r.json()).catch(() => null);
    if (!r || $('#convFilter').value.trim().toLowerCase() !== kw) return;     // 打字快：只认最后一次
    HITS = r.hits; HITS_Q = kw; renderConvs();
  }, 250);
});

async function doImport(files) {
  if (!files.length) return;
  const box = $('#importResult'); box.classList.remove('hidden');
  box.innerHTML = `<div class="cv-import-row"><span class="pulse"></span>正在导入 ${files.length} 个文件…</div>`;
  const fd = new FormData(); [...files].forEach(f => fd.append('file', f));
  let r;
  try {
    const res = await fetch('/api/import', { method: 'POST', body: fd });
    r = await res.json().catch(() => ({}));
    if (!res.ok && !(r.results || []).length) throw new Error(r.error || `Ephemeris 出错了（${res.status}）`);
  } catch (e) {                      // 连不上 / 服务器报错：别一直转圈，说原因，给个重试
    box.innerHTML = `<div class="cv-import-row bad">没导进去：${esc(e instanceof TypeError ? '连不上 Ephemeris' : e.message)} · <a class="cv-import-retry">再选一次</a></div>`;
    box.querySelector('.cv-import-retry').onclick = () => $('#importFile').click();
    return;
  }
  box.innerHTML = (r.results || []).map(x => x.ok
    ? `<div class="cv-import-row"><b>${esc(x.file)}</b><span class="cv-import-meta">${x.n_convs} 个窗口（新 ${x.n_convs_new}）· ${x.n_msgs.toLocaleString()} 条（新 ${x.n_msgs_new.toLocaleString()}）· 涉及 ${x.days_touched.length} 天${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated} 篇，侧栏可补全` : ''}</span></div>`
    : `<div class="cv-import-row bad"><b>${esc(x.file)}</b><span class="cv-import-meta">${esc(x.error || '失败')}</span></div>`).join('')
    + (r.error ? `<div class="cv-import-row bad">${esc(r.error)}</div>` : '');
  daysChanged();                     // 日历、日记状态、回顾指纹都可能变了：全部重拉
  loadConvs();
}
// 「导入…」：拖放区、Gemini 步骤、导入历史平时收着
function showImport(open) {
  const box = $('#importBox'), t = $('#importToggle');
  open = open ?? box.classList.contains('hidden');
  box.classList.toggle('hidden', !open);
  t.setAttribute('aria-expanded', open);
  t.classList.toggle('on', open);
}
$('#importToggle').onclick = () => showImport();
// 收着的时候直接把文件拖进这一栏：先展开，再落到拖放区里
$('.modal-pane[data-pane=convs]').addEventListener('dragenter', e => {
  if ([...(e.dataTransfer?.types || [])].includes('Files')) showImport(true);
});
$('#importPick').onclick = () => $('#importFile').click();
// Gemini 导出脚本（export_gemini.js）：复制到剪贴板，用户自己去 gemini.google.com 的控制台粘贴
$('#geminiCopy').onclick = async () => {
  const say = t => { $('#geminiCopied').textContent = t; };
  try {
    const code = await fetch('/tools/export_gemini.js').then(r => { if (!r.ok) throw new Error(r.status); return r.text(); });
    await navigator.clipboard.writeText(code);
    say('　已复制，去 Gemini 页面粘贴吧');
  } catch (e) { say('　没复制上（' + e.message + '）：直接打开 Ephemeris 文件夹里的 export_gemini.js 复制'); }
};
$('#importFile').onchange = e => { doImport(e.target.files); e.target.value = ''; };
const ib = $('#importBox');
ib.addEventListener('dragover', e => { e.preventDefault(); ib.classList.add('over'); });
ib.addEventListener('dragleave', () => ib.classList.remove('over'));
ib.addEventListener('drop', e => { e.preventDefault(); ib.classList.remove('over'); doImport(e.dataTransfer.files); });

export const ensureConvs = () => { if (!CONVS.length) loadConvs(); };

// ---------------------------------------------------------------- claude.ai 自动同步
// 状态条放在「数据来源」面板顶上——那一栏本来就是讲「数据从哪来」的。

let CSPOLL = null;
// 早上那一串（daily.py）：同步的几个阶段之后还有「写日记」「整理」两段，分开说清楚在干嘛
const CS_STAGE = { '写日记': '拉完了，在写缺的日记', '整理': '在整理：手写日记、两个指标、主题、窗口回顾' };

function renderCSync(s) {
  const box = $('#syncBox'); if (!box) return;
  const j = s.job || {}, last = s.last_run;

  if (!s.configured) {
    box.innerHTML = `<div class="cv-sync-row"><span class="cv-sync-dot off"></span>
      <span>没配 claude.ai 自动同步：贴上 sessionKey 就能每天自动拉新对话。</span><a href="#/panel/settings">去设置</a></div>`;
    return;
  }
  if (j.running) {
    box.innerHTML = `<div class="cv-sync-row"><span class="pulse"></span>
      <span>${CS_STAGE[j.stage] || `正在从 claude.ai 同步：${esc(j.stage || '')}`} ${j.total ? `${j.done}/${j.total}` : ''}</span></div>`;
    clearTimeout(CSPOLL); CSPOLL = setTimeout(pollCSync, 2000);
    return;
  }

  const bad = j.error || (last && !last.ok);
  const when = last?.finished_at ? rel(last.finished_at) : '还没跑过';
  const detail = last?.ok
    ? `拉了 ${last.n_fetched || 0} 个对话${last.n_msgs_new ? `，新消息 ${last.n_msgs_new}` : '，没有新内容'}`
    : esc(j.error || last?.note || '失败');
  // 模型不可用要看得见：以前调模型失败只返回空，日记写不出来也没人知道
  const L = s.llm || {};
  const llmWarn = L.check ? `模型不可用（${esc(L.backend)}）：${esc(L.check)}`
    : (L.failures && L.last_error) ? `最近有 ${L.failures} 次调模型失败（${esc(L.backend)}）：${esc(L.last_error)}` : '';
  box.innerHTML = (llmWarn ? `<div class="cv-sync-row"><span class="cv-sync-dot bad"></span><span><b>模型</b>　${llmWarn}</span></div>` : '') + `<div class="cv-sync-row">
      <span class="cv-sync-dot ${bad ? 'bad' : 'ok'}"></span>
      <span><b>claude.ai 同步</b>　上次 ${when} · ${detail} · 每天 ${s.at} 自动</span>
      ${bad ? '<a href="#/panel/settings" title="sessionKey 过期了最常见：去设置里贴一个新的">去设置换 sessionKey</a>' : ''}
      <a id="csyncNow">${bad ? '重试' : '立刻同步'}</a>
    </div>`;
  const now = $('#csyncNow');
  // 立刻跑一遍早上那一串：会补写最近几天缺的日记、做卡、整理，都要调模型 —— 先说清楚
  now.onclick = () => { const n = recentMissing(); ask(now, `会拉新对话，补写最近 7 天缺的日记（${n ? `现在缺 ${n} 天，${usd(n * 0.01)}` : '现在不缺'}，新拉到的天另算），再做卡、整理，都要调模型`, async () => {
    const row = now.closest('.cv-sync-row');
    row.innerHTML = '<span class="pulse"></span><span>起来了…</span>';   // 上面可能还有一行模型警告
    const ok = await fetch('/api/claude/sync', { method: 'POST' }).then(r => r.ok).catch(() => false);
    if (!ok) {                       // 没起来：说原因，给重试，别一直转圈
      row.innerHTML = '<span class="cv-sync-dot bad"></span><span>没起来：连不上 Ephemeris</span><a>重试</a>';
      row.querySelector('a').onclick = pollCSync;
      return;
    }
    pollCSync();
  }); };
  clearTimeout(CSPOLL);
}

// 最近 7 天（不含今天）聊够了、还没日记的天数：跟早上那一串（daily.AUTO_DAYS）补的是同一批
function recentMissing() {
  const iso = t => new Date(t - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  const today = iso(Date.now()), since = iso(Date.now() - 7 * 864e5);
  return S.days.filter(d => d.date >= since && d.date < today && !d.has_diary && (d.chars || 0) >= 800).length;
}

export async function pollCSync() {
  const s = await fetch('/api/claude/sync').then(r => r.json()).catch(() => null);
  if (s) renderCSync(s);
  pollBackup();
}

// 每周备份到 Google Drive：状态放在同步那行下面，坏了看得见
let BKPOLL = null;
async function pollBackup() {
  const b = await fetch('/api/backup').then(r => r.json()).catch(() => null);
  const box = $('#backupBox'); if (!b || !box) return;
  const j = b.job || {}, ok = b.last_ok, last = b.last;
  clearTimeout(BKPOLL);
  if (j.running) {
    box.innerHTML = `<div class="cv-sync-row"><span class="pulse"></span><span>正在备份：${esc(j.stage || '')}</span></div>`;
    BKPOLL = setTimeout(pollBackup, 2000);
    return;
  }
  const bad = last && !last.ok;
  const where = (b.dest || '').replace(/^.*\/My Drive\//, 'Google Drive / ');
  const kb = n => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;
  // 备份只增不减：本地少了的行在备份里留着、改掉的旧版本另存（backup.py）。有这种情况就顺带说一句
  const extra = ok && /留着|旧版本/.test(ok.note || '') ? ok.note.split(' · ').slice(1).join(' · ') : '';
  const detail = bad ? esc(last.note || '失败')
    : ok ? `上次 ${rel(ok.finished_at)} · 写了 ${ok.files_written ?? '?'} / ${ok.files_total ?? '?'} 个文件（${kb(ok.bytes_written || 0)}）` : '还没备份过';
  // 只增不减的细节（本地少了的行留在备份里、改掉的旧版本另存）放进悬停提示，状态条只占一行
  const tip = `只增不减，只传有变化的${extra ? ' · ' + extra : ''}`;
  const warn = !bad && ok?.warning;
  box.innerHTML = `<div class="cv-sync-row">
      <span class="cv-sync-dot ${bad || warn ? 'bad' : ok ? 'ok' : 'off'}"></span>
      <span title="${esc(tip)}"><b>备份</b>　${detail} → ${esc(where || '（没找到 Google Drive）')} · ${b.every_days === 1 ? '每天' : `每 ${b.every_days} 天`}一次</span>
      <a id="backupNow">${bad ? '重试' : '立刻备份'}</a></div>` +
    (warn ? `<div class="cv-sync-row"><span class="cv-sync-dot bad"></span><span>${esc(ok.warning)}</span></div>` : '');
  // 点了马上变灰、转起来；没起来就说原因
  const now = $('#backupNow');
  now.onclick = async () => {
    const row = now.closest('.cv-sync-row');
    row.classList.add('busy');
    now.replaceWith(Object.assign(document.createElement('span'), { className: 'pulse' }));
    const ok = await fetch('/api/backup', { method: 'POST' }).then(r => r.ok).catch(() => false);
    if (!ok) {
      row.classList.remove('busy');
      row.lastElementChild.replaceWith(Object.assign(document.createElement('a'), { textContent: '没起来（连不上 Ephemeris）· 重试', onclick: pollBackup }));
      return;
    }
    setTimeout(pollBackup, 300);
  };
}

