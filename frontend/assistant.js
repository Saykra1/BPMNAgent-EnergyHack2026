/* Analyst workflow: reviewed plans, traceable steps, energy exception review. */
(() => {
  const PREFIX = 'BPMN_AGENT_DETAILS:';
  let pending = null, inspection = null, selectedId = null, requestId = 0, timer = null;
  let simulation = false, snapshot = '';

  function tab(name) {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab').forEach(t => { t.hidden = t.id !== 'tab-' + name; });
  }
  function clearInterview() {
    pending = null;
    $('#interview').hidden = true;
    $('#interview').replaceChildren();
  }
  function notice(message) {
    $('#summary').textContent = message;
  }
  function readDetails(element) {
    for (const doc of element.businessObject.documentation || []) {
      if ((doc.text || '').startsWith(PREFIX)) {
        try { return JSON.parse(doc.text.slice(PREFIX.length)); } catch { return {}; }
      }
    }
    return {};
  }
  function writeDetails(element, next) {
    const old = (element.businessObject.documentation || []).filter(d => !(d.text || '').startsWith(PREFIX));
    const doc = modeler.get('moddle').create('bpmn:Documentation', { textFormat: 'application/json', text: PREFIX + JSON.stringify(next) });
    modeler.get('modeling').updateProperties(element, { documentation: [...old, doc] });
  }
  function renderInterview() {
    const box = $('#interview');
    box.hidden = false;
    const questions = pending.plan.questions || [];
    box.innerHTML = `<h4>Уточним процесс перед построением</h4>
      <p>${esc(pending.plan.title)} · ${pending.plan.elements.length} элементов</p>
      <p class="hint">Ответьте на важные вопросы или постройте черновик с неразрешёнными вопросами. Ответы уточнят план через модель.</p>
      ${questions.map((q, i) => `<label for="answer-${i}">${esc(q)}</label><textarea class="answer" id="answer-${i}" data-question="${i}" maxlength="1500" placeholder="Ответ заказчика; можно оставить пустым"></textarea>`).join('')}
      ${!questions.length ? '<p>Планировщик не задал вопросов. Проверьте допущения перед построением.</p>' : ''}
      ${(pending.plan.assumptions || []).length ? `<details><summary>Допущения (${pending.plan.assumptions.length})</summary><ul class="list">${pending.plan.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul></details>` : ''}
      <div class="row">${questions.length ? '<button id="answer-apply">Учесть ответы</button>' : ''}<button id="plan-build" class="primary">${questions.length ? 'Построить черновик' : 'Построить схему'}</button></div>
      <p id="interview-status" class="hint" role="status"></p>`;
    if ($('#answer-apply')) $('#answer-apply').onclick = async () => {
      const answers = [...box.querySelectorAll('.answer')].filter(el => el.value.trim())
        .map(el => `${questions[+el.dataset.question]}\nОтвет: ${el.value.trim()}`);
      if (!answers.length) { $('#interview-status').textContent = 'Заполните хотя бы один ответ или постройте черновик.'; return; }
      const context = pending.text + '\n\nУточнения заказчика:\n' + answers.join('\n\n');
      state.privacy.trusted.push(...answers);   // typed by the analyst: never taken for injection
      if (context.length > 20000) { $('#interview-status').textContent = 'Описание вместе с ответами превышает 20 000 символов. Сократите ответы.'; return; }
      await prepare(context, true);
    };
    $('#plan-build').onclick = async () => {
      const current = pending;
      busy(true, 'Строю схему по проверенному плану…');
      try {
        const res = await api('/api/from-plan', { plan: current.plan, text: current.text, resolutions: state.resolutions });
        if (!res.xml) throw new Error(res.message || 'Не удалось построить план');
        if (!res.privacy?.requests) res.privacy = current.privacy;   // the model worked only at the planning step
        state.text = current.text;
        $('#text').value = current.text;
        await applyResult(res);
        clearInterview();
        tab('report');
      } catch (e) { $('#interview-status').textContent = e.message; }
      finally { busy(false); }
    };
  }
  async function prepare(text, answering = false) {
    busy(true, answering ? 'Учитываю ответы заказчика…' : 'Выделяю шаги и вопросы к процессу…');
    let prepared = false;
    try {
      const res = await api('/api/prepare', { text, resolutions: state.resolutions, privacy: window.privacyUI.options() });
      if (!res.ok) throw new Error(res.message || 'Не удалось подготовить план');
      if (res.conflicts?.length) {
        clearInterview();
        notice('Описание противоречит само себе. После выбора старшего правила план будет составлен заново.');
        window.conflictGate.show(res.conflicts, (decided) => { state.resolutions.push(...decided); prepare(text, answering); });
        return;
      }
      pending = { plan: res.plan, text, privacy: res.privacy };
      renderInterview();
      prepared = true;
      notice('План подготовлен. Ответьте на вопросы или постройте схему.');
    } catch (e) {
      if (answering && $('#interview-status')) $('#interview-status').textContent = e.message;
      else notice(e.message);
    } finally {
      busy(false);
      if (prepared && matchMedia('(max-width: 760px)').matches) $('#interview').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function renderCard() {
    const box = $('#tab-element');
    const element = selectedId && modeler.get('elementRegistry').get(selectedId);
    if (!element || element.labelTarget || !element.businessObject.$instanceOf('bpmn:FlowNode')) {
      box.innerHTML = '<p class="muted">Выберите задачу, событие или шлюз на схеме.</p>';
      return;
    }
    const details = readDetails(element);
    const card = inspection?.cards.find(c => c.id === selectedId);
    const quote = typeof details.source_quote === 'string' ? details.source_quote : '';
    const found = quote && state.text.includes(quote);
    box.innerHTML = `<h3>${esc(element.businessObject.name || 'Без названия')}</h3>
      ${card?.role ? `<p class="muted">Исполнитель: ${esc(card.role)}</p>` : ''}
      <h4>Основание в описании</h4>
      ${quote ? `<blockquote>${esc(quote)}</blockquote><p class="hint">${found ? 'Цитата найдена в исходном описании. Смысловое соответствие проверяет аналитик.' : 'Цитата сохранена в BPMN; совпадение с текущим описанием не подтверждено.'}</p>${found ? '<button id="show-source">Показать в тексте</button>' : ''}` : '<p class="muted">Основание не привязано. Это не доказывает, что шаг отсутствует в описании.</p>'}
      ${details.assumption ? `<div class="notice">Допущение: ${esc(details.assumption)}</div>` : ''}
      <h4>Карточка срока и документов</h4>
      <p class="hint">Указывайте срок вместе с точкой отсчёта. Это описание обязательства, не исполняемый таймер.</p>
      <label for="detail-deadline">Срок и точка отсчёта</label><input id="detail-deadline" maxlength="500" placeholder="20 рабочих дней с получения уведомления" value="${esc(details.deadline || '')}">
      <label for="detail-documents">Документы — по одному на строку</label><textarea id="detail-documents" maxlength="4000" placeholder="Заявка\nТехнические условия">${esc((Array.isArray(details.documents) ? details.documents : []).join('\n'))}</textarea>
      <label for="detail-quote">Цитата из исходного описания</label><textarea id="detail-quote" maxlength="3000" placeholder="Вставьте точный фрагмент описания">${esc(quote)}</textarea>
      <label for="detail-assumption">Допущение / требует уточнения</label><textarea id="detail-assumption" maxlength="1500">${esc(details.assumption || '')}</textarea>
      <button id="detail-save" class="primary" ${state.busy || simulation ? 'disabled' : ''}>Сохранить карточку</button>
      <p class="hint">Карточка хранится внутри .bpmn и сохраняется при повторном открытии. Отмена — Ctrl+Z.</p>
      <p id="detail-status" role="status"></p>`;
    if ($('#show-source')) $('#show-source').onclick = () => {
      const input = $('#text');
      if (input.value !== state.text) { notice('Описание изменено после построения. Исходная цитата показана в карточке.'); return; }
      const reader = $('#source-reader');
      if (!reader.hidden) {
        const fragment = reader.querySelector('.source-current') ||
          [...reader.querySelectorAll('.source-fragment')].find(b => quote.includes(b.textContent.trim()));
        window.workspaceUI?.closeInsights();
        (fragment || reader).scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      const start = input.value.indexOf(quote);
      input.focus(); input.setSelectionRange(start, start + quote.length);
    };
    $('#detail-save').onclick = () => {
      if (state.busy || simulation) return;
      const sourceQuote = $('#detail-quote').value.trim();
      // Existing imported quotes can be retained when the source text is unavailable.
      if (sourceQuote && sourceQuote !== quote && !state.text.includes(sourceQuote)) {
        $('#detail-status').textContent = 'Новая цитата должна дословно встречаться в описании, по которому построена схема.'; return;
      }
      const next = { source_quote: sourceQuote, assumption: $('#detail-assumption').value.trim(),
        deadline: $('#detail-deadline').value.trim(), documents: $('#detail-documents').value.split('\n').map(s => s.trim()).filter(Boolean) };
      writeDetails(element, next);
      $('#detail-status').textContent = 'Карточка сохранена в схеме.';
    };
  }

  function renderEnergy() {
    const box = $('#tab-energy');
    box.innerHTML = `<h3>Исключительные ситуации</h3><p class="hint">${esc(inspection.note)}</p>` + inspection.energy_checks.map(c => `
      <article class="review-card"><b>${esc(c.title)}</b>
      <p><span class="pill ${c.status === 'review' ? 'warn' : ''}">${c.status === 'mentioned' ? 'Есть упоминания — проверьте маршрут' : c.status === 'review' ? 'Нужна проверка аналитика' : 'Применимость не определена'}</span></p>
      <p>${esc(c.question)}</p><div class="row">${c.elements.length ? `<button data-check="${esc(c.id)}">Показать элементы</button>` : ''}<button data-discuss="${esc(c.id)}">Обсудить правку</button></div></article>`).join('');
    box.querySelectorAll('[data-check]').forEach(b => b.onclick = () => highlight(inspection.energy_checks.find(c => c.id === b.dataset.check).elements));
    box.querySelectorAll('[data-discuss]').forEach(b => b.onclick = () => {
      const c = inspection.energy_checks.find(c => c.id === b.dataset.discuss);
      $('#instruction').value = c.question + ' Решение заказчика: ';
      $('#instruction').focus();
      notice('Допишите решение заказчика и отправьте просьбу о правке. Ветви не добавляются автоматически.');
    });
    const obligations = inspection.cards.filter(c => c.deadline || c.documents?.length);
    box.insertAdjacentHTML('beforeend', `<h4>Сроки и документы (${obligations.length})</h4><p class="hint">Сведения из карточек, без проверки нормативных требований.</p>` +
      (obligations.length ? obligations.map(c => `<button class="obligation" data-card="${esc(c.id)}"><b>${esc(c.name || 'Без названия')}</b><br>${esc(c.deadline || 'Срок не указан')}<br><small>${esc((c.documents || []).join(', '))}</small></button>`).join('') : '<p class="muted">Выберите шаг на схеме и заполните карточку во вкладке «Шаг».</p>'));
    box.querySelectorAll('[data-card]').forEach(b => b.onclick = () => {
      const element = modeler.get('elementRegistry').get(b.dataset.card);
      if (element) { selectedId = element.id; highlight([element.id]); renderCard(); tab('element'); }
    });
  }

  async function inspectCurrent(force = false) {
    if (!state.hasDiagram || simulation) return;
    const id = ++requestId;
    try {
      const { xml } = await modeler.saveXML({ format: true });
      const signature = xml + state.text;
      if (!force && signature === snapshot) return;
      const res = await api('/api/inspect', { xml, text: state.text.slice(0, 30000) });
      if (id !== requestId) return;
      snapshot = signature; inspection = res;
      window.diagramStatus?.update({ issues: res.issues, xsd_errors: res.xsd_errors });
      window.sourceReview?.onInspect(res);
      const errors = res.issues.filter(i => i.level === 'error');
      const report = $('#tab-report');
      report.innerHTML = `<h3>Проверка текущей схемы</h3><p class="hint">Обновляется после ручных изменений. Замечания не исправляются без вашего решения.</p>
        <span class="pill ${res.xsd_errors.length ? 'err' : 'ok'}">XML: ${res.xsd_errors.length ? 'ошибки XSD' : 'XSD пройден'}</span>
        <span class="pill ${errors.length ? 'err' : 'ok'}">${errors.length ? errors.length + ' ошибок структуры' : 'Структурных ошибок не найдено'}</span>
        ${res.unsupported.length ? `<div class="notice">Ограниченная проверка: есть неподдерживаемые элементы (${esc(res.unsupported.join(', '))}).</div>` : ''}
        ${res.issues.map((i, n) => `<article class="issue ${esc(i.level)}" data-issue="${n}"><b>${esc(i.title)}</b><p>${esc(i.message)}</p><p>${esc(i.advice)}</p></article>`).join('')}
        ${res.xsd_errors.length ? `<pre class="code">${esc(res.xsd_errors.join('\n'))}</pre>` : ''}
        <p class="hint">Отсутствие замечаний не доказывает полноту бизнес-процесса. Проверьте вкладку «Исключения».</p>`;
      if (state.lastResult?.assumptions?.length) report.insertAdjacentHTML('beforeend', '<h4>Допущения при построении</h4><ul class="list">' + state.lastResult.assumptions.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>');
      if (state.lastResult?.questions?.length) report.insertAdjacentHTML('beforeend', '<h4>Нерешённые вопросы при построении</h4><ul class="list">' + state.lastResult.questions.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>');
      const decided = (state.lastResult?.resolutions || []).filter(r => r.chosen !== 'both');
      if (decided.length) report.insertAdjacentHTML('beforeend', '<h4>Противоречия в описании</h4><ul class="list">' + decided.map(r => `<li>${r.topic ? esc(r.topic) + ': п' : 'П'}ринято «${esc((r.chosen === 'a' ? r.rule_a : r.rule_b).replace(/[\s.;,]+$/, ''))}». Сноска на схеме напоминает поправить текст.</li>`).join('') + '</ul>');
      report.querySelectorAll('[data-issue]').forEach(el => el.onclick = () => highlight(res.issues[+el.dataset.issue].elements));
      window.factCheck?.onInspect(res);
      window.privacyUI?.renderReport();
      renderEnergy();
      // Do not erase an in-progress edit while an automatic check finishes.
      if (!$('#tab-element').contains(document.activeElement)) renderCard();
    } catch (e) {
      if (id === requestId) {
        inspection = null; snapshot = '';
        window.diagramStatus?.failed();
        $('#tab-report').textContent = 'Проверка не завершена: ' + e.message;
        $('#tab-energy').textContent = 'Проверка исключений недоступна, пока не удалось прочитать схему.';
      }
    }
  }
  function onBusy(on) {
    if (on && simulation) modeler.get('toggleMode').toggleMode(false);
    document.querySelectorAll('#interview button, #interview textarea, #detail-save, #inspect-current').forEach(el => { el.disabled = on; });
    if (simulation && $('#detail-save')) $('#detail-save').disabled = true;
  }
  window.agentFeatures = {
    prepare, clearInterview, onBusy,
    details: { read: readDetails, write: writeDetails },
    beforeImport: () => {
      if (simulation) modeler.get('toggleMode').toggleMode(false);
      window.sourceReview?.beforeImport();
      window.factCheck?.beforeImport();
      ++requestId; clearTimeout(timer); inspection = null; snapshot = ''; selectedId = null;
    },
    onResult: async () => { selectedId = null; snapshot = ''; inspection = null; await inspectCurrent(true); await window.sourceReview?.onResult(); }
  };
  $('#text').addEventListener('input', clearInterview);
  $('#mode').addEventListener('change', clearInterview);
  $('#example').addEventListener('change', clearInterview);
  $('#inspect-current').onclick = async () => { tab('report'); await inspectCurrent(true); };
  $('#simulate').onclick = () => {
    if (!state.hasDiagram) { notice('Сначала постройте или откройте схему.'); return; }
    modeler.get('toggleMode').toggleMode();
  };
  modeler.get('eventBus').on('tokenSimulation.toggleMode', e => {
    simulation = e.active;
    $('#simulation-hint').hidden = !simulation;
    $('#simulate').textContent = simulation ? '■ Завершить проигрывание' : '▶ Проиграть';
    $('#simulate').dataset.help = simulation ? 'Закончить проверку маршрута и вернуться к редактированию.' :
      'Пошагово пройти по маршрутам процесса и проверить ветвления.';
    if ($('#detail-save')) $('#detail-save').disabled = simulation || state.busy;
  });
  modeler.get('eventBus').on('selection.changed', e => {
    selectedId = e.newSelection.length === 1 ? e.newSelection[0].id : null;
    window.sourceReview?.onSelect(selectedId);
    renderCard();
    if (selectedId && !simulation) tab('element');
  });
  modeler.get('eventBus').on('commandStack.changed', () => {
    window.diagramStatus?.pending();
    ++requestId;
    clearTimeout(timer);
    timer = setTimeout(() => inspectCurrent(), 600);
  });
})();
