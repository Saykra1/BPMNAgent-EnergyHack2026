/* Команды: имя без пароля, приглашение по ссылке, общее сохранение проекта. */
const TOKEN_KEY = 'bpmn_user_token';
const TEAM_KEY = 'bpmn_team';
const ROLES = { owner: 'Владелец', editor: 'Редактор', viewer: 'Наблюдатель' };

const collab = { me: null, team: null };

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: 'Bearer ' + token } : {};
}

async function collabFetch(path, options = {}) {
  const headers = { ...authHeaders(), ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : 'Запрос не выполнен');
    error.status = response.status;
    throw error;
  }
  return data;
}

function askName(title, hint) {
  return new Promise((resolve) => {
    const modal = $('#name-modal');
    const form = $('#name-form');
    const input = $('#name-input');
    $('#name-title').textContent = title;
    $('#name-hint').textContent = hint;
    input.value = '';
    modal.hidden = false;
    input.focus();
    const finish = (value) => {
      form.removeEventListener('submit', onSubmit);
      $('#name-cancel').removeEventListener('click', onCancel);
      modal.hidden = true;
      resolve(value);
    };
    const onSubmit = (event) => {
      event.preventDefault();
      const name = input.value.trim();
      if (!name) return;
      finish(name);
    };
    const onCancel = () => finish(null);
    form.addEventListener('submit', onSubmit);
    $('#name-cancel').addEventListener('click', onCancel);
  });
}

function setOpen(on) {
  $('#team-panel').hidden = !on;
  $('#team-backdrop').hidden = !on;
}

function showStatus(message) {
  const box = $('#team-status');
  if (box) box.textContent = message || '';
}

async function ensureUser() {
  if (localStorage.getItem(TOKEN_KEY)) {
    try { return await collabFetch('/api/me'); }
    catch (error) {
      if (error.status !== 401) throw error;
      localStorage.removeItem(TOKEN_KEY);
    }
  }
  const name = await askName('Как вас зовут?', 'Имя видно участникам команды. Пароль не нужен.');
  if (!name) return null;
  const user = await collabFetch('/api/session', { method: 'POST', body: JSON.stringify({ name }) });
  localStorage.setItem(TOKEN_KEY, user.token);
  return collabFetch('/api/me');
}

function roleOptions(current) {
  return Object.entries(ROLES).map(([value, label]) => (
    `<option value="${value}" ${value === current ? 'selected' : ''}>${label}</option>`
  )).join('');
}

function render(me, team) {
  const teams = me.teams.length
    ? `<div class="team-switch">${me.teams.map((item) => (
      `<button type="button" data-team="${esc(item.id)}" class="${team && team.id === item.id ? 'active' : ''}">${esc(item.name)} · ${ROLES[item.role]}</button>`
    )).join('')}</div>`
    : '<p class="muted">Пока нет команд. Создайте первую — вы станете владельцем.</p>';
  const detail = team ? renderDetail(team) : '';
  $('#team-body').innerHTML = `
    <p id="team-status" class="hint" role="status"></p>
    <label for="new-team-name">Новая команда</label>
    <div class="row">
      <input id="new-team-name" maxlength="80" placeholder="Например, Диспетчерская смена">
      <button id="team-create" type="button" class="primary">Создать</button>
    </div>
    ${teams}
    ${detail}`;
  $('#who').textContent = me.name;
  bind(team);
}

