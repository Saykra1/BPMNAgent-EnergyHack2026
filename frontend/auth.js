/* Registration, login and the access model of the UI.
   Loaded before app.js: every /api request gets the session token and, inside a team project, the
   project id, so the server checks the member's roles. Without a session (and REQUIRE_LOGIN on) the
   page shows the login / registration screen. window.access.can(permission) is what other modules
   use to lock what the current roles do not allow; outside a team project everything is allowed. */
(() => {
  const TOKEN = 'bpmn-agent-token';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };
  const escHtml = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ALL = ['view', 'edit_content', 'edit_bpmn', 'generate', 'edit_code', 'run', 'delete_projects'];

  const session = { token: store.get(TOKEN), me: null, config: null, project: null };
  window.session = session;

  // ------------------------------------------------------------------ fetch with credentials
  const rawFetch = window.fetch.bind(window);
  function isApi(url) {
    try { const u = new URL(url, location.href); return u.origin === location.origin && u.pathname.startsWith('/api/'); }
    catch { return false; }
  }
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!isApi(url)) return rawFetch(input, init);
    const headers = new Headers(init.headers || (typeof input === 'string' ? {} : input.headers));
    if (session.token && !headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + session.token);
    if (session.project && !headers.has('X-Project-Id')) headers.set('X-Project-Id', session.project.id);
    return rawFetch(input, { ...init, headers }).then(r => {
      if (r.status === 401 && !new URL(url, location.href).pathname.startsWith('/api/auth/')) onUnauthorized();
      return r;
    });
  };
  // links to /api files (documents, archives) are downloaded through fetch so they carry the token
  async function authDownload(url, name) {
    const r = await window.fetch(url);
    if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.detail || 'Не удалось скачать файл'); return; }
    let filename = name;
    const cd = r.headers.get('content-disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="([^"]+)"/i);
    if (!filename && m) filename = decodeURIComponent(m[1]);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob()); a.download = filename || 'file'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
  }
  window.authDownload = authDownload;
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[href]');
    if (!a || !isApi(a.getAttribute('href'))) return;
    e.preventDefault();
    authDownload(a.getAttribute('href'), a.getAttribute('download') || '');
  }, true);

  // ------------------------------------------------------------------ access model
  window.access = {
    perms() { return session.project ? new Set(session.project.permissions || []) : new Set(ALL); },
    can(p) { return this.perms().has(p); },
    inProject() { return !!session.project; },
  };

  // ------------------------------------------------------------------ API helpers
  async function call(path, method = 'GET', body) {
    const r = await window.fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(typeof data.detail === 'string' ? data.detail : (Array.isArray(data.detail) ? 'Проверьте поля формы' : 'Запрос не выполнен'));
      err.status = r.status; throw err;
    }
    return data;
  }
  window.authApi = call;

  // ------------------------------------------------------------------ login / registration screen
  let shown = false;
  function onUnauthorized() {
    if (session.config && !session.config.require_login && !session.token) return;
    if (session.token) { session.token = null; store.set(TOKEN, null); }
    if (session.config?.require_login) showAuth();
  }
  function showAuth(mode = 'login', note = '') {
    if (shown) return;
    shown = true;
    const box = document.createElement('div');
    box.id = 'auth-screen';
    box.innerHTML = `<div class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title">
      <div class="auth-brand"><span class="brand-mark" aria-hidden="true">◇</span><div><strong>Архитектор процессов</strong><span>Текст → BPMN 2.0</span></div></div>
      <div class="auth-tabs" role="tablist"><button type="button" role="tab" data-mode="login">Вход</button><button type="button" role="tab" data-mode="register">Регистрация</button></div>
      <h2 id="auth-title"></h2>
      ${note ? `<p class="auth-note">${escHtml(note)}</p>` : ''}
      <form id="auth-form" novalidate>
        <div data-only="register"><label>Имя — как вас увидят коллеги<input name="name" autocomplete="name" maxlength="80"></label></div>
        <div data-only="register"><label>Почта<input name="email" type="email" autocomplete="email" maxlength="254"></label></div>
        <label><span data-only="login">Логин, почта или ID</span><span data-only="register">Логин (латиница, цифры, . _ -)</span>
          <input name="login" autocomplete="username" maxlength="254" required></label>
        <label>Пароль<input name="password" type="password" autocomplete="current-password" maxlength="200" required></label>
        <div data-only="register"><label>Пароль ещё раз<input name="password2" type="password" autocomplete="new-password" maxlength="200"></label>
          <p class="hint">Не короче 8 символов. Пароль хранится только в виде хэша — его не видит даже администратор.</p></div>
        <p class="be-error" id="auth-error" role="alert"></p>
        <button class="primary" type="submit" id="auth-submit"></button>
      </form></div>`;
    document.body.append(box);
    const form = box.querySelector('#auth-form');
    const setMode = (m) => {
      mode = m;
      box.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.mode === m)));
      box.querySelectorAll('[data-only]').forEach(el => { el.hidden = el.dataset.only !== m; });
      box.querySelector('#auth-title').textContent = m === 'login' ? 'Войдите, чтобы продолжить' : 'Создайте аккаунт';
      box.querySelector('#auth-submit').textContent = m === 'login' ? 'Войти' : 'Зарегистрироваться';
      form.password.autocomplete = m === 'login' ? 'current-password' : 'new-password';
      box.querySelector('#auth-error').textContent = '';
      (m === 'login' ? form.login : form.name).focus();
    };
    box.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => setMode(b.dataset.mode));
    form.onsubmit = async (e) => {
      e.preventDefault();
      const err = box.querySelector('#auth-error');
      const f = Object.fromEntries(new FormData(form));
      try {
        if (mode === 'register' && f.password !== f.password2) throw new Error('Пароли не совпадают');
        box.querySelector('#auth-submit').disabled = true;
        const res = mode === 'login'
          ? await call('/api/auth/login', 'POST', { login: f.login, password: f.password })
          : await call('/api/auth/register', 'POST', { login: f.login, email: f.email, name: f.name, password: f.password });
        store.set(TOKEN, res.token);
        location.reload();
      } catch (ex) { err.textContent = ex.message; box.querySelector('#auth-submit').disabled = false; }
    };
    setMode(mode);
  }
  window.showAuth = showAuth;

  async function logout() {
    try { await call('/api/auth/logout', 'POST'); } catch { /* already gone */ }
    store.set(TOKEN, null);
    location.href = '/';
  }

  // ------------------------------------------------------------------ account chip in the top bar
  function renderChip() {
    const right = document.querySelector('.topbar-right');
    if (!right) return;
    let chip = document.getElementById('account');
    if (!chip) {
      chip = document.createElement('div');
      chip.id = 'account'; chip.className = 'account';
      right.prepend(chip);
    }
    if (!session.me) {
      chip.innerHTML = `<button type="button" class="topbar-button" id="login-open">Войти</button>`;
      chip.querySelector('#login-open').onclick = () => showAuth();
      return;
    }
    const u = session.me.user;
    const pending = session.me.invites.length;
    chip.innerHTML = `<button type="button" class="topbar-button" id="teams-open" data-help="Команды, проекты, роли и приглашения.">Команды${pending ? ` <span class="badge-count" title="Приглашения">${pending}</span>` : ''}</button>
      <details class="account-menu"><summary class="topbar-button" aria-label="Аккаунт">${escHtml(u.name)}</summary>
        <div class="account-pop"><strong>${escHtml(u.name)}</strong><span class="muted">${escHtml(u.login)} · ${escHtml(u.email)}</span>
          <span>Ваш ID для приглашений: <code id="my-id">#${escHtml(u.id.slice(0, 4))}-${escHtml(u.id.slice(4))}</code> <button type="button" class="quiet" id="copy-id">Копировать</button></span>
          <button type="button" id="profile-open">Профиль и пароль</button><button type="button" id="logout">Выйти</button></div></details>`;
    chip.querySelector('#teams-open').onclick = () => window.teams?.open();
    chip.querySelector('#logout').onclick = logout;
    chip.querySelector('#copy-id').onclick = () => navigator.clipboard?.writeText(chip.querySelector('#my-id').textContent).catch(() => {});
    chip.querySelector('#profile-open').onclick = openProfile;
  }
  window.renderAccount = renderChip;

  function openProfile() {
    document.querySelector('.account-menu')?.removeAttribute('open');
    const u = session.me.user;
    const wrap = document.createElement('div');
    wrap.id = 'be-modal';
    wrap.innerHTML = `<div class="be-backdrop"></div><div class="be-dialog" role="dialog" aria-modal="true" aria-label="Профиль">
      <div class="be-head"><h3>Профиль</h3><button class="icon-button be-x" aria-label="Закрыть">×</button></div>
      <form class="be-body" novalidate><fieldset><legend>Данные</legend>
        <label>Имя<input id="pf-name" value="${escHtml(u.name)}" maxlength="80"></label>
        <p class="hint">Логин: ${escHtml(u.login)} · почта: ${escHtml(u.email)} · ID: #${escHtml(u.id)}</p>
        <button type="button" id="pf-save-name">Сохранить имя</button></fieldset>
        <fieldset><legend>Сменить пароль</legend>
        <label>Текущий пароль<input id="pf-old" type="password" autocomplete="current-password"></label>
        <label>Новый пароль<input id="pf-new" type="password" autocomplete="new-password"></label>
        <p class="hint">После смены пароля другие сеансы закрываются.</p>
        <button type="button" id="pf-save-pass">Сменить пароль</button></fieldset></form>
      <p class="be-error" role="alert"></p></div>`;
    document.body.append(wrap);
    const close = () => wrap.remove();
    const err = wrap.querySelector('.be-error');
    wrap.querySelector('.be-x').onclick = close; wrap.querySelector('.be-backdrop').onclick = close;
    wrap.querySelector('#pf-save-name').onclick = async () => {
      try { session.me = await call('/api/auth/me', 'PATCH', { name: wrap.querySelector('#pf-name').value }); renderChip(); err.textContent = 'Имя сохранено'; }
      catch (e) { err.textContent = e.message; }
    };
    wrap.querySelector('#pf-save-pass').onclick = async () => {
      try { await call('/api/auth/password', 'POST', { old_password: wrap.querySelector('#pf-old').value, new_password: wrap.querySelector('#pf-new').value }); err.textContent = 'Пароль изменён'; }
      catch (e) { err.textContent = e.message; }
    };
  }

  // ------------------------------------------------------------------ start
  session.ready = (async () => {
    try { session.config = await call('/api/auth/config'); } catch { session.config = { require_login: false }; }
    if (session.token) {
      try { session.me = await call('/api/auth/me'); }
      catch { session.token = null; store.set(TOKEN, null); }
    }
    if (!session.me && session.config.require_login) {
      const join = location.pathname.startsWith('/join/');
      showAuth(join ? 'register' : 'login', join ? 'Вас пригласили в команду. Войдите или зарегистрируйтесь, чтобы ответить на приглашение.' : '');
    }
    const ready = () => renderChip();
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
    return session.me;
  })();
})();
