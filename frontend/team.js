/* Teams: invitations (by e-mail, login or personal id, plus links), accept / decline, team roles that
   the head creates and configures permission by permission (with project visibility), members and
   shared projects. Inside a team project the UI follows the member's permissions; the server checks
   them again on every save and on LLM / process-run calls.
   Based on the team panel of the Asya_feature_MakeTeam branch, rebuilt for the current interface. */
(() => {
  const TEAM_KEY = 'bpmn-agent-team';
  const call = (...a) => window.authApi(...a);
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* unavailable */ } },
  };
  let team = null, tab = 'projects', dirty = false;

  // ================================================================== panel shell
  const panel = document.createElement('aside');
  panel.id = 'team-panel';
  panel.setAttribute('aria-label', 'Команды');
  panel.innerHTML = `<div class="run-head"><div><span class="eyebrow">СОВМЕСТНАЯ РАБОТА</span><h2>Команды</h2></div>
    <button class="icon-button" id="team-close" aria-label="Закрыть">×</button></div><div class="run-body" id="team-body"></div>`;
  document.body.append(panel);
  panel.querySelector('#team-close').onclick = () => document.body.classList.remove('team-open');

  async function open(teamId) {
    if (!window.session.me) { window.showAuth(); return; }
    document.body.classList.add('team-open');
    document.body.classList.remove('run-open');
    await refresh(teamId ?? store.get(TEAM_KEY));
  }
  function status(msg, bad = false) {
    const el = panel.querySelector('#team-status');
    if (el) { el.textContent = msg || ''; el.classList.toggle('bad', bad); }
  }
  async function refresh(teamId) {
    window.session.me = await call('/api/auth/me');
    window.renderAccount?.();
    team = null;
    if (teamId && window.session.me.teams.some(t => t.id === teamId)) {
      try { team = await call('/api/teams/' + teamId); store.set(TEAM_KEY, teamId); } catch { store.set(TEAM_KEY, null); }
    }
    render();
  }

  // ================================================================== rendering
  const can = (p) => team && (team.is_owner || team.permissions.includes(p));
  const roleChip = (r) => `<span class="role-chip" style="--c:${h(r.color)}">${h(r.name)}</span>`;
  const rolesById = () => Object.fromEntries((team?.roles || []).map(r => [r.id, r]));

  function render() {
    const me = window.session.me;
    const invites = me.invites.map(i => `<div class="run-card invite-card"><strong>${h(i.team_name)}</strong>
        <p class="muted">Приглашает ${h(i.invited_by)}${i.roles.length ? ' · роли: ' + i.roles.map(h).join(', ') : ' · без ролей'}</p>
        <div class="be-row"><button class="primary" data-accept="${h(i.id)}">Принять</button><button data-decline="${h(i.id)}">Отклонить</button></div></div>`).join('');
    const list = me.teams.map(t => `<button type="button" class="team-tab ${team?.id === t.id ? 'active' : ''}" data-team="${h(t.id)}">
        ${h(t.name)} <span class="muted">${t.is_owner ? 'глава' : (t.roles.join(', ') || 'без ролей')}</span></button>`).join('');
    panel.querySelector('#team-body').innerHTML = `
      <p id="team-status" class="hint" role="status"></p>
      ${invites ? `<h3>Приглашения</h3>${invites}` : ''}
      <h3>Мои команды</h3>
      <div class="team-switch">${list || '<p class="muted">Пока нет команд. Создайте свою — вы станете её главой — или примите приглашение.</p>'}</div>
      <form class="be-row" id="team-create"><input id="team-new-name" maxlength="80" placeholder="Название новой команды" aria-label="Название новой команды"><button class="primary" type="submit">Создать команду</button></form>
      ${team ? renderTeam() : ''}`;
    bind();
  }

  function renderTeam() {
    if (team.no_access) {
      return `<div class="team-view"><h3>${h(team.name)}</h3><div class="run-card"><strong>У вас пока нет ролей в этой команде.</strong>
        <p>Без роли ничего не видно. Глава команды назначит вам роль — после этого появятся проекты.</p>
        <button data-leave>Покинуть команду</button></div></div>`;
    }
    const tabs = [['projects', 'Проекты'], ['members', 'Участники'], ['roles', 'Роли'],
      ...(can('manage_members') ? [['invites', 'Приглашения']] : []), ...(team.is_owner ? [['settings', 'Настройки']] : [])];
    if (!tabs.some(([id]) => id === tab)) tab = 'projects';
    const body = { projects: renderProjects, members: renderMembers, roles: renderRoles, invites: renderInvites, settings: renderSettings }[tab]();
    const mine = team.is_owner ? '<span class="role-chip owner">глава команды</span>' : team.my_roles.map(id => rolesById()[id]).filter(Boolean).map(roleChip).join(' ');
    return `<div class="team-view"><h3>${h(team.name)}</h3><p>Вы: ${mine}</p>
      <div class="team-tabs" role="tablist">${tabs.map(([id, t]) => `<button type="button" role="tab" aria-selected="${id === tab}" data-tab="${id}">${t}</button>`).join('')}</div>
      ${body}</div>`;
  }

  function renderProjects() {
    const items = team.projects.map(p => `<li><button type="button" class="link-like" data-open="${h(p.id)}">${h(p.title)}</button>
      <span class="muted">версия ${p.version}${p.updated_by_name ? ' · ' + h(p.updated_by_name) : ''} · права: ${permSummary(p.permissions)}</span>
      ${p.permissions.includes('delete_projects') ? `<button type="button" class="quiet danger" data-delete="${h(p.id)}">Удалить</button>` : ''}</li>`).join('');
    return `${can('create_projects') ? `<form class="be-row" id="project-create"><input id="project-new" maxlength="200" placeholder="Название новой схемы" aria-label="Название проекта"><button type="submit">Новый проект</button></form>
      <label class="be-check"><input type="checkbox" id="project-from-current" ${state.hasDiagram ? 'checked' : 'disabled'}> Взять текущую схему с холста</label>` : ''}
      <ul class="team-list">${items || '<li class="muted">Видимых вам проектов нет.</li>'}</ul>`;
  }
  function permSummary(perms) {
    const names = window.session.config.permissions.filter(p => perms.includes(p.id) && p.id !== 'view').map(p => p.name.toLowerCase());
    return names.length ? h(names.join(', ')) : 'только просмотр';
  }

  function renderMembers() {
    const rb = rolesById();
    const meId = window.session.me.user.id;
    return `<ul class="team-list">${team.members.map(m => `<li>
      <div><b>${h(m.name)}</b> <span class="muted">${h(m.login)}</span><br>
      ${m.is_owner ? '<span class="role-chip owner">глава</span>' : (m.role_ids.map(id => rb[id]).filter(Boolean).map(roleChip).join(' ') || '<span class="muted">без ролей — ничего не видит</span>')}</div>
      <div class="be-row">
      ${!m.is_owner && can('manage_members') ? `<button type="button" data-member-roles="${h(m.id)}">Роли</button>` : ''}
      ${!m.is_owner && m.id !== meId && can('manage_members') ? `<button type="button" class="quiet danger" data-kick="${h(m.id)}">Исключить</button>` : ''}
      ${!m.is_owner && m.id === meId ? '<button type="button" class="quiet danger" data-leave>Покинуть команду</button>' : ''}</div></li>`).join('')}</ul>`;
  }

  function renderRoles() {
    const cat = window.session.config.permissions;
    const cards = team.roles.map(r => `<div class="role-card" style="--c:${h(r.color)}">
      <div class="be-row"><strong>${roleChip(r)}</strong><span class="be-spacer"></span>
      ${can('manage_roles') ? `<button type="button" data-role-edit="${h(r.id)}">Настроить</button>` : ''}</div>
      <p class="muted">${r.permissions.length ? h(cat.filter(p => r.permissions.includes(p.id)).map(p => p.name).join(' · ')) : 'Нет прав: участник с этой ролью ничего не видит.'}</p>
      <p class="hint">Проекты: ${r.scope === null ? 'все' : r.scope.length ? h(r.scope.map(id => team.projects.find(p => p.id === id)?.title || '—').join(', ')) : 'ни одного'}</p></div>`).join('');
    return `${can('manage_roles') ? '<button type="button" class="primary" data-role-new>+ Новая роль</button>' : '<p class="hint">Роли настраивает глава команды или участник с правом «Настройка ролей».</p>'}
      ${cards || '<p class="muted">Ролей нет.</p>'}`;
  }

  function roleChecks(selected = [], name = 'inv-role') {
    return team.roles.map(r => {
      const managing = r.permissions.some(p => p === 'manage_members' || p === 'manage_roles');
      const locked = managing && !team.is_owner;
      return `<label class="be-chip"><input type="checkbox" name="${name}" value="${h(r.id)}" ${selected.includes(r.id) ? 'checked' : ''} ${locked ? 'disabled title="Управляющие роли выдаёт только глава"' : ''}> ${roleChip(r)}</label>`;
    }).join('');
  }

  function renderInvites() {
    const rb = rolesById();
    return `<form id="invite-form"><label>Почта, логин или ID пользователя<input id="invite-who" maxlength="254" placeholder="ivanov@example.com, ivanov или #7KQ2-M9XA"></label>
      <div class="be-label">Роли после принятия</div><div class="be-chips">${roleChecks([], 'inv-role')}</div>
      <button class="primary" type="submit">Пригласить</button>
      <p class="hint">Человек увидит приглашение в разделе «Команды» и сможет принять или отклонить его. Если почта ещё не зарегистрирована, приглашение появится после регистрации.</p></form>
      <h4>Ждут ответа</h4><ul class="team-list">${team.invites.map(i => `<li><span>${h(i.to)} <span class="muted">${i.roles.map(h).join(', ') || 'без ролей'}</span></span>
        <button type="button" class="quiet danger" data-cancel-invite="${h(i.id)}">Отозвать</button></li>`).join('') || '<li class="muted">Нет</li>'}</ul>
      <h4>Ссылка-приглашение</h4><p class="hint">По ссылке человек входит или регистрируется и сам решает, принять ли приглашение.</p>
      <div class="be-chips">${roleChecks([], 'link-role')}</div><button type="button" id="link-create">Создать ссылку</button>
      <ul class="team-list">${team.links.map(l => { const url = location.origin + '/join/' + l.token; return `<li>
        <input readonly value="${h(url)}" aria-label="Ссылка"><span class="muted">${l.role_ids.map(id => rb[id]?.name).filter(Boolean).map(h).join(', ') || 'без ролей'}</span>
        <button type="button" data-copy="${h(url)}">Копировать</button><button type="button" class="quiet danger" data-revoke="${h(l.token)}">Отозвать</button></li>`; }).join('')}</ul>`;
  }

  function renderSettings() {
    return `<form id="team-rename" class="be-row"><label>Название команды<input id="team-name" value="${h(team.name)}" maxlength="80"></label><button type="submit">Сохранить</button></form>
      <div class="run-card bad"><strong>Удалить команду</strong><p>Будут удалены все проекты, роли и приглашения команды.</p>
      <button type="button" class="danger" id="team-delete">Удалить команду</button></div>`;
  }

  // ================================================================== actions
  async function act(fn, ok) {
    try { const r = await fn(); if (r && r.id && r.roles) { team = r; } if (r?.notice) ok = r.notice; render(); if (ok) status(ok); return r; }
    catch (e) { status(e.message, true); return null; }
  }
  function checked(name) { return [...panel.querySelectorAll(`input[name="${name}"]:checked`)].map(i => i.value); }

  function bind() {
    const on = (sel, fn) => panel.querySelectorAll(sel).forEach(el => { el.onclick = (e) => { e.preventDefault(); fn(el); }; });
    panel.querySelector('#team-create').onsubmit = async (e) => {
      e.preventDefault();
      const name = panel.querySelector('#team-new-name').value.trim();
      if (!name) return;
      try { const t = await call('/api/teams', 'POST', { name }); tab = 'roles'; await refresh(t.id); status('Команда создана. Вы — её глава. Для старта созданы роли «Разработчик», «Администратор», «Наблюдатель» — их можно изменить.'); }
      catch (err) { status(err.message, true); }
    };
    on('[data-team]', el => { tab = 'projects'; refresh(el.dataset.team); });
    on('[data-accept]', async el => { try { const r = await call(`/api/invites/${el.dataset.accept}/accept`, 'POST'); await refresh(r.team_id); status('Вы вступили в команду'); } catch (e) { status(e.message, true); } });
    on('[data-decline]', async el => { try { await call(`/api/invites/${el.dataset.decline}/decline`, 'POST'); await refresh(team?.id); status('Приглашение отклонено'); } catch (e) { status(e.message, true); } });
    on('[data-tab]', el => { tab = el.dataset.tab; refresh(team?.id).catch(e => status(e.message, true)); });
    if (!team) return;
    on('[data-leave]', async () => {
      if (!confirm(`Покинуть команду «${team.name}»?`)) return;
      try { await call(`/api/teams/${team.id}/members/${window.session.me.user.id}`, 'DELETE'); if (window.session.project?.team_id === team.id) closeProject(); store.set(TEAM_KEY, null); await refresh(null); }
      catch (e) { status(e.message, true); }
    });
    // projects
    const pc = panel.querySelector('#project-create');
    if (pc) pc.onsubmit = async (e) => {
      e.preventDefault();
      const title = panel.querySelector('#project-new').value.trim();
      if (!title) return;
      try {
        let p = await call(`/api/teams/${team.id}/projects`, 'POST', { title });
        if (panel.querySelector('#project-from-current')?.checked && state.hasDiagram) {
          const snap = await snapshot();
          p = await call('/api/projects/' + p.id, 'PUT', { ...snap, title, version: p.version });
        }
        await openProject(p);
        await refresh(team.id);
      } catch (err) { status(err.message, true); }
    };
    on('[data-open]', async el => { try { await openProject(await call('/api/projects/' + el.dataset.open)); } catch (e) { status(e.message, true); } });
    on('[data-delete]', async el => {
      if (!confirm('Удалить проект для всей команды?')) return;
      try { await call('/api/projects/' + el.dataset.delete, 'DELETE'); if (window.session.project?.id === el.dataset.delete) closeProject(); await refresh(team.id); }
      catch (e) { status(e.message, true); }
    });
    // members
    on('[data-member-roles]', el => memberRolesDialog(team.members.find(m => m.id === el.dataset.memberRoles)));
    on('[data-kick]', async el => {
      const m = team.members.find(x => x.id === el.dataset.kick);
      if (!confirm(`Исключить ${m?.name || 'участника'} из команды?`)) return;
      act(() => call(`/api/teams/${team.id}/members/${el.dataset.kick}`, 'DELETE'), 'Участник исключён');
    });
    // roles
    on('[data-role-new]', () => roleDialog(null));
    on('[data-role-edit]', el => roleDialog(team.roles.find(r => r.id === el.dataset.roleEdit)));
    // invitations
    const inv = panel.querySelector('#invite-form');
    if (inv) inv.onsubmit = (e) => {
      e.preventDefault();
      const who = panel.querySelector('#invite-who').value.trim();
      if (!who) { status('Укажите почту, логин или ID', true); return; }
      act(() => call(`/api/teams/${team.id}/invites`, 'POST', { who, role_ids: checked('inv-role') }));
    };
    on('[data-cancel-invite]', el => act(() => call('/api/invites/' + el.dataset.cancelInvite, 'DELETE'), 'Приглашение отозвано'));
    const lc = panel.querySelector('#link-create');
    if (lc) lc.onclick = async () => { try { await call(`/api/teams/${team.id}/links`, 'POST', { role_ids: checked('link-role') }); await refresh(team.id); status('Ссылка создана'); } catch (e) { status(e.message, true); } };
    on('[data-copy]', async el => { try { await navigator.clipboard.writeText(el.dataset.copy); status('Ссылка скопирована'); } catch { status('Скопируйте ссылку из поля'); } });
    on('[data-revoke]', async el => { try { await call('/api/links/' + el.dataset.revoke, 'DELETE'); await refresh(team.id); status('Ссылка отозвана'); } catch (e) { status(e.message, true); } });
    // settings
    const rn = panel.querySelector('#team-rename');
    if (rn) rn.onsubmit = (e) => { e.preventDefault(); act(() => call(`/api/teams/${team.id}`, 'PATCH', { name: panel.querySelector('#team-name').value }), 'Сохранено').then(() => refresh(team.id)); };
    const del = panel.querySelector('#team-delete');
    if (del) del.onclick = async () => {
      if (prompt(`Чтобы удалить команду, введите её название: ${team.name}`) !== team.name) return;
      try { await call(`/api/teams/${team.id}`, 'DELETE'); if (window.session.project?.team_id === team.id) closeProject(); store.set(TEAM_KEY, null); await refresh(null); status('Команда удалена'); }
      catch (e) { status(e.message, true); }
    };
  }

  // ================================================================== dialogs
  function modal(title, html, onSave) {
    document.getElementById('be-modal')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'be-modal';
    wrap.innerHTML = `<div class="be-backdrop"></div><div class="be-dialog" role="dialog" aria-modal="true" aria-label="${h(title)}">
      <div class="be-head"><h3>${h(title)}</h3><button class="icon-button be-x" aria-label="Закрыть">×</button></div>
      <form class="be-body" novalidate>${html}</form><p class="be-error" role="alert"></p>
      <div class="be-foot"><span class="be-extra"></span><span class="be-spacer"></span><button type="button" class="be-cancel">Отмена</button><button type="button" class="primary be-save">Сохранить</button></div></div>`;
    document.body.append(wrap);
    const close = () => wrap.remove();
    wrap.querySelector('.be-x').onclick = close; wrap.querySelector('.be-cancel').onclick = close; wrap.querySelector('.be-backdrop').onclick = close;
    wrap.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    wrap.querySelector('.be-save').onclick = async () => {
      try { const r = await onSave(wrap); if (r) { team = r; render(); } close(); }
      catch (e) { wrap.querySelector('.be-error').textContent = e.message; }
    };
    wrap.querySelector('input, select, textarea')?.focus();
    return wrap;
  }

  function memberRolesDialog(m) {
    const w = modal(`Роли: ${m.name}`, `<p class="hint">Права участника — объединение прав всех его ролей. Без ролей он ничего не видит.</p>
      <div class="be-chips be-col">${roleChecks(m.role_ids, 'mr')}</div>`,
    (w) => call(`/api/teams/${team.id}/members/${m.id}/roles`, 'PUT', { role_ids: [...w.querySelectorAll('input[name="mr"]:checked')].map(i => i.value) }));
    return w;
  }

  function roleDialog(role) {
    const cat = window.session.config.permissions;
    const perms = new Set(role?.permissions || ['view']);
    const scope = role ? role.scope : null;
    const group = (g, title) => `<fieldset><legend>${title}</legend>${cat.filter(p => p.group === g).map(p => {
      const managing = p.id === 'manage_members' || p.id === 'manage_roles';
      return `<label class="perm-row"><input type="checkbox" name="perm" value="${p.id}" ${perms.has(p.id) ? 'checked' : ''} ${managing && !team.is_owner ? 'disabled' : ''}>
        <span><strong>${h(p.name)}</strong><span class="hint">${h(p.hint)}${managing && !team.is_owner ? ' · выдаёт только глава' : ''}</span></span></label>`; }).join('')}</fieldset>`;
    const w = modal(role ? `Роль «${role.name}»` : 'Новая роль', `
      <div class="be-row"><label>Название<input id="role-name" value="${h(role?.name || '')}" maxlength="60" placeholder="Например, Юрист"></label>
      <label class="role-color">Цвет<input id="role-color" type="color" value="${h(role?.color || '#3f7fbf')}"></label></div>
      ${group('project', 'Что можно делать в проектах')}
      ${group('team', 'Что можно делать в команде')}
      <fieldset><legend>Область видимости — какие проекты видит роль</legend>
        <label class="be-check"><input type="radio" name="scope" value="all" ${scope === null ? 'checked' : ''}> Все проекты команды</label><br>
        <label class="be-check"><input type="radio" name="scope" value="some" ${scope !== null ? 'checked' : ''}> Только выбранные:</label>
        <div class="be-chips be-col" id="scope-list">${team.projects.map(p => `<label class="be-chip"><input type="checkbox" name="scope-p" value="${h(p.id)}" ${scope?.includes(p.id) ? 'checked' : ''}> ${h(p.title)}</label>`).join('') || '<span class="muted">Проектов пока нет</span>'}</div>
        <p class="hint">Любое право в проектах включает просмотр. Права действуют только в проектах из области видимости.</p></fieldset>`,
    (w) => {
      const body = { name: w.querySelector('#role-name').value.trim(), color: w.querySelector('#role-color').value,
        permissions: [...w.querySelectorAll('input[name="perm"]:checked')].map(i => i.value),
        scope: w.querySelector('input[name="scope"]:checked').value === 'all' ? null : [...w.querySelectorAll('input[name="scope-p"]:checked')].map(i => i.value) };
      if (!body.name) throw new Error('Введите название роли');
      return role ? call('/api/roles/' + role.id, 'PUT', body) : call(`/api/teams/${team.id}/roles`, 'POST', body);
    });
    // any project permission implies "view"
    w.querySelectorAll('input[name="perm"]').forEach(i => i.addEventListener('change', () => {
      const projectPerms = cat.filter(p => p.group === 'project' && p.id !== 'view').map(p => p.id);
      const view = w.querySelector('input[name="perm"][value="view"]');
      if (i.checked && projectPerms.includes(i.value)) view.checked = true;
      if (i === view && !view.checked) w.querySelectorAll('input[name="perm"]').forEach(x => { if (projectPerms.includes(x.value)) x.checked = false; });
    }));
    if (role) {
      const del = document.createElement('button');
      del.type = 'button'; del.className = 'be-delete'; del.textContent = 'Удалить роль';
      del.onclick = async () => {
        if (!confirm(`Удалить роль «${role.name}»? Участники потеряют её права.`)) return;
        try { team = await call('/api/roles/' + role.id, 'DELETE'); render(); w.remove(); }
        catch (e) { w.querySelector('.be-error').textContent = e.message; }
      };
      w.querySelector('.be-extra').append(del);
    }
  }

  // ================================================================== project workspace
  async function snapshot() {
    const xml = state.hasDiagram ? (await modeler.saveXML({ format: true })).xml : '';
    return { text: $('#text').value, xml, code: $('#code').value || state.code || '', plan: state.plan };
  }
  async function openProject(p) {
    if (dirty && !confirm('В текущем проекте есть несохранённые изменения. Открыть другой проект?')) return;
    window.session.project = { id: p.id, team_id: p.team_id, team_name: p.team_name, title: p.title, version: p.version,
      permissions: p.permissions, updated_by_name: p.updated_by_name };
    state.text = p.text || '';
    $('#text').value = state.text;
    state.plan = p.plan || null;
    state.code = p.code || '';
    $('#code').value = state.code;
    window.agentFeatures?.clearInterview?.();
    if (p.xml) {
      await applyResult({ xml: p.xml, code: p.code || '', plan: p.plan, issues: [], xsd_errors: [], stats: {}, assumptions: [],
        questions: [], attempts: [], source: 'manual', duration_s: 0 });
    } else {
      try { modeler.clear(); } catch { /* empty canvas */ }
      state.hasDiagram = false;
      $('#empty').hidden = false;
      syncDiagramActions();
    }
    dirty = false;
    applyPermissions();
    document.body.classList.remove('team-open');
    $('#summary').innerHTML = `<span class="pill ok">проект</span> ${h(p.title)} · версия ${p.version}`;
  }
  function closeProject() {
    window.session.project = null;
    dirty = false;
    applyPermissions();
  }
  async function saveProject() {
    const cur = window.session.project;
    if (!cur) return;
    try {
      busy(true, 'Сохраняю в команду…');
      const snap = await snapshot();
      const saved = await call('/api/projects/' + cur.id, 'PUT', { ...snap, title: cur.title, version: cur.version });
      Object.assign(cur, { version: saved.version, permissions: saved.permissions, updated_by_name: saved.updated_by_name });
      dirty = false;
      $('#summary').innerHTML = `<span class="pill ok">сохранено</span> «${h(cur.title)}», версия ${saved.version}`;
    } catch (e) { alert(e.message); }
    finally { busy(false); applyPermissions(); }
  }
  async function reloadProject() {
    const cur = window.session.project;
    if (!cur) return;
    if (dirty && !confirm('Загрузить сохранённую версию? Несохранённые правки будут заменены.')) return;
    dirty = false;
    try { await openProject(await call('/api/projects/' + cur.id)); } catch (e) { alert(e.message); }
  }
  window.addEventListener('beforeunload', (e) => { if (dirty && window.session.project) { e.preventDefault(); e.returnValue = ''; } });

  // project bar in the workspace header
  const bar = document.createElement('div');
  bar.id = 'project-bar'; bar.hidden = true;
  document.querySelector('.workspace-heading')?.after(bar);
  function renderBar() {
    const cur = window.session.project;
    bar.hidden = !cur;
    if (!cur) return;
    const canSave = ['edit_content', 'edit_bpmn', 'generate', 'edit_code'].some(p => cur.permissions.includes(p));
    bar.innerHTML = `<span class="pill ${dirty ? 'warn' : 'ok'}">${dirty ? 'есть изменения' : 'сохранено'}</span>
      <span><strong>${h(cur.title)}</strong> <span class="muted">· ${h(cur.team_name)} · версия ${cur.version}${cur.updated_by_name ? ' · ' + h(cur.updated_by_name) : ''}</span></span>
      ${canSave ? '<button type="button" class="primary" id="project-save">Сохранить</button>' : '<span class="muted">только просмотр</span>'}
      <button type="button" id="project-reload" title="Загрузить последнюю сохранённую версию">Обновить</button>
      <button type="button" class="quiet" id="project-close" title="Продолжить без проекта (личная работа)">Закрыть проект</button>`;
    bar.querySelector('#project-save')?.addEventListener('click', saveProject);
    bar.querySelector('#project-reload').onclick = reloadProject;
    bar.querySelector('#project-close').onclick = () => {
      if (dirty && !confirm('Есть несохранённые изменения. Закрыть проект? Схема останется на экране как личная копия.')) return;
      closeProject();
      $('#summary').innerHTML = '<span class="pill">личная работа</span> Проект закрыт, схема осталась на экране';
    };
  }

  // ================================================================== permissions in the UI
  const CONTENT_CMDS = new Set(['element.updateLabel', 'element.updateProperties', 'element.updateModdleProperties', 'element.setColor']);
  const acc = () => window.access;
  function cmdAllowed(cmd) {
    if (!acc().inProject()) return true;
    if (CONTENT_CMDS.has(cmd)) return ['edit_content', 'edit_code', 'generate', 'edit_bpmn'].some(p => acc().can(p));
    return acc().can('edit_bpmn');
  }
  let warned = 0;
  function denied(what) {
    if (Date.now() - warned < 1500) return;
    warned = Date.now();
    $('#summary').innerHTML = `<span class="pill err">нет прав</span> ${h(what)}`;
  }
  (function guardCommands() {
    const stack = modeler.get('commandStack');
    const raw = { execute: stack.execute.bind(stack), undo: stack.undo.bind(stack), redo: stack.redo.bind(stack) };
    stack.execute = (cmd, ctx) => {
      if (!cmdAllowed(cmd)) { denied('Ваши роли в проекте не позволяют менять структуру схемы'); return; }
      return raw.execute(cmd, ctx);
    };
    for (const name of ['undo', 'redo']) {
      stack[name] = () => {
        if (acc().inProject() && !['edit_content', 'edit_code', 'generate', 'edit_bpmn'].some(p => acc().can(p))) return;
        return raw[name]();
      };
    }
    modeler.get('eventBus').on('commandStack.changed', () => { if (acc().inProject() && !dirty) { dirty = true; renderBar(); } });
  })();

  const LOCKS = [
    ['generate', ['#generate', '#refine', '#instruction', '#mode']],
    ['edit_bpmn', ['#relayout', '#open-file', '#rebuild', '#palette-toggle']],
    ['run', ['#run-open']],
  ];
  function applyPermissions() {
    const a = acc();
    document.body.classList.toggle('lock-structure', a.inProject() && !a.can('edit_bpmn'));
    document.body.classList.toggle('lock-content', a.inProject() && !a.can('edit_content') && !a.can('generate'));
    if (!state.busy) {
      for (const [perm, sels] of LOCKS) for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) continue;
        if (a.inProject() && !a.can(perm)) { el.disabled = true; el.dataset.locked = perm; el.title = 'Нет права в этом проекте'; }
        else if (el.dataset.locked) { delete el.dataset.locked; el.removeAttribute('title'); el.disabled = false; }
      }
      const text = $('#text');
      text.readOnly = a.inProject() && !a.can('edit_content') && !a.can('generate');
    }
    document.getElementById('run-open')?.toggleAttribute('hidden', a.inProject() && !a.can('run'));
    renderBar();
  }
  window.applyPermissions = applyPermissions;
  // app.js re-enables controls in busy() / syncDiagramActions(): re-apply the locks after them
  for (const fn of ['busy', 'syncDiagramActions']) {
    const original = window[fn];
    if (typeof original === 'function') window[fn] = function (...args) { const r = original.apply(this, args); applyPermissions(); return r; };
  }

  // ================================================================== invitation link /join/<token>
  async function handleJoinLink() {
    const m = location.pathname.match(/^\/join\/([^/]+)\/?$/);
    if (!m || !window.session.me) return;
    const token = decodeURIComponent(m[1]);
    history.replaceState({}, '', '/');
    try {
      const info = await call('/api/links/' + encodeURIComponent(token));
      if (info.already_member) { await open(info.team_id); status('Вы уже в этой команде'); return; }
      const w = modal('Приглашение в команду', `<p><strong>${h(info.invited_by)}</strong> приглашает вас в команду <strong>«${h(info.team_name)}»</strong>.</p>
        <p>${info.roles.length ? 'Роли: ' + info.roles.map(h).join(', ') : 'Роли назначит глава команды.'}</p>`,
      async () => { const r = await call(`/api/links/${encodeURIComponent(token)}/accept`, 'POST'); await open(r.team_id); status('Вы вступили в команду'); return null; });
      w.querySelector('.be-save').textContent = 'Принять';
      w.querySelector('.be-cancel').textContent = 'Отклонить';
    } catch (e) { alert(e.message); }
  }

  window.teams = { open, openProject, closeProject, save: saveProject, current: () => window.session.project };
  window.session.ready.then((me) => {
    applyPermissions();
    if (me) handleJoinLink();
  });
})();
