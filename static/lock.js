// 隐私锁（规则在 lock.py）：页面一被切走就盖上锁屏，回来输密码；一直亮着就不打扰。
// 三页和锁屏页共用（templates/_lock.html），普通 script 放在 <head> 里，比各页的模块先跑。
// 服务器那头也锁着：没解锁的接口回 423 —— 这里把 fetch 包一层，碰到 423 就盖锁屏。
// 锁着的时候页面上有请求被挡过，解锁后整页重新载一次（各页的轮询碰到错就停了，不重载会停在半截）。
(() => {
  const me = document.currentScript;
  let on = me.dataset.on === '1';
  const isLockPage = me.dataset.page === 'lock';
  const PING = (+me.dataset.ping || 20) * 1000;
  const rawFetch = window.fetch.bind(window);
  let box = null, muted = [], missed = false, leaving = false, title = null, titleObs = null;

  window.fetch = async (...a) => {
    const r = await rawFetch(...a);
    if (r.status === 423 && on) { missed = true; show(); }
    return r;
  };

  const beacon = path => { try { navigator.sendBeacon(path); } catch (e) { rawFetch(path, { method: 'POST', keepalive: true }).catch(() => {}); } };

  function show() {
    if (box || !document.body) return;
    box = document.createElement('div');
    box.className = 'lock-scrim';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Ephemeris 锁着');
    box.innerHTML = `
      <form class="lock-card" autocomplete="on">
        <svg class="lock-logo" viewBox="0 0 24 24" fill="none" aria-hidden="true"><ellipse cx="12" cy="12" rx="9.4" ry="5.4" stroke="currentColor" stroke-width="1.7" transform="rotate(-32 12 12)"/><circle cx="17.4" cy="7.2" r="3.1" fill="var(--lock-accent)"/></svg>
        <div class="lock-title">Ephemeris 锁着</div>
        <div class="lock-sub">切到别处再回来，要输一次密码。</div>
        <input type="text" name="username" value="Ephemeris" autocomplete="username" hidden>
        <input class="lock-input" type="password" name="password" autocomplete="current-password" placeholder="密码" aria-label="密码">
        <button class="lock-btn" type="submit">解锁</button>
        <div class="lock-msg" role="status"></div>
      </form>`;
    muted = [...document.body.children].filter(el => !el.inert);       // 盖住的东西 Tab 键也摸不到
    muted.forEach(el => { el.inert = true; });
    document.body.append(box);
    document.documentElement.classList.add('lock-on');
    // 标签上的标题也别露（「问」页会把对话标题写在上面）；锁着的时候页面再改也改回来
    title = document.title; document.title = 'Ephemeris';
    const t = document.querySelector('title');
    if (t) { titleObs = new MutationObserver(() => { if (document.title !== 'Ephemeris') { title = document.title; document.title = 'Ephemeris'; } }); titleObs.observe(t, { childList: true }); }
    const input = box.querySelector('.lock-input'), msg = box.querySelector('.lock-msg'), btn = box.querySelector('.lock-btn');
    box.querySelector('form').onsubmit = async e => {
      e.preventDefault();
      if (!input.value) { input.focus(); return; }
      btn.disabled = true; msg.textContent = '';
      const r = await rawFetch('/api/lock/unlock', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: input.value }) }).then(r => r.json()).catch(() => null);
      btn.disabled = false;
      if (!r) { msg.textContent = '连不上：Ephemeris 还开着吗？'; return; }
      if (!r.ok) { msg.textContent = r.error || '密码不对'; input.select(); return; }
      if (isLockPage || missed) { location.reload(); return; }
      hide();
    };
    if (document.visibilityState === 'visible') input.focus();
  }

  function hide() {
    if (!box) return;
    titleObs?.disconnect(); titleObs = null;
    box.remove(); box = null;
    muted.forEach(el => { el.inert = false; }); muted = [];
    document.documentElement.classList.remove('lock-on');
    if (title !== null) document.title = title;
  }

  // 切走：先盖上（回来时第一眼就是锁屏），再告诉服务器作废令牌。
  // 在三页之间点导航 / 刷新也会变 hidden，但那之前一定先来 pagehide —— 那种不锁，只说一声「我走了」，下一页 5 秒内接上就行。
  addEventListener('pagehide', () => { leaving = true; if (on && !box) beacon('/api/lock/leave'); });
  addEventListener('pageshow', e => {
    leaving = false;
    if (e.persisted && on) ping();               // 后退回来的缓存页：问一下还算不算解锁
  });
  document.addEventListener('visibilitychange', () => {
    if (!on) return;
    if (document.visibilityState === 'hidden') {
      if (leaving) return;
      show(); beacon('/api/lock/lock');
    } else box?.querySelector('.lock-input')?.focus();
  });

  // 亮着的页面定时报到：服务器那头超过一分钟没人报到（标签都关了、电脑睡了）也算锁上
  async function ping() {
    if (!on || box || isLockPage || document.visibilityState !== 'visible') return;
    const r = await rawFetch('/api/lock/ping', { method: 'POST' }).catch(() => null);
    if (r && r.status === 423) show();
  }
  setInterval(ping, PING);

  if (on && isLockPage) document.addEventListener('DOMContentLoaded', show);
  else if (on) document.addEventListener('DOMContentLoaded', ping);

  // 设置里刚设好 / 去掉密码：这一页不用重载就开始 / 停止管
  window.ephLock = { set(v) { on = !!v; if (!on) hide(); } };
})();