function renderDetail(team) {
  const owner = team.role === 'owner';
  const canEdit = team.role === 'owner' || team.role === 'editor';
  const members = team.members.map((member) => `
    <li>
      <b>${esc(member.name)}</b>
      ${owner
        ? `<select data-role="${esc(member.user_id)}" aria-label="Роль ${esc(member.name)}">${roleOptions(member.role)}</select>
           <button type="button" data-kick="${esc(member.user_id)}">Исключить</button>`
        : `<span class="pill">${ROLES[member.role]}</span>`}
    </li>`).join('');
  const invites = owner ? `
    <h4>Приглашение</h4>
    <p class="hint">Ссылка действует, пока её не отзовут. Роль задаётся заранее. По умолчанию приглашённый может редактировать.</p>
    <div class="row">
      <select id="invite-role">${roleOptions('editor')}</select>
      <button id="invite-create" type="button">Создать ссылку</button>
    </div>
    <ul class="team-list">${team.invites.map((invite) => {
      const url = location.origin + '/join/' + invite.token;
      return `<li>
        <span class="pill">${ROLES[invite.role]}</span>
        <input readonly value="${esc(url)}" aria-label="Ссылка приглашения">
        <button type="button" data-copy="${esc(url)}">Копировать</button>
        <button type="button" data-revoke="${esc(invite.token)}">Отозвать</button>
      </li>`;
    }).join('') || '<li class="muted">Ссылок пока нет</li>'}</ul>` : '';
  const projects = team.projects.length ? team.projects.map((project) => `
    <li>
      <button type="button" data-open="${esc(project.id)}">${esc(project.title)}</button>
      <span class="muted">версия ${project.version}${project.updated_by_name ? ' · ' + esc(project.updated_by_name) : ''}</span>
      ${owner ? `<button type="button" data-delete="${esc(project.id)}">Удалить</button>` : ''}
    </li>`).join('') : '<li class="muted">Проектов пока нет</li>';
  const createProject = canEdit ? `
    <div class="row">
      <input id="new-project-name" maxlength="200" placeholder="Название схемы">
      <button id="project-create" type="button">Новый проект</button>
    </div>` : '<p class="hint">Наблюдатель открывает схемы и скачивает их. Сохранение и правки недоступны.</p>';
  return `
    <h4>${esc(team.name)} · ${ROLES[team.role]}</h4>
    <h4>Участники</h4>
    <ul class="team-list">${members}</ul>
    ${invites}
    <h4>Проекты</h4>
    ${createProject}
    <ul class="team-list">${projects}</ul>`;
}

function bind(team) {
  $('#team-create').onclick = async () => {
    const name = $('#new-team-name').value.trim();
    if (!name) return;
    try {
      const created = await collabFetch('/api/teams', { method: 'POST', body: JSON.stringify({ name }) });
      await refresh(created.id);
    } catch (error) { showStatus(error.message); }
  };
  document.querySelectorAll('[data-team]').forEach((button) => {
    button.onclick = () => refresh(button.dataset.team).catch((error) => showStatus(error.message));
  });
  if (!team) return;
  const onClick = (selector, fn) => document.querySelectorAll(selector).forEach((node) => { node.onclick = fn; });
  document.querySelectorAll('[data-role]').forEach((select) => {
    select.onchange = async () => {
      try {
        await collabFetch(`/api/teams/${team.id}/members/${select.dataset.role}`, {
          method: 'PATCH', body: JSON.stringify({ role: select.value }),
        });
        await refresh(team.id);
        syncAccess();
      } catch (error) {
        showStatus(error.message);
        await refresh(team.id);
      }
    };
  });
  onClick('[data-kick]', async (event) => {
    const userId = event.currentTarget.dataset.kick;
    const member = team.members.find((item) => item.user_id === userId);
    if (!confirm(`Исключить ${member ? member.name : 'участника'} из команды?`)) return;
    try {
      const result = await collabFetch(`/api/teams/${team.id}/members/${userId}`, { method: 'DELETE' });
      if (result.left) {
        sessionStorage.removeItem(TEAM_KEY);
        collab.team = null;
        $('#team-label').hidden = true;
        await refresh(null);
        return;
      }
      await refresh(team.id);
    } catch (error) { showStatus(error.message); }
  });
  if ($('#invite-create')) $('#invite-create').onclick = async () => {
    try {
      await collabFetch(`/api/teams/${team.id}/invites`, {
        method: 'POST', body: JSON.stringify({ role: $('#invite-role').value }),
      });
      await refresh(team.id);
      showStatus('Ссылка создана. Её можно отправить коллеге.');
    } catch (error) { showStatus(error.message); }
  };
  onClick('[data-copy]', async (event) => {
    const url = event.currentTarget.dataset.copy;
    try {
      await navigator.clipboard.writeText(url);
      showStatus('Ссылка скопирована.');
    } catch {
      event.currentTarget.previousElementSibling?.select?.();
      showStatus('Скопируйте ссылку из поля.');
    }
  });
  onClick('[data-revoke]', async (event) => {
    try {
      await collabFetch('/api/invites/' + event.currentTarget.dataset.revoke, { method: 'DELETE' });
      await refresh(team.id);
    } catch (error) { showStatus(error.message); }
  });
  if ($('#project-create')) $('#project-create').onclick = async () => {
    const title = $('#new-project-name').value.trim();
    if (!title) return;
    try {
      const project = await collabFetch(`/api/teams/${team.id}/projects`, {
        method: 'POST', body: JSON.stringify({ title }),
      });
      await window.diagramIO.load(project);
      await refresh(team.id);
      setOpen(false);
    } catch (error) { showStatus(error.message); }
  };
  onClick('[data-open]', async (event) => {
    try {
      const project = await collabFetch('/api/projects/' + event.currentTarget.dataset.open);
      await window.diagramIO.load(project);
      setOpen(false);
    } catch (error) { showStatus(error.message); }
  });
  onClick('[data-delete]', async (event) => {
    const projectId = event.currentTarget.dataset.delete;
    if (!confirm('Удалить проект для всей команды?')) return;
    try {
      await collabFetch('/api/projects/' + projectId, { method: 'DELETE' });
      if (window.diagramIO.current()?.id === projectId) window.diagramIO.clearProject();
      await refresh(team.id);
    } catch (error) { showStatus(error.message); }
  });
}

