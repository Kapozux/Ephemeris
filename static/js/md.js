// 手写日记（journal.py 存的 Markdown）→ HTML。只认下面这几样，别的原样当文字：
//   # 标题 · - 列表 · 1. 有序 · ☐ / ☑ 待办 · > 引用 · ——— 分隔 · ![说明](/journal-files/…) 图片（单独一行）
//   行内：**粗体** · ~~删除线~~ · `代码` · [链接](http…)
// 先整体转义再加标签 —— 原文里的尖括号永远不会变成 HTML。行首每两个空格算一层缩进（Notion 里的嵌套）。
import { esc } from './util.js';

function inline(s) {                     // s 已经转义过
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/~~(.+?)~~/g, '<s>$1</s>')
    .replace(/(?<!!)\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function line(raw) {
  const pad = raw.match(/^ */)[0].length, l = raw.slice(pad);
  if (!l.trim()) return '';
  const depth = Math.min(3, Math.floor(pad / 2));
  const cls = base => {
    const c = [base, depth ? `j-in${depth}` : ''].filter(Boolean).join(' ');
    return c ? ` class="${c}"` : '';
  };
  let m;
  if ((m = l.match(/^!\[([^\]]*)\]\((\/journal-files\/[^)\s]+)\)$/))) {
    const src = esc(m[2]), cap = esc(m[1]);
    return `<figure${cls('j-fig')}><a href="${src}" target="_blank" rel="noopener" title="看原图"><img src="${src}" alt="${cap}" loading="lazy"></a>` +
           (cap ? `<figcaption>${inline(cap)}</figcaption>` : '') + '</figure>';
  }
  if ((m = l.match(/^(#{1,3}) (.*)$/))) return `<h4${cls('')}>${inline(esc(m[2]))}</h4>`;
  if (/^—{3,}$/.test(l.trim())) return '<hr>';
  if (/^(?:-|\d+\.|[☐☑])\s*$/.test(l)) return '';      // 空的列表项 / 待办（Notion 模板里留的空位）：不画，免得多出一个孤零零的记号
  if ((m = l.match(/^- (.*)$/))) return `<div${cls('j-li')}>${inline(esc(m[1]))}</div>`;
  if ((m = l.match(/^(\d+)\. (.*)$/))) return `<div${cls('j-li j-num')} data-n="${m[1]}.">${inline(esc(m[2]))}</div>`;
  if ((m = l.match(/^([☐☑]) (.*)$/))) return `<div${cls(m[1] === '☑' ? 'j-todo done' : 'j-todo')}>${inline(esc(m[2]))}</div>`;
  if ((m = l.match(/^> (.*)$/))) return `<blockquote${cls('')}>${inline(esc(m[1]))}</blockquote>`;
  return `<p${cls('')}>${inline(esc(l))}</p>`;
}

export const journalHtml = text => (text || '').split('\n').map(line).join('');

// 有几张图（日记页决定要不要先收起来）
export const imageCount = text => ((text || '').match(/^ *!\[[^\]]*\]\(\/journal-files\//gm) || []).length;

// 表格（10-25）：Markdown 的「| a | b |」（第二行是 |---|），和 Claude Code 用制表符画的 ┌─┬─┐ / │ a │ b │。
// 以前当成一行一行的字，比例字体一排就散成满屏的横线竖线。画成真的表：表头一行，数字靠右等宽；单元格折行不横滚（同 pre）。
const BOX_ROW = /^\s*[│┃║]/, BOX_RULE = /^\s*[┌┏╔├┣╠╞╟└┗╚╘╙╒╓][─━═┬┼┴┳╋┻╤╪╧╥╫╨┯┿┷\s]*[┐┓╗┤┫╣╡╢┘┛╝╛╜╕╖]?\s*$/;
const PIPE_ROW = /^\s*\|.*\|\s*$/, PIPE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const NUM = /^[−\-+±~≈]?[\d.,]+\s*(%|x|×|s|ms|k|K|M)?$/;
const cells = (l, sep) => { const c = l.trim().split(sep); if (c[0].trim() === '') c.shift(); if (c.length && c[c.length - 1].trim() === '') c.pop(); return c.map(x => x.trim()); };

function tableHtml(rows, head) {
  const td = (c, tag) => `<${tag}${NUM.test(c) ? ' class="num"' : ''}>${inline(esc(c))}</${tag}>`;
  const tr = (r, tag) => `<tr>${r.map(c => td(c, tag)).join('')}</tr>`;
  const [h, body] = head ? [rows[0], rows.slice(1)] : [null, rows];
  return `<div class="md-table"><table>${h ? `<thead>${tr(h, 'th')}</thead>` : ''}<tbody>${body.map(r => tr(r, 'td')).join('')}</tbody></table></div>`;
}

// 从第 i 行起是不是一张表：是就返回 {html, end}（end = 表后面那一行），不是返回 null
function table(lines, i) {
  if (BOX_RULE.test(lines[i]) || BOX_ROW.test(lines[i])) {
    let j = i;
    const groups = [[]];
    for (; j < lines.length && (BOX_RULE.test(lines[j]) || BOX_ROW.test(lines[j])); j++) {
      if (BOX_ROW.test(lines[j])) groups[groups.length - 1].push(cells(lines[j], /[│┃║]/));
      else if (groups[groups.length - 1].length) groups.push([]);
    }
    const filled = groups.filter(g => g.length);
    // 目录树（│   ├── a.py）也是 │ 开头：得有带列分隔（┬ ┼）的线才算表
    if (!filled.length || !lines.slice(i, j).some(l => BOX_RULE.test(l) && /[┬┼╤╪┳╋┯┿]/.test(l))) return null;
    // 行之间都有 ├ 分开：两条分隔线之间的几行是同一行折下来的，按格拼起来；只有表头下面一条：一行一行
    const merge = filled.length > 2 || filled.length === 2 && filled[1].length === 1;
    const rows = merge ? filled.map(g => g[0].map((_, k) => g.map(r => r[k] || '').join(' ').trim())) : filled.flat();
    return { html: tableHtml(rows, filled.length > 1 && filled[0].length >= 1), end: j };
  }
  if (PIPE_ROW.test(lines[i]) && i + 1 < lines.length && PIPE_RULE.test(lines[i + 1]) && lines[i + 1].includes('-')) {
    let j = i + 2;
    const rows = [cells(lines[i], '|')];
    for (; j < lines.length && PIPE_ROW.test(lines[j]); j++) rows.push(cells(lines[j], '|'));
    return { html: tableHtml(rows, true), end: j };
  }
  return null;
}

// 我说的话（原样显示，不当 Markdown）里贴进来的表格（10-25：把 Claude Code 的输出贴进 claude.ai，┌─┬─┐ 散成满屏横线）：
// 只把表画成表，别的字一个不动（转义、保留换行）。没有表返回 null，调用的照老样子显示。
export function plainBlocks(text) {
  const all = (text || '').split('\n'), out = [];
  let plain = [], found = false;
  // 一行一块（带上换行，外面是 pre-wrap）：长消息照样能按行折
  const flush = () => { for (const l of plain) out.push({ html: esc(l) + '\n', n: l.length, lines: 1 }); plain = []; };
  for (let i = 0; i < all.length; i++) {
    const t = table(all, i);
    if (!t) { plain.push(all[i]); continue; }
    flush();
    const src = all.slice(i, t.end);
    out.push({ html: t.html, n: src.join('\n').length, lines: src.length });
    found = true;
    i = t.end - 1;
  }
  flush();
  return found ? out : null;
}

// AI 回复（Claude / Gemini 写的 Markdown）→ 一块一块的 HTML。跟手写日记同一套行规则（同样先转义），
// 另外认 ``` 代码块、表格、「* 」列表、#### 标题、--- 分隔。每块带原文字数 n 和行数 lines：日记页按块折叠，不在半个标签里切。
export function mdBlocks(text) {
  const out = [];
  let code = null;
  const flush = () => { const s = code.join('\n'); out.push({ html: `<pre><code>${esc(s)}</code></pre>`, n: s.length, lines: code.length }); code = null; };
  const all = (text || '').split('\n');
  for (let i = 0; i < all.length; i++) {
    const raw = all[i];
    if (/^\s*```/.test(raw)) { if (code) flush(); else code = []; continue; }
    if (code) { code.push(raw); continue; }
    const t = table(all, i);
    if (t) {
      const src = all.slice(i, t.end);
      out.push({ html: t.html, n: src.join('\n').length, lines: src.length });
      i = t.end - 1;
      continue;
    }
    const l = raw.replace(/^(\s*)\* /, '$1- ').replace(/^(\s*)#{4,6} /, '$1### ').replace(/^\s*(?:-{3,}|\*{3,})\s*$/, '———')
                 .replace(/^(\s*)!\[/, '$1\u200b![');               // 行首垫个零宽空格：AI 写的图片语法不当成本地图片
    const html = line(l);
    if (html) out.push({ html, n: raw.length, lines: 1 });
  }
  if (code) flush();                                          // 没收尾的代码块
  return out;
}
