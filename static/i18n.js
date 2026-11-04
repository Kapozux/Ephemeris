// 界面语言（11-04 用户：英文 README 的动图里全是中文，要做真的英文界面）。
// 界面文字照旧用中文写；换成别的语言时，这里照 static/i18n/<语言>.js 那张「中文原文 → 译文」的表，
// 把页面上的文字节点和 title / placeholder / aria-label / alt 换掉（MutationObserver：模板、JS 画出来的、后来改的都管）。
// 表里没有的照旧显示中文。带 {0} {1} 的条目是模板：「查了 {0} 步」能对上「查了 4 步」，抓到的部分本身在表里也换。
// 用户自己的内容（聊天原话、日记正文、回答、窗口标题、搜索结果……）所在的元素标 translate="no"，整棵子树不碰。
// JS 里不进 DOM 的文字（confirm、画布上的字）用 I18N.t('中文', 参数…)。
// 语言存在 .env 的 EPHEMERIS_LANG（setup.save），模板经 _lock.html 把它放在本标签的 data-lang 上。
(() => {
  const lang = (document.currentScript && document.currentScript.dataset.lang) || 'zh';
  const DICT = (lang !== 'zh' && window.I18N_DICT) || {};
  const HAN = /[㐀-鿿]/;
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  const SKIP = 'script,style,textarea,[translate="no"],[contenteditable="true"]';

  const exact = new Map(), byHead = new Map(), wild = [];
  for (const [k, v] of Object.entries(DICT)) {
    if (!/\{\d+\}/.test(k)) { exact.set(k, v); continue; }
    const parts = k.split(/(\{\d+\})/);
    const order = [];
    const re = new RegExp('^' + parts.map(p => {
      const m = /^\{(\d+)\}$/.exec(p);
      if (m) { order.push(+m[1]); return '([\\s\\S]+?)'; }
      return p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('') + '$');
    const pat = { re, order, to: v };
    if (parts[0]) {
      const h = parts[0][0];
      if (!byHead.has(h)) byHead.set(h, []);
      byHead.get(h).push(pat);
    } else wild.push(pat);
  }

  function fill(to, args) {
    return to.replace(/\{(\d+)\}/g, (_, i) => (args[+i] ?? ''));
  }

  // 抓到的部分也照表换（「{0} · Ephemeris」里的「问」）；整句对不上就按「 · 」切开一段段换（「1 个窗口 · 2 条 · 122 字」）
  function part(g, depth) {
    const core = g.trim();
    if (!HAN.test(core)) return g;
    const to = lookup(core, depth + 1);
    return to == null ? g : g.replace(core, to);
  }

  function lookup(s, depth = 0) {
    if (exact.has(s)) return exact.get(s);
    if (depth > 3) return null;
    for (const p of [...(byHead.get(s[0]) || []), ...wild]) {
      const m = p.re.exec(s);
      if (!m) continue;
      const args = [];
      p.order.forEach((n, i) => { args[n] = part(m[i + 1], depth); });
      return fill(p.to, args);
    }
    if (/\s·\s?|\s?·\s/.test(s)) {
      const bits = s.split(/(\s*·\s*)/);
      const out = bits.map((b, i) => i % 2 ? b : part(b, depth));
      if (out.some((b, i) => b !== bits[i])) return out.join('');
    }
    return null;
  }

  // 给 JS 用：t('查了 {0} 步', 4)。中文界面直接填参数。
  function t(s, ...args) {
    if (lang === 'zh') return fill(s, args);
    const k = exact.has(s) ? exact.get(s) : null;
    return fill(k ?? s, args);
  }

  window.I18N = { lang, t };
  if (lang === 'zh' || !Object.keys(DICT).length) return;
  document.documentElement.lang = lang;

  function text(node) {
    const v = node.nodeValue;
    if (!v || !HAN.test(v)) return;
    const p = node.parentElement;
    if (p && p.closest(SKIP)) return;
    const core = v.trim();
    const to = lookup(core);
    if (to != null && to !== core) node.nodeValue = v.replace(core, to);
  }

  function attrs(el) {
    if (el.closest(SKIP) && el.tagName !== 'TEXTAREA') return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || !HAN.test(v)) continue;
      const to = lookup(v.trim());
      if (to != null) el.setAttribute(a, to);
    }
  }

  function walk(root) {
    if (root.nodeType === 3) { text(root); return; }
    if (root.nodeType !== 1) return;
    if (root.matches(SKIP) && root.tagName !== 'TEXTAREA') return;
    attrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => (n.nodeType === 1 && n.matches(SKIP) && n.tagName !== 'TEXTAREA') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? text(n) : attrs(n);
  }

  new MutationObserver(recs => {
    for (const r of recs) {
      if (r.type === 'characterData') text(r.target);
      else if (r.type === 'attributes') attrs(r.target);
      else r.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
})();
