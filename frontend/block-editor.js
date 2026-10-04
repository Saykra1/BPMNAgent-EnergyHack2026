/* Block properties: a gear appears on the selected block; it opens a dialog to edit everything about
   the block (name, type, role/lane, performer, description, deadlines, SLA, durations, documents,
   basis, RACI, branch conditions). Plus a directory of performers and roles stored inside the .bpmn.
   All changes go through the bpmn-js command stack: undo (Ctrl+Z), history and checks keep working. */
(() => {
  const DETAILS = 'BPMN_AGENT_DETAILS:';
  const PEOPLE = 'BPMN_AGENT_PERFORMERS:';
  const TASK_TYPES = [['bpmn:Task', 'Задача'], ['bpmn:UserTask', 'Пользовательская (в системе)'],
    ['bpmn:ServiceTask', 'Сервисная (автоматически)'], ['bpmn:ManualTask', 'Ручная работа'],
    ['bpmn:SendTask', 'Отправка'], ['bpmn:ReceiveTask', 'Получение'], ['bpmn:ScriptTask', 'Скрипт'],
    ['bpmn:BusinessRuleTask', 'Бизнес-правило']];
  const GATEWAY_TYPES = [['bpmn:ExclusiveGateway', 'Исключающий (одна ветка)'], ['bpmn:ParallelGateway', 'Параллельный (все ветки)'],
    ['bpmn:InclusiveGateway', 'Инклюзивный (одна или несколько)'], ['bpmn:EventBasedGateway', 'По событию']];
  let gearId = null;

  const svc = (n) => modeler.get(n);
  const is = (el, t) => el?.businessObject?.$instanceOf?.(t);
  const uid = (p) => `${p}_${Math.random().toString(36).slice(2, 8)}`;

  // ------------------------------------------------------------------ data helpers
  function docs(bo) { return bo.documentation || []; }
  function readDetails(bo) {
    for (const d of docs(bo)) if ((d.text || '').startsWith(DETAILS)) { try { return JSON.parse(d.text.slice(DETAILS.length)); } catch { return {}; } }
    return {};
  }
  function readDescription(bo) {
    return docs(bo).filter(d => d.text && !d.text.startsWith('BPMN_AGENT_')).map(d => d.text).join('\n\n');
  }
  function buildDocs(bo, details, description) {
    const moddle = svc('moddle');
    const keep = docs(bo).filter(d => d.text && d.text.startsWith('BPMN_AGENT_') && !d.text.startsWith(DETAILS));
    const out = [];
    if (description && description.trim()) out.push(moddle.create('bpmn:Documentation', { text: description.trim() }));
    out.push(...keep);
    const clean = Object.fromEntries(Object.entries(details).filter(([, v]) => v !== '' && v != null && !(Array.isArray(v) && !v.length)));
    if (Object.keys(clean).length) out.push(moddle.create('bpmn:Documentation', { textFormat: 'application/json', text: DETAILS + JSON.stringify(clean) }));
    return out;
  }
  function lanes() { return svc('elementRegistry').filter(e => e.type === 'bpmn:Lane'); }
  function pools() { return svc('elementRegistry').filter(e => e.type === 'bpmn:Participant'); }
  function roles() {   // a role = a lane; pools without lanes also act as roles
    const ls = lanes();
    const bare = pools().filter(p => !ls.some(l => poolOf(l) === p));
    return [...ls, ...bare].map(e => ({ id: e.id, name: e.businessObject.name || e.id, shape: e }));
  }
  function poolOf(el) { let p = el; while (p && p.type !== 'bpmn:Participant') p = p.parent; return p; }
  function laneOf(el) {
    const bo = el.businessObject;
    const lane = lanes().find(l => (l.businessObject.flowNodeRef || []).includes(bo));
    return lane || poolOf(el) || null;
  }
  function directoryHost() {
    const root = svc('canvas').getRootElement();
    if (is(root, 'bpmn:Process')) return { shape: root, process: root.businessObject };
    const pool = pools().find(p => p.businessObject.processRef && lanes().some(l => poolOf(l) === p)) ||
      pools().find(p => p.businessObject.processRef);
    return pool ? { shape: pool, process: pool.businessObject.processRef } : null;
  }
  function readPeople() {
    const host = directoryHost();
    if (!host) return [];
    for (const d of docs(host.process)) if ((d.text || '').startsWith(PEOPLE)) { try { return JSON.parse(d.text.slice(PEOPLE.length)); } catch { return []; } }
    return [];
  }
  function writePeople(people) {
    const host = directoryHost();
    if (!host) throw new Error('Нет процесса, к которому можно привязать справочник');
    const moddle = svc('moddle');
    const other = docs(host.process).filter(d => !(d.text || '').startsWith(PEOPLE));
    const documentation = people.length
      ? [...other, moddle.create('bpmn:Documentation', { textFormat: 'application/json', text: PEOPLE + JSON.stringify(people) })] : other;
    if (host.shape.businessObject === host.process) svc('modeling').updateProperties(host.shape, { documentation });
    else svc('modeling').updateModdleProperties(host.shape, host.process, { documentation });
  }
  function personLabel(p) { return p.name + (p.position ? `, ${p.position}` : ''); }

  // ------------------------------------------------------------------ roles (lanes)
  function addRole(name, nearElement) {
    const modeling = svc('modeling');
    const pool = (nearElement && poolOf(nearElement)) || pools()[0];
    if (!pool) throw new Error('В схеме нет пула — роль добавить некуда. Постройте схему с участниками.');
    const own = lanes().filter(l => poolOf(l) === pool && l.parent === pool);
    let lane;
    if (own.length) lane = modeling.addLane(own.reduce((a, b) => (a.y + a.height > b.y + b.height ? a : b)), 'bottom');
    else {
      modeling.addLane(pool, 'bottom');                    // first split: existing content gets its own lane
      lane = lanes().filter(l => poolOf(l) === pool).sort((a, b) => b.y - a.y)[0];
    }
    modeling.updateLabel(lane, name);
    return lane;
  }
  function moveToRole(element, roleShape) {
    if (!roleShape || laneOf(element) === roleShape) return;
    if (poolOf(roleShape) !== poolOf(element)) throw new Error('Роль из другого пула: перенос между организациями делается связью-сообщением.');
    const dy = (roleShape.y + roleShape.height / 2) - (element.y + element.height / 2);
    const dx = Math.min(0, roleShape.x + 40 - element.x) || 0;
    svc('modeling').moveElements([element], { x: dx, y: dy }, roleShape.type === 'bpmn:Lane' ? roleShape : undefined);
  }

  // ------------------------------------------------------------------ gear overlay
  function editable(el) {
    return el && !el.labelTarget && (is(el, 'bpmn:FlowNode') || is(el, 'bpmn:SequenceFlow') || el.type === 'bpmn:Lane' || el.type === 'bpmn:Participant');
  }
  function clearGear() {
    if (gearId) { try { svc('overlays').remove(gearId); } catch { /* already gone */ } gearId = null; }
  }
  function showGear(el) {
    clearGear();
    if (!editable(el)) return;
    const html = document.createElement('button');
    html.className = 'be-gear'; html.type = 'button'; html.title = 'Свойства блока'; html.setAttribute('aria-label', 'Свойства блока');
    html.textContent = '⚙';
    html.addEventListener('mousedown', e => e.stopPropagation());
    html.addEventListener('click', e => { e.stopPropagation(); openEditor(el); });
    const isFlow = is(el, 'bpmn:SequenceFlow');
    const pos = isFlow ? { top: -18, left: -12 } : el.type === 'bpmn:Lane' || el.type === 'bpmn:Participant'
      ? { top: 4, left: 34 } : { top: -14, left: -14 };
    if (isFlow) {
      const wp = el.waypoints[Math.floor(el.waypoints.length / 2)];
      const pts = el.waypoints, minX = Math.min(...pts.map(p => p.x)), minY = Math.min(...pts.map(p => p.y));
      pos.top = wp.y - minY - 14; pos.left = wp.x - minX - 14;
    }
    gearId = svc('overlays').add(el, 'block-editor', { position: pos, html, show: { minZoom: 0.3 }, scale: false });
  }

  // ------------------------------------------------------------------ dialog shell
  function dialog(title, bodyHtml, onSave, extraButtons = '') {
    closeDialog();
    const wrap = document.createElement('div');
    wrap.id = 'be-modal';
    wrap.innerHTML = `<div class="be-backdrop"></div><div class="be-dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="be-head"><h3>${esc(title)}</h3><button class="icon-button be-x" aria-label="Закрыть">×</button></div>
      <form class="be-body" novalidate>${bodyHtml}</form>
      <p class="be-error" role="alert"></p>
      <div class="be-foot">${extraButtons}<span class="be-spacer"></span><button type="button" class="be-cancel">Отмена</button><button type="button" class="primary be-save">Сохранить</button></div></div>`;
    document.body.append(wrap);
    const close = () => closeDialog();
    wrap.querySelector('.be-x').onclick = close; wrap.querySelector('.be-cancel').onclick = close;
    wrap.querySelector('.be-backdrop').onclick = close;
    wrap.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    wrap.querySelector('.be-save').onclick = () => {
      try { onSave(wrap); closeDialog(); }
      catch (err) { wrap.querySelector('.be-error').textContent = err.message; }
    };
    setTimeout(() => wrap.querySelector('input, select, textarea')?.focus(), 30);
    return wrap;
  }
  function closeDialog() { document.getElementById('be-modal')?.remove(); }
  const val = (w, sel) => w.querySelector(sel)?.value?.trim() ?? '';
  const num = (w, sel, what) => {
    const v = val(w, sel);
    if (v === '') return null;
    const n = Number(v.replace(',', '.'));
    if (!isFinite(n) || n < 0) throw new Error(`${what}: укажите неотрицательное число`);
    return n;
  };
  const options = (list, current, empty) => (empty ? `<option value="">${esc(empty)}</option>` : '') +
    list.map(([v, t]) => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(t)}</option>`).join('');

  // ------------------------------------------------------------------ block editor
  function openEditor(el) {
    if (is(el, 'bpmn:SequenceFlow')) return openFlowEditor(el);
    if (el.type === 'bpmn:Lane' || el.type === 'bpmn:Participant') return openRoleEditor(el);
    const bo = el.businessObject;
    const d = readDetails(bo);
    const people = readPeople();
    const rs = roles();
    const curRole = laneOf(el);
    const isTask = is(el, 'bpmn:Activity');
    const isGw = is(el, 'bpmn:Gateway');
    const typeList = isTask && !is(el, 'bpmn:SubProcess') ? TASK_TYPES : isGw ? GATEWAY_TYPES : null;
    const roleOpts = rs.map(r => [r.id, r.name]);
    const branches = isGw ? (el.outgoing || []).filter(c => is(c, 'bpmn:SequenceFlow')) : [];
    const roleName = (id) => rs.find(r => r.id === id)?.name || id;
    const checks = (key) => rs.map(r => `<label class="be-chip"><input type="checkbox" data-${key}="${esc(r.id)}" ${(d[key] || []).includes(r.id) ? 'checked' : ''}> ${esc(r.name)}</label>`).join('');
    const html = `
      <fieldset><legend>Основное</legend>
        <label>Название<input id="be-name" value="${esc(bo.name || '')}" maxlength="200"></label>
        ${typeList ? `<label>Тип<select id="be-type">${options(typeList, bo.$type)}</select></label>` : ''}
        <label>Описание<textarea id="be-desc" rows="3" maxlength="4000" placeholder="Что именно делается на этом шаге, правила, особенности">${esc(readDescription(bo))}</textarea></label>
      </fieldset>
      <fieldset><legend>Роль и исполнитель</legend>
        <div class="be-row"><label>Роль (дорожка)<select id="be-role">${options(roleOpts, curRole?.id, rs.length ? null : 'Нет ролей')}<option value="__new">+ Новая роль…</option></select></label>
        <label id="be-newrole-box" hidden>Название новой роли<input id="be-newrole" maxlength="120" placeholder="Например, Юрист"></label></div>
        <div class="be-row"><label>Исполнитель<select id="be-person">${options(people.map(p => [p.id, personLabel(p) + (p.role ? ` — ${roleName(p.role)}` : '')]), d.performer, '— не назначен —')}<option value="__new">+ Новый исполнитель…</option></select></label>
        <button type="button" id="be-dir" class="quiet">Справочник исполнителей</button></div>
        <div id="be-newperson" class="be-row" hidden><label>ФИО<input id="be-p-name" maxlength="150"></label><label>Должность<input id="be-p-pos" maxlength="150"></label><label>Контакты<input id="be-p-cont" maxlength="200"></label></div>
      </fieldset>
      <fieldset><legend>Сроки и трудоёмкость</legend>
        <label>Срок и точка отсчёта<input id="be-deadline" value="${esc(d.deadline || '')}" maxlength="500" placeholder="20 рабочих дней с получения уведомления"></label>
        <div class="be-row"><label>Норматив (SLA), ч<input id="be-sla" inputmode="decimal" value="${d.sla_hours ?? ''}"></label>
        <label>Работа, мин<input id="be-dur" inputmode="decimal" value="${d.duration_min ?? ''}"></label>
        <label>Ожидание, мин<input id="be-wait" inputmode="decimal" value="${d.wait_min ?? ''}"></label></div>
        <label class="be-check"><input type="checkbox" id="be-est" ${d.estimate ? 'checked' : ''}> Значения — оценка, а не факт</label>
      </fieldset>
      <fieldset><legend>Документы и основание</legend>
        <label>Документы — по одному на строку<textarea id="be-docs" rows="3" maxlength="4000">${esc((d.documents || []).join('\n'))}</textarea></label>
        <label>Цитата из исходного описания<textarea id="be-quote" rows="2" maxlength="3000">${esc(d.source_quote || '')}</textarea></label>
        <label>Допущение / требует уточнения<textarea id="be-assumption" rows="2" maxlength="1500">${esc(d.assumption || '')}</textarea></label>
      </fieldset>
      ${isTask ? `<fieldset><legend>RACI</legend>
        <label>Отвечает за результат (A)<select id="be-acc">${options(roleOpts, d.accountable, '— как исполнитель —')}</select></label>
        <div class="be-label">Консультирует (C)</div><div class="be-chips">${checks('consulted')}</div>
        <div class="be-label">Информируется (I)</div><div class="be-chips">${checks('informed')}</div>
      </fieldset>` : ''}
      ${branches.length ? `<fieldset><legend>Ветки развилки</legend><table class="be-table"><tr><th>Куда</th><th>Условие</th><th title="Ветка «иначе»">Иначе</th><th>Вероятн.</th></tr>
        ${branches.map(c => `<tr><td>${esc(c.target.businessObject.name || c.target.id)}</td>
        <td><input data-cond="${esc(c.id)}" value="${esc(c.businessObject.name || '')}" maxlength="150"></td>
        <td><input type="radio" name="be-default" value="${esc(c.id)}" ${bo.default === c.businessObject ? 'checked' : ''} ${is(el, 'bpmn:ParallelGateway') ? 'disabled' : ''}></td>
        <td><input data-prob="${esc(c.id)}" inputmode="decimal" value="${readDetails(c.businessObject).probability ?? ''}" placeholder="0–1"></td></tr>`).join('')}
        </table><label class="be-check"><input type="radio" name="be-default" value="" ${!bo.default ? 'checked' : ''}> Без ветки «иначе»</label></fieldset>` : ''}
      <p class="hint">ID: ${esc(el.id)} · изменения можно отменить Ctrl+Z, предыдущие версии — во вкладке «История».</p>`;
    const w = dialog(`Свойства: ${bo.name || el.id}`, html, (w) => save(el, w, d), `<button type="button" class="be-delete">Удалить блок</button>`);
    w.querySelector('#be-role').onchange = (e) => { w.querySelector('#be-newrole-box').hidden = e.target.value !== '__new'; };
    w.querySelector('#be-person').onchange = (e) => {
      w.querySelector('#be-newperson').hidden = e.target.value !== '__new';
      const p = people.find(x => x.id === e.target.value);
      if (p && p.role && rs.some(r => r.id === p.role) && w.querySelector('#be-role').value !== p.role) {
        w.querySelector('#be-role').value = p.role;            // performer's role suggests the lane
      }
    };
    w.querySelector('#be-dir').onclick = () => { closeDialog(); openDirectory(() => openEditor(svc('elementRegistry').get(el.id))); };
    w.querySelector('.be-delete').onclick = () => {
      if (!confirm(`Удалить «${bo.name || el.id}» со всеми его связями?`)) return;
      svc('modeling').removeElements([el]); closeDialog(); clearGear();
    };
  }

  function save(el, w, old) {
    const modeling = svc('modeling');
    const name = val(w, '#be-name');
    const roleSel = val(w, '#be-role');
    const newRole = val(w, '#be-newrole');
    if (roleSel === '__new' && !newRole) throw new Error('Введите название новой роли');
    let personSel = val(w, '#be-person');
    const details = {
      ...old,
      deadline: val(w, '#be-deadline'), sla_hours: num(w, '#be-sla', 'Норматив'),
      duration_min: num(w, '#be-dur', 'Работа'), wait_min: num(w, '#be-wait', 'Ожидание'),
      estimate: w.querySelector('#be-est').checked || undefined,
      documents: val(w, '#be-docs').split('\n').map(s => s.trim()).filter(Boolean),
      source_quote: val(w, '#be-quote'), assumption: val(w, '#be-assumption'),
    };
    if (details.source_quote && state.text && !state.text.includes(details.source_quote) && details.source_quote !== old.source_quote) {
      throw new Error('Цитата должна дословно встречаться в исходном описании (или оставьте поле пустым).');
    }
    if (w.querySelector('#be-acc')) {
      details.accountable = val(w, '#be-acc') || undefined;
      details.consulted = [...w.querySelectorAll('[data-consulted]:checked')].map(i => i.dataset.consulted);
      details.informed = [...w.querySelectorAll('[data-informed]:checked')].map(i => i.dataset.informed);
    }
    const probs = {};
    w.querySelectorAll('[data-prob]').forEach(i => {
      const v = i.value.trim();
      if (v === '') return;
      const n = Number(v.replace(',', '.'));
      if (!(n >= 0 && n <= 1)) throw new Error('Вероятность ветки — число от 0 до 1');
      probs[i.dataset.prob] = n;
    });
    // performer (may create a new one)
    if (personSel === '__new') {
      const pname = val(w, '#be-p-name');
      if (!pname) throw new Error('Укажите ФИО нового исполнителя');
      personSel = uid('person');
    }
    // ---- apply (each step is an undoable command)
    let element = el;
    const type = val(w, '#be-type');
    if (type && type !== element.businessObject.$type) element = svc('bpmnReplace').replaceElement(element, { type });
    if (name !== (element.businessObject.name || '')) modeling.updateLabel(element, name);
    let roleShape = null;
    if (roleSel === '__new') roleShape = addRole(newRole, element);
    else if (roleSel) roleShape = svc('elementRegistry').get(roleSel);
    if (roleShape) moveToRole(element, roleShape);
    if (val(w, '#be-person') === '__new') {
      const people = readPeople();
      people.push({ id: personSel, name: val(w, '#be-p-name'), role: (roleShape || laneOf(element))?.id || null,
        position: val(w, '#be-p-pos'), contacts: val(w, '#be-p-cont') });
      writePeople(people);
    }
    details.performer = personSel || undefined;
    modeling.updateProperties(element, { documentation: buildDocs(element.businessObject, details, val(w, '#be-desc')) });
    // gateway branches
    const moddle = svc('moddle');
    const defaultId = w.querySelector('input[name="be-default"]:checked')?.value || '';
    w.querySelectorAll('[data-cond]').forEach(i => {
      const flow = svc('elementRegistry').get(i.dataset.cond);
      if (!flow) return;
      const label = i.value.trim();
      if (label !== (flow.businessObject.name || '')) modeling.updateLabel(flow, label);
      const conditional = is(element, 'bpmn:ExclusiveGateway') || is(element, 'bpmn:InclusiveGateway');
      const cond = conditional && label && flow.id !== defaultId
        ? moddle.create('bpmn:FormalExpression', { body: label }) : undefined;
      const fd = { ...readDetails(flow.businessObject), probability: probs[flow.id] };
      modeling.updateProperties(flow, { conditionExpression: cond, documentation: buildDocs(flow.businessObject, fd, readDescription(flow.businessObject)) });
    });
    if (w.querySelector('input[name="be-default"]') && !is(element, 'bpmn:ParallelGateway')) {
      const flow = defaultId ? svc('elementRegistry').get(defaultId) : null;
      if ((element.businessObject.default || null) !== (flow?.businessObject || null)) modeling.updateProperties(element, { default: flow ? flow.businessObject : undefined });
    }
    svc('selection').select(element);
    $('#summary').innerHTML = `<span class="pill ok">сохранено</span> Свойства «${esc(name || element.id)}» обновлены`;
  }

  // ------------------------------------------------------------------ sequence flow editor
  function openFlowEditor(flow) {
    const bo = flow.businessObject;
    const src = flow.source;
    const conditional = is(src, 'bpmn:ExclusiveGateway') || is(src, 'bpmn:InclusiveGateway');
    const d = readDetails(bo);
    const html = `<fieldset><legend>Связь «${esc(src.businessObject.name || src.id)}» → «${esc(flow.target.businessObject.name || flow.target.id)}»</legend>
      <label>Подпись / условие<input id="be-flabel" value="${esc(bo.name || '')}" maxlength="150"></label>
      ${conditional ? `<label class="be-check"><input type="checkbox" id="be-fdef" ${src.businessObject.default === bo ? 'checked' : ''}> Ветка по умолчанию («иначе»)</label>` : ''}
      <label>Вероятность (0–1, для аналитики)<input id="be-fprob" inputmode="decimal" value="${d.probability ?? ''}"></label>
      <label>Описание<textarea id="be-fdesc" rows="2">${esc(readDescription(bo))}</textarea></label></fieldset>`;
    dialog('Свойства связи', html, (w) => {
      const modeling = svc('modeling');
      const label = val(w, '#be-flabel');
      const pv = val(w, '#be-fprob');
      const p = pv === '' ? undefined : Number(pv.replace(',', '.'));
      if (p !== undefined && !(p >= 0 && p <= 1)) throw new Error('Вероятность — число от 0 до 1');
      if (label !== (bo.name || '')) modeling.updateLabel(flow, label);
      const isDefault = conditional && w.querySelector('#be-fdef').checked;
      const cond = conditional && label && !isDefault ? svc('moddle').create('bpmn:FormalExpression', { body: label }) : undefined;
      modeling.updateProperties(flow, { conditionExpression: cond, documentation: buildDocs(bo, { ...d, probability: p }, val(w, '#be-fdesc')) });
      if (conditional) {
        const cur = src.businessObject.default === bo;
        if (isDefault !== cur) modeling.updateProperties(src, { default: isDefault ? bo : undefined });
      }
    }, `<button type="button" class="be-delete">Удалить связь</button>`).querySelector('.be-delete').onclick = () => {
      svc('modeling').removeElements([flow]); closeDialog(); clearGear();
    };
  }

  // ------------------------------------------------------------------ role editor (lane / pool)
  function openRoleEditor(shape) {
    const bo = shape.businessObject;
    const people = readPeople().filter(p => p.role === shape.id);
    const inLane = shape.type === 'bpmn:Lane' ? (bo.flowNodeRef || []).length : null;
    const html = `<fieldset><legend>${shape.type === 'bpmn:Lane' ? 'Роль (дорожка)' : 'Участник (пул)'}</legend>
      <label>Название<input id="be-rname" value="${esc(bo.name || '')}" maxlength="150"></label>
      <label>Описание роли<textarea id="be-rdesc" rows="2">${esc(readDescription(bo))}</textarea></label>
      <p>Исполнители с этой ролью: ${people.length ? people.map(p => esc(personLabel(p))).join(', ') : '<span class="muted">нет</span>'}</p>
      ${inLane !== null ? `<p class="hint">Шагов в роли: ${inLane}. Удалить можно только пустую роль.</p>` : ''}</fieldset>`;
    const w = dialog('Свойства роли', html, (w) => {
      const name = val(w, '#be-rname');
      if (!name) throw new Error('Название не может быть пустым');
      if (name !== (bo.name || '')) svc('modeling').updateLabel(shape, name);
      svc('modeling').updateProperties(shape, { documentation: buildDocs(bo, readDetails(bo), val(w, '#be-rdesc')) });
    }, `${shape.type === 'bpmn:Lane' ? '<button type="button" class="be-delete">Удалить роль</button>' : ''}<button type="button" class="be-dir quiet">Справочник исполнителей</button>`);
    const del = w.querySelector('.be-delete');
    if (del) del.onclick = () => {
      if ((bo.flowNodeRef || []).length) { w.querySelector('.be-error').textContent = 'В роли есть шаги — сначала перенесите их в другую роль.'; return; }
      svc('modeling').removeShape(shape); closeDialog(); clearGear();
    };
    w.querySelector('.be-dir').onclick = () => { closeDialog(); openDirectory(); };
  }

  // ------------------------------------------------------------------ directory of performers and roles
  function openDirectory(after) {
    if (!state.hasDiagram) { alert('Сначала постройте или откройте схему'); return; }
    const people = readPeople();
    const rs = roles();
    const roleOpts = rs.map(r => [r.id, r.name]);
    const row = (p) => `<tr data-id="${esc(p.id)}"><td><input class="d-name" value="${esc(p.name)}" maxlength="150" placeholder="ФИО"></td>
      <td><input class="d-pos" value="${esc(p.position || '')}" maxlength="150" placeholder="Должность"></td>
      <td><select class="d-role">${options(roleOpts, p.role, '— без роли —')}</select></td>
      <td><input class="d-cont" value="${esc(p.contacts || '')}" maxlength="200" placeholder="Телефон, e-mail"></td>
      <td><button type="button" class="icon-button d-del" aria-label="Удалить">×</button></td></tr>`;
    const used = (id) => svc('elementRegistry').filter(e => readDetails(e.businessObject || {}).performer === id).length;
    const html = `<fieldset><legend>Роли (дорожки)</legend>
      <div id="d-roles">${rs.map(r => `<div class="be-row"><input class="r-name" data-role="${esc(r.id)}" value="${esc(r.name)}" maxlength="150"></div>`).join('') || '<p class="muted">В схеме нет ролей.</p>'}</div>
      <div class="be-row"><input id="d-newrole" placeholder="Новая роль, например «Юрист»" maxlength="150"><button type="button" id="d-addrole">Добавить роль</button></div></fieldset>
      <fieldset><legend>Исполнители</legend><table class="be-table" id="d-people"><tr><th>ФИО</th><th>Должность</th><th>Роль</th><th>Контакты</th><th></th></tr>${people.map(row).join('')}</table>
      <button type="button" id="d-add">+ Добавить исполнителя</button>
      <p class="hint">Справочник хранится внутри .bpmn. Исполнителя назначают блоку в его свойствах (⚙). Персональные данные справочника не отправляются модели при правках: они маскируются.</p></fieldset>`;
    const w = dialog('Исполнители и роли', html, (w) => {
      const modeling = svc('modeling');
      w.querySelectorAll('.r-name').forEach(i => {
        const shape = svc('elementRegistry').get(i.dataset.role);
        if (shape && i.value.trim() && i.value.trim() !== (shape.businessObject.name || '')) modeling.updateLabel(shape, i.value.trim());
      });
      const next = [...w.querySelectorAll('#d-people tr[data-id]')].map(tr => ({
        id: tr.dataset.id, name: tr.querySelector('.d-name').value.trim(), position: tr.querySelector('.d-pos').value.trim(),
        role: tr.querySelector('.d-role').value || null, contacts: tr.querySelector('.d-cont').value.trim() }));
      if (next.some(p => !p.name)) throw new Error('У каждого исполнителя должно быть ФИО (или удалите пустую строку)');
      writePeople(next);
      // clear assignments of deleted people
      const ids = new Set(next.map(p => p.id));
      svc('elementRegistry').filter(e => e.businessObject && readDetails(e.businessObject).performer && !ids.has(readDetails(e.businessObject).performer))
        .forEach(e => modeling.updateProperties(e, { documentation: buildDocs(e.businessObject, { ...readDetails(e.businessObject), performer: undefined }, readDescription(e.businessObject)) }));
      after?.();
    });
    w.querySelector('#d-add').onclick = () => {
      w.querySelector('#d-people').insertAdjacentHTML('beforeend', row({ id: uid('person'), name: '', position: '', role: null, contacts: '' }));
      bindDel(); w.querySelector('#d-people tr:last-child .d-name').focus();
    };
    const bindDel = () => w.querySelectorAll('.d-del').forEach(b => b.onclick = () => {
      const tr = b.closest('tr'); const n = used(tr.dataset.id);
      if (n && !confirm(`Исполнитель назначен на ${n} блок(ов). Удалить и снять назначения?`)) return;
      tr.remove();
    });
    bindDel();
    w.querySelector('#d-addrole').onclick = () => {
      const name = w.querySelector('#d-newrole').value.trim();
      if (!name) { w.querySelector('.be-error').textContent = 'Введите название роли'; return; }
      try { addRole(name); closeDialog(); openDirectory(after); }
      catch (e) { w.querySelector('.be-error').textContent = e.message; }
    };
  }

  // ------------------------------------------------------------------ wiring
  const eventBus = svc('eventBus');
  eventBus.on('selection.changed', (e) => {
    const sel = e.newSelection;
    if (sel.length === 1 && !document.querySelector('.djs-container.simulation')) showGear(sel[0]); else clearGear();
  });
  eventBus.on('import.done', () => { gearId = null; closeDialog(); });
  eventBus.on('tokenSimulation.toggleMode', (e) => { if (e.active) clearGear(); });
  eventBus.on('element.dblclick', 1500, (e) => {
    if (e.originalEvent?.altKey && editable(e.element)) { openEditor(e.element); return false; }
  });
  // menu entry "Исполнители и роли"
  const menu = document.querySelector('.toolbar-menu');
  if (menu && !document.getElementById('open-directory')) {
    const b = document.createElement('button');
    b.id = 'open-directory'; b.type = 'button'; b.textContent = 'Исполнители и роли';
    b.dataset.help = 'Создать исполнителей, выдать им роли (дорожки), переименовать или добавить роли.';
    b.onclick = () => { document.querySelector('.toolbar-more')?.removeAttribute('open'); openDirectory(); };
    menu.querySelector('#palette-toggle')?.after(b);
  }
  window.blockEditor = { open: openEditor, openDirectory, readDetails, readDescription, readPeople };
})();
