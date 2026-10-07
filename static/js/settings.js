// 浮层「设置」栏：目前只有导出（export.py）。
// 完整导出：所有聊天原文 + 每周日记 + 图片 + 主题，不调模型；交接包：请大模型（Claude Opus 4.6）写给新 AI 账号的三份文件。
import { $, esc, rel, ask } from './util.js';

let POLL = null, ESTMR = null, LAST = null;
const nice = m => (m || '').split('/').pop().replace(/-/g, ' ').replace(/\b\w/g, x => x.toUpperCase());

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
    ask(b, `把 ${n.diaries} 篇日记、${n.themes} 个主题发给 OpenRouter 上的 ${nice(LAST.model)}（约 $${LAST.estimate.toFixed(2)}）`, () => run('handoff'));
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
    + `再附「怎么用」。约 $${s.estimate.toFixed(2)}，按实际收，两三分钟。`
    + (skip ? ` 跳过「${skip}」：标题或正文提到它的 ${n.dropped_diaries} 篇日记、${n.dropped_themes} 个主题不发出去。` : '');
  for (const [k, res, act] of [['full', '#setFullRes', '#setFullAct'], ['handoff', '#setHoRes', '#setHoAct']]) {
    const j = s[k];
    $(res).innerHTML = result(k, j);
    $(res).classList.toggle('bad', !!j.error && !j.running);
    $(act).querySelector('button').disabled = !!j.running;
  }
  if (s.full.running || s.handoff.running) POLL = setTimeout(pollExport, 1500);
}
