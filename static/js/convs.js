// 浮层「窗口」栏（窗口与导入）：claude.ai 同步和备份的状态、导入新文件、全部窗口一览、导入历史。
import { $, esc, rel, fmtW } from './util.js';
import { daysChanged } from './state.js';
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
  // 每个来源新到哪天。太久没新数据的提醒一句怎么补（账号要是没了就导不出来了）
  const HOW = { gemini: '用下面「复制导出脚本」在 gemini.google.com 导一份拖进来', claude: '看看上面的 claude.ai 同步是不是坏了' };
  const src = Object.entries(d.by_source).map(([k, v]) => {
    const last = (d.latest || {})[k], ago = last ? Math.round((Date.now() - new Date(last + 'T12:00:00')) / 864e5) : null;
    const name = k === 'claude' ? 'Claude' : k === 'gemini' ? 'Gemini' : k;
    return `${name} ${v}` + (last ? `（最新 ${last.slice(5)}${ago > 30 ? `，${ago} 天前 · ${HOW[k] || '导一份新的补上'}` : ''}）` : '');
  }).join(' · ');
  $('#convSub').textContent = `${d.total} 个窗口（${src}），其中 ${d.multi_day} 个横跨多天。最新的在上面。`;
  renderConvs();
  const h = await fetch('/api/imports').then(r => r.json());
  $('#importHist').innerHTML = h.imports.map(x => `<div class="pane-row">
      <span class="pane-row-title">${esc(x.file)}</span>
      <span class="pane-row-meta">${(x.imported_at || '').replace('T', ' ').slice(0, 16)}</span>
      <span class="pane-row-meta">${x.n_convs} 窗口 / 新 ${x.n_convs_new} · ${x.n_msgs.toLocaleString()} 条 / 新 ${x.n_msgs_new.toLocaleString()}${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated}` : ''}</span>
    </div>`).join('') || '<div class="pane-sub">还没有导入记录</div>';
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
  const r = await fetch('/api/import', { method: 'POST', body: fd }).then(r => r.json());
  box.innerHTML = (r.results || []).map(x => x.ok
    ? `<div class="cv-import-row"><b>${esc(x.file)}</b><span class="cv-import-meta">${x.n_convs} 个窗口（新 ${x.n_convs_new}）· ${x.n_msgs.toLocaleString()} 条（新 ${x.n_msgs_new.toLocaleString()}）· 涉及 ${x.days_touched.length} 天${x.diaries_invalidated ? ` · 作废日记 ${x.diaries_invalidated} 篇，侧栏可补全` : ''}</span></div>`
    : `<div class="cv-import-row bad"><b>${esc(x.file)}</b><span class="cv-import-meta">${esc(x.error || '失败')}</span></div>`).join('')
    + (r.error ? `<div class="cv-import-row bad">${esc(r.error)}</div>` : '');
  daysChanged();                     // 日历、日记状态、回顾指纹都可能变了：全部重拉
  loadConvs();
}
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
// 状态条放在「窗口与导入」面板顶上——那一栏本来就是讲「数据从哪来」的。

let CSPOLL = null;
// 早上那一串（daily.py）：同步的几个阶段之后还有「写日记」「整理」两段，分开说清楚在干嘛
const CS_STAGE = { '写日记': '拉完了，在写缺的日记', '整理': '在整理：手写日记、两个指标、主题、窗口回顾' };

function renderCSync(s) {
  const box = $('#syncBox'); if (!box) return;
  const j = s.job || {}, last = s.last_run;

  if (!s.configured) {
    box.innerHTML = `<div class="cv-sync-row"><span class="cv-sync-dot off"></span>
      <span>没配 claude.ai 自动同步。在 <code>ephemeris/.env</code> 里设 <code>CLAUDE_SESSION_KEY</code> 就能每天自动拉新对话。</span></div>`;
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
      <span><b>claude.ai 自动同步</b>　每天 ${s.at}，拉完自动写日记 · 上次 ${when} · ${detail}</span>
      <a id="csyncNow">${bad ? '重试' : '立刻同步'}</a>
    </div>`;
  $('#csyncNow').onclick = async () => {
    $('#csyncNow').closest('.cv-sync-row').innerHTML = '<span class="pulse"></span><span>起来了…</span>';   // 上面可能还有一行模型警告
    await fetch('/api/claude/sync', { method: 'POST' });
    pollCSync();
  };
  clearTimeout(CSPOLL);
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
  const extra = ok && /留着|旧版本/.test(ok.note || '') ? ' · ' + esc(ok.note.split(' · ').slice(1).join(' · ')) : '';
  const detail = bad ? esc(last.note || '失败')
    : ok ? `上次 ${rel(ok.finished_at)} · 只增不减，只传有变化的 · 这次写了 ${ok.files_written ?? '?'} / ${ok.files_total ?? '?'} 个文件（${kb(ok.bytes_written || 0)}）${extra}` : '还没备份过';
  const warn = !bad && ok?.warning;
  box.innerHTML = `<div class="cv-sync-row">
      <span class="cv-sync-dot ${bad || warn ? 'bad' : ok ? 'ok' : 'off'}"></span>
      <span><b>数据备份</b>　${b.every_days === 1 ? '每天' : `每 ${b.every_days} 天`}一次 → ${esc(where || '（没找到 Google Drive）')} · ${detail}</span>
      <a id="backupNow">${bad ? '重试' : '立刻备份'}</a></div>` +
    (warn ? `<div class="cv-sync-row"><span class="cv-sync-dot bad"></span><span>${esc(ok.warning)}</span></div>` : '');
  $('#backupNow').onclick = async () => { await fetch('/api/backup', { method: 'POST' }); setTimeout(pollBackup, 300); };
}

