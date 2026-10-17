// 隐私锁（规则在 lock.py）：页面一被切走就盖上锁屏，回来输密码或者按 Touch ID；一直亮着就不打扰。
// 三页和锁屏页共用（templates/_lock.html），普通 script 放在 <head> 里，比各页的模块先跑。
// 服务器那头也锁着：没解锁的接口回 423 —— 这里把 fetch 包一层，碰到 423 就盖锁屏。
// 锁着的时候页面上有请求被挡过，解锁后整页重新载一次（各页的轮询碰到错就停了，不重载会停在半截）。
// 锁屏照 macOS「密码」App 的样子（10-16 用户给的截图）：图标右下角一个指纹，取消了还能输密码。
// 指纹有两种（10-17）：
//   · 系统指纹框（data-native，lock.native_unlock：服务器在这台 Mac 上弹 macOS 自己的框，跟 sudo 那个一样，不用登记）—— 优先用。
//     这一页真的在前面（标签亮着、窗口有焦点）时自动弹：锁屏一出来、从别处回来都弹；你取消了就不再自己弹，
//     直到你真的走开再回来（不然框一收、焦点回来又弹，没完）。
//   · 浏览器的通行密钥（WebAuthn，要先在设置里登记）—— 没有系统指纹时才用，只在点了指纹 / 空密码框回车时弹（用户嫌它难用）。
// 两种都是：切到别的标签就取消；验过了但这一页已经不在前面（你去了别的窗口），令牌当场作废、不解锁。
// 10-16 第一版的 bug：后台标签、在别的网页时也弹，弹着的时候切走还不锁，在别处一按就开了。
// 在 Mac App（mac/Ephemeris.swift）里打开时：锁屏是 App 原生画的（指纹嵌在里面，不弹框），这里不画，
// 该锁的时候告诉 App 一声（webkit.messageHandlers.eph）；App 解开后调 ephLock.appUnlocked()。
(() => {
  const me = document.currentScript;
  let on = me.dataset.on === '1';
  let touchOn = me.dataset.touch === '1';
  const nativeOn = me.dataset.native === '1';
  const isLockPage = me.dataset.page === 'lock';
  const PING = (+me.dataset.ping || 20) * 1000;
  const rawFetch = window.fetch.bind(window);
  let box = null, muted = [], missed = false, leaving = false, title = null, titleObs = null;
  let hold = false, asking = null, askEnd = 0;        // asking：正在等指纹（.abort() 取消）；askEnd：上次指纹框收起的时刻

  window.fetch = async (...a) => {
    const r = await rawFetch(...a);
    if (r.status === 423 && on) { missed = true; show(); }
    return r;
  };

  const beacon = path => { try { navigator.sendBeacon(path); } catch (e) { rawFetch(path, { method: 'POST', keepalive: true }).catch(() => {}); } };
  const post = (path, body) => rawFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(r => r.json()).catch(() => null);
  // WebAuthn 只认 localhost 这个名字（lock.RP_ID）；从 127.0.0.1 打开的只能输密码
  const canPasskey = () => touchOn && location.hostname === 'localhost' && !!window.PublicKeyCredential;
  const canTouch = () => nativeOn || canPasskey();
  const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const unb64 = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat(-s.length & 3)), c => c.charCodeAt(0));

  const FINGER = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true">
    <path d="M5.2 9.2A7.6 7.6 0 0 1 18.8 9.2"/><path d="M4.4 13.4c.3-1 .4-2 .4-3"/><path d="M19.4 14.6c.2-1.2.2-2.4 0-3.4"/>
    <path d="M7.4 18.6c1-1.9 1.4-4 1.4-6.2a3.2 3.2 0 0 1 6.4 0c0 .9 0 1.8-.1 2.6"/><path d="M10.2 20.4c1.3-2.3 1.9-4.9 1.8-7.9"/>
    <path d="M13.6 20.6c.4-.9.8-1.9 1-2.9"/><path d="M17.2 18.4c.4-1.1.7-2.3.8-3.6"/><path d="M8.3 6.1a6 6 0 0 1 8.1 2.4"/></svg>`;

  const app = window.webkit?.messageHandlers?.eph;

  function show() {
    if (app) { if (on) app.postMessage({ lock: true }); return; }
    if (box || !document.body) return;
    box = document.createElement('div');
    box.className = 'lock-scrim';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Ephemeris 锁着');
    box.innerHTML = `
      <form class="lock-card" autocomplete="on">
        <div class="lock-icon">
          <svg class="lock-logo" viewBox="0 0 24 24" fill="none" aria-hidden="true"><ellipse cx="12" cy="12" rx="9.4" ry="5.4" stroke="currentColor" stroke-width="1.7" transform="rotate(-32 12 12)"/><circle cx="17.4" cy="7.2" r="3.1" fill="var(--lock-accent)"/></svg>
          <button type="button" class="lock-touch" title="用 Touch ID 解锁" aria-label="用 Touch ID 解锁" hidden>${FINGER}</button>
        </div>
        <div class="lock-title">Ephemeris 锁着</div>
        <div class="lock-sub">输入密码解锁。</div>
        <input type="text" name="username" value="Ephemeris" autocomplete="username" hidden>
        <input class="lock-input" type="password" name="password" autocomplete="current-password" placeholder="输入密码" aria-label="密码">
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
    const input = box.querySelector('.lock-input'), msg = box.querySelector('.lock-msg');
    if (canTouch()) {
      box.querySelector('.lock-touch').hidden = false;
      box.querySelector('.lock-sub').textContent = nativeOn ? '按指纹解锁，或者输入密码。' : '点指纹用 Touch ID（密码框空着按回车也行），或者输入密码。';
      box.querySelector('.lock-touch').onclick = () => touch();
    }
    box.querySelector('form').onsubmit = async e => {
      e.preventDefault();
      if (!input.value) { input.focus(); return; }
      input.disabled = true; msg.textContent = '';
      const r = await post('/api/lock/unlock', { password: input.value });
      input.disabled = false;
      if (!r) { msg.textContent = '连不上：Ephemeris 还开着吗？'; input.focus(); return; }
      if (!r.ok) { msg.textContent = r.error || '密码不对'; input.focus(); input.select(); return; }
      done();
    };
    // 表单里还有个藏着的用户名框（给密码管理器认），没有提交按钮时回车不会自己提交
    // 密码框空着按回车 = 用 Touch ID（手不用离开键盘）
    input.onkeydown = e => {
      if (e.key !== 'Enter' || e.isComposing) return;
      e.preventDefault();
      if (!input.value && canTouch()) touch();
      else box.querySelector('form').requestSubmit();
    };
    if (document.visibilityState === 'visible') input.focus();
    autoTouch();
  }

  const front = () => document.visibilityState === 'visible' && document.hasFocus();

  // 系统指纹框自动弹：只在这一页真的在前面、没在等、刚收起的框不算「回来」时
  function autoTouch() {
    if (!nativeOn || !box || asking || Date.now() - askEnd < 1500) return;
    setTimeout(() => { if (front()) touch(); }, 120);
  }

  // 解锁前最后一眼：系统框收起后焦点要回来一下；这一页不在前面就作废刚拿到的令牌
  async function settle(msg) {
    for (let i = 0; i < 10 && !front(); i++) await new Promise(ok => setTimeout(ok, 100));
    if (front() && !asking?.signal.aborted) { done(); return; }
    await post('/api/lock/lock');
    if (box) msg.textContent = '按指纹时你不在这一页，没解开。回来再按一次';
  }

  async function nativeTouch(msg) {
    const r = await post('/api/lock/touch/native');
    if (!r?.ok) { if (box && r?.error && r.error !== '没按指纹') msg.textContent = r.error; return; }
    await settle(msg);
  }

  function done() {
    if (isLockPage || missed) { location.reload(); return; }
    hide();
  }

  // Touch ID：只由点击 / 回车调用。系统弹框时窗口会失焦再回来，所以不能「一失焦就取消」；
  // 切到别的标签（hidden）就取消；验过了再看一眼这一页还在不在前面，不在就不解锁、作废刚拿到的令牌。
  async function touch() {
    if (!box || asking || !canTouch() || !front()) return;
    const msg = box.querySelector('.lock-msg');
    msg.textContent = '';
    if (nativeOn) {
      const sig = { aborted: false };
      asking = { signal: sig, abort() { sig.aborted = true; beacon('/api/lock/touch/cancel'); } };
      try { await nativeTouch(msg); } finally { asking = null; askEnd = Date.now(); if (front()) box?.querySelector('.lock-input')?.focus(); }
      return;
    }
    asking = new AbortController();
    try {
      const c = await post('/api/lock/touch/begin', { kind: 'get' });
      if (!c?.ok || !c.ids?.length) { msg.textContent = '这台 Mac 还没登记 Touch ID（设置 → 隐私锁），先输密码'; return; }
      const cred = await navigator.credentials.get({ signal: asking.signal, publicKey: {
        challenge: unb64(c.challenge), rpId: c.rp, userVerification: 'required', timeout: 60000,
        allowCredentials: c.ids.map(id => ({ type: 'public-key', id: unb64(id) })) } });
      const r = await post('/api/lock/touch/unlock', { id: b64(cred.rawId), clientData: b64(cred.response.clientDataJSON),
        authData: b64(cred.response.authenticatorData), sig: b64(cred.response.signature) });
      if (!r?.ok) { if (box) msg.textContent = r?.error || 'Touch ID 没通过，输密码吧'; return; }
      await settle(msg);
    } catch (e) {
      // 取消了 / 超时 / 切走了：安静地留在输密码
    } finally {
      asking = null;
      askEnd = Date.now();
      if (front()) box?.querySelector('.lock-input')?.focus();
    }
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
      asking?.abort();                           // 正在等 Touch ID：切走了就作废这次
      if (leaving) return;
      show(); beacon('/api/lock/lock');
    } else { box?.querySelector('.lock-input')?.focus(); autoTouch(); }
  });

  // 窗口还露在屏幕上、但你去用别的了（点了终端、别的窗口）也锁（10-16）。
  // 等一下再看 hasFocus：点进页面里的 iframe 也会先来 blur，那种焦点还在这一页。选文件的系统对话框、登记 Touch ID 的系统弹框也会抢走焦点，那段不算。
  // 回到窗口：光标放回密码框；有系统指纹框就自动弹（autoTouch 自己挡掉「框刚收起、焦点回来」那一下）。
  let picking = false;
  document.addEventListener('click', e => { if (e.target.closest?.('input[type=file]')) picking = true; }, true);
  addEventListener('focus', () => { picking = false; box?.querySelector('.lock-input')?.focus(); autoTouch(); });
  addEventListener('blur', () => {
    if (!on || box || leaving || picking || hold) return;
    setTimeout(() => {
      if (!on || box || leaving || picking || hold || document.hasFocus()) return;
      show(); beacon('/api/lock/lock');
    }, 150);
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

  // 设置里用：刚设好 / 去掉密码，这一页不用重载就开始 / 停止管；登记 Touch ID（系统弹框会抢焦点，那段别锁）
  window.ephLock = {
    set(v) { on = !!v; if (!on) { hide(); touchOn = false; } app?.postMessage({ on }); },
    appUnlocked() { if (isLockPage || missed) location.reload(); },
    canTouch: () => location.hostname === 'localhost' && !!window.PublicKeyCredential,
    async addTouch() {
      const c = await post('/api/lock/touch/begin', { kind: 'create' });
      if (!c?.ok) return { error: c?.error || '连不上：Ephemeris 还开着吗？' };
      hold = true;
      try {
        const cred = await navigator.credentials.create({ publicKey: {
          challenge: unb64(c.challenge), rp: { id: c.rp, name: 'Ephemeris' },
          user: { id: crypto.getRandomValues(new Uint8Array(16)), name: 'Ephemeris', displayName: 'Ephemeris' },
          pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
          authenticatorSelection: { authenticatorAttachment: 'platform', userVerification: 'required', residentKey: 'discouraged' },
          excludeCredentials: (c.ids || []).map(id => ({ type: 'public-key', id: unb64(id) })),
          attestation: 'none', timeout: 60000 } });
        const r = await post('/api/lock/touch/add', { id: b64(cred.rawId), spki: b64(cred.response.getPublicKey()),
          alg: cred.response.getPublicKeyAlgorithm(), clientData: b64(cred.response.clientDataJSON) });
        if (r?.ok) touchOn = true;
        return r?.ok ? { ok: true } : { error: r?.error || '没存上' };
      } catch (e) {
        return { error: e.name === 'InvalidStateError' ? '这台 Mac 已经登记过了' : '取消了，或者这台电脑用不了 Touch ID' };
      } finally {
        setTimeout(() => { hold = false; }, 300);
      }
    },
    async removeTouch() {
      const r = await post('/api/lock/touch/remove');
      if (r?.ok) touchOn = false;
      return r?.ok ? { ok: true } : { error: r?.error || '没去掉' };
    },
  };
})();
