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