function syncAccess() {
  const project = window.diagramIO.current();
  if (project && collab.team && project.team_id === collab.team.id) window.diagramIO.setAccess(collab.team.role);
}

async function refresh(teamId) {
  const me = await collabFetch('/api/me');
  collab.me = me;
  let team = null;
  if (teamId && me.teams.some((item) => item.id === teamId)) {
    team = await collabFetch('/api/teams/' + teamId);
    sessionStorage.setItem(TEAM_KEY, teamId);
    $('#team-label').hidden = false;
    $('#team-label').textContent = team.name;
  } else if (!teamId) {
    $('#team-label').hidden = true;
  }
  collab.team = team;
  render(me, team);
  syncAccess();
}

async function openPanel() {
  try {
    const me = await ensureUser();
    if (!me) return;
    collab.me = me;
    const stored = sessionStorage.getItem(TEAM_KEY);
    const teamId = me.teams.some((item) => item.id === stored) ? stored : null;
    await refresh(teamId);
    setOpen(true);
  } catch (error) { alert(error.message); }
}

async function saveProject() {
  const current = window.diagramIO.current();
  if (!current || state.access === 'viewer') return;
  try {
    const snap = await window.diagramIO.snapshot();
    const saved = await collabFetch('/api/projects/' + current.id, {
      method: 'PUT',
      body: JSON.stringify({ ...snap, title: current.title, version: current.version }),
    });
    window.diagramIO.noteSaved(saved);
  } catch (error) {
    alert(error.message);
  }
}

async function reloadProject() {
  const current = window.diagramIO.current();
  if (!current) return;
  if (!confirm('Загрузить сохранённую версию? Несохранённые правки на этом экране будут заменены.')) return;
  try {
    const project = await collabFetch('/api/projects/' + current.id);
    await window.diagramIO.load(project);
  } catch (error) { alert(error.message); }
}

async function acceptInvite(token) {
  try {
    let headers = authHeaders();
    const body = {};
    if (!localStorage.getItem(TOKEN_KEY)) {
      const name = await askName('Вас пригласили в команду', 'Напишите имя, под которым вас увидят коллеги.');
      if (!name) return;
      body.name = name;
      headers = {};
    }
    const joined = await collabFetch('/api/join/' + encodeURIComponent(token), {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    localStorage.setItem(TOKEN_KEY, joined.user.token);
    history.replaceState({}, '', '/');
    await refresh(joined.team_id);
    setOpen(true);
  } catch (error) {
    history.replaceState({}, '', '/');
    alert(error.message);
  }
}

$('#team-open').onclick = () => { if ($('#team-panel').hidden) openPanel(); else setOpen(false); };
$('#team-close').onclick = () => setOpen(false);
$('#team-backdrop').onclick = () => setOpen(false);
$('#project-save').onclick = saveProject;
$('#project-reload').onclick = reloadProject;

const invitePath = location.pathname.match(/^\/join\/([^/]+)\/?$/);
if (invitePath) acceptInvite(decodeURIComponent(invitePath[1]));
else if (localStorage.getItem(TOKEN_KEY)) {
  collabFetch('/api/me').then((me) => { $('#who').textContent = me.name; }).catch(() => localStorage.removeItem(TOKEN_KEY));
}
