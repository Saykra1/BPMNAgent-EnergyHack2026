/* Bidirectional source ↔ diagram review and lightweight local version history. */
(() => {
  const $reader = $('#source-reader');
  const $editor = $('#text');
  const $tip = $('#source-tip');
  const sourceMarkers = ['source-preview', 'source-selected'];
  const taskKinds = new Set(['task', 'userTask', 'serviceTask', 'scriptTask', 'manualTask',
    'sendTask', 'receiveTask', 'businessRuleTask', 'subProcess']);
  let report = null, fragments = [], activeIndex = -1, editMode = true, dirty = false;
  let versions = [], versionIndex = -1, versionTimer = null, importing = false, restoring = false;
  let touchStart = null;
  const hasWord = value => /[\p{L}\p{N}]/u.test(value);
  const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function focusLabel(names) {
    const box = $('#source-focus');
    box.hidden = !names.length;
    box.textContent = names.length ? `На схеме: ${names.join(' · ')}` : '';
  }

  function clearMarker(marker) {
    const canvas = modeler.get('canvas');
    modeler.get('elementRegistry').getAll().forEach(el => canvas.removeMarker(el, marker));
  }
  function mark(ids, marker) {
    clearMarker(marker);
    const canvas = modeler.get('canvas');
    for (const id of ids) {
      const el = modeler.get('elementRegistry').get(id);
      if (el) canvas.addMarker(el, marker);
    }
  }
  function revealIfOutside(id) {
    const element = modeler.get('elementRegistry').get(id);
    const graphics = element && modeler.get('canvas').getGraphics(element);
    if (!graphics) return;
    const target = graphics.getBoundingClientRect();
    const viewport = $('#canvas').getBoundingClientRect();
    const margin = 24;
    if (target.right < viewport.left + margin || target.left > viewport.right - margin ||
        target.bottom < viewport.top + margin || target.top > viewport.bottom - margin) {
      modeler.get('canvas').scrollToElement(element, 100);
    }
  }
  function validCards() {
    if (!report || !state.hasDiagram || dirty || !$editor.value || $editor.value !== state.text) return [];
    return report.cards.filter(c => typeof c.source_quote === 'string' && c.source_quote.trim() &&
      modeler.get('elementRegistry').get(c.id));
  }
  function matchedRanges() {
    const ranges = [];
    for (const card of validCards()) {
      // Models sometimes change whitespace or letter case while copying a quote.
      // Match only the same words in the same order, retaining real text offsets.
      const words = card.source_quote.trim().split(/\s+/).map(escapeRegex);
      const pattern = new RegExp(words.join('\\s+'), 'giu');
      for (const match of state.text.matchAll(pattern)) {
        ranges.push({ start: match.index, end: match.index + match[0].length, id: card.id, name: card.name });
      }
    }
    return ranges;
  }
  function segments(text, ranges) {
    const points = [...new Set([0, text.length, ...ranges.flatMap(r => [r.start, r.end])])].sort((a, b) => a - b);
    const result = [];
    for (let i = 0; i < points.length - 1; i++) {
      const start = points[i], end = points[i + 1];
      if (end <= start) continue;
      const active = ranges.filter(r => r.start < end && r.end > start);
      const ids = [...new Set(active.map(r => r.id))];
      const names = [...new Set(active.map(r => r.name))];
      const prev = result[result.length - 1];
      if (prev && prev.end === start && prev.ids.join('|') === ids.join('|')) {
        prev.end = end;
        prev.names = [...new Set([...prev.names, ...names])];
      } else result.push({ start, end, ids, names });
    }
    return result;
  }
  function showReader() {
    const available = state.hasDiagram && Boolean(state.text) && !dirty;
    $reader.hidden = !available || editMode;
    $editor.hidden = available && !editMode;
    $('#edit-source').hidden = !state.hasDiagram || !$editor.value || dirty;
    $('#edit-source').textContent = editMode ? 'Показать связи' : 'Редактировать';
    $tip.hidden = !state.hasDiagram;
    $tip.textContent = dirty ? 'Описание изменено. Постройте схему снова, чтобы обновить связи.' :
      available && fragments.length ? 'Наведите на выделенную фразу или нажмите её. Стрелки листают связанные места.' :
      available ? 'У шагов пока нет точных цитат из текста. Выберите шаг и добавьте цитату в «Анализ → Шаг».' :
      'Добавьте описание и постройте схему, чтобы видеть связи с её шагами.';
    $('#source-nav').hidden = $reader.hidden || !fragments.length;
    $('#jev-entry').hidden = !available;
    $('#jev-entry').disabled = !fragments.length;
  }
  function renderSource() {
    const text = state.text || '';
    const ranges = matchedRanges();
    const pieces = segments(text, ranges);
    fragments = pieces.filter(p => p.ids.length && hasWord(text.slice(p.start, p.end)));
    $reader.innerHTML = pieces.map(p => p.ids.length && hasWord(text.slice(p.start, p.end))
      ? `<button type="button" class="source-fragment" data-start="${p.start}" data-end="${p.end}" data-ids="${esc(p.ids.join(','))}" aria-label="${esc(text.slice(p.start, p.end))}: ${esc(p.names.join(', '))}" title="${esc(p.names.join(', '))}">${esc(text.slice(p.start, p.end))}</button>`
      : `<span data-start="${p.start}" data-end="${p.end}">${esc(text.slice(p.start, p.end))}</span>`).join('');
    $reader.querySelectorAll('.source-fragment').forEach(button => {
      const ids = button.dataset.ids.split(',');
      const names = [...new Set(ids.map(id => report?.cards.find(c => c.id === id)?.name).filter(Boolean))];
      button.addEventListener('mouseenter', () => { mark(ids, 'source-preview'); revealIfOutside(ids[0]); focusLabel(names); });
      button.addEventListener('mouseleave', () => { clearMarker('source-preview'); if (!modeler.get('selection').get().length) focusLabel([]); });
      button.addEventListener('focus', () => { mark(ids, 'source-preview'); focusLabel(names); });
      button.addEventListener('blur', () => clearMarker('source-preview'));
      button.addEventListener('click', () => {
        const first = modeler.get('elementRegistry').get(ids[0]);
        if (!first) return;
        activeIndex = fragments.findIndex(p => p.start === Number(button.dataset.start));
        mark(ids, 'source-selected');
        focusLabel(names);
        updatePosition();
        modeler.get('selection').select(first);
        const canvas = modeler.get('canvas');
        if (canvas.zoom() < .9) canvas.zoom(.9);
        canvas.scrollToElement(first, matchMedia('(max-width: 760px)').matches ? 28 : 120);
        if (matchMedia('(max-width: 650px)').matches) $('#center').scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
    if (activeIndex >= fragments.length) activeIndex = fragments.length - 1;
    updatePosition();
    showReader();
    renderCoverage();
  }
  function updatePosition() {
    $('#source-position').textContent = fragments.length ? `${Math.max(0, activeIndex) + 1} / ${fragments.length}` : '';
    $('#source-prev').disabled = fragments.length < 2;
    $('#source-next').disabled = fragments.length < 2;
  }
  function goTo(direction) {
    if (!fragments.length) return;
    activeIndex = ((activeIndex < 0 ? (direction > 0 ? -1 : 0) : activeIndex) + direction + fragments.length) % fragments.length;
    const part = fragments[activeIndex];
    const button = [...$reader.querySelectorAll('.source-fragment')]
      .find(el => Number(el.dataset.start) === part.start);
    button?.click();
    button?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  function renderCoverage() {
    const box = $('#tab-coverage');
    if (!state.hasDiagram) { box.innerHTML = '<p class="muted">Постройте схему, чтобы проверить связь текста с шагами.</p>'; return; }
    if (!state.text) { box.innerHTML = '<p class="muted">Описание не загружено. Добавьте текст, чтобы проверить связь шагов с исходными фразами.</p>'; return; }
    if (dirty) { box.innerHTML = '<p class="muted">Описание изменено. Постройте схему снова, чтобы обновить связи.</p>'; return; }
    const cards = (report?.cards || []).filter(c => taskKinds.has(c.kind));
    const ranges = matchedRanges();
    const linkedIds = new Set(ranges.map(r => r.id));
    const linked = cards.filter(c => linkedIds.has(c.id));
    const sentences = [...state.text.matchAll(/[^.!?\n]+[.!?]?/g)]
      .map(m => ({ start: m.index, end: m.index + m[0].length, text: m[0].trim() }))
      .filter(s => s.text.length >= 18 && !s.text.endsWith(':'));
    const withoutLink = sentences.filter(s => !ranges.some(r => r.start < s.end && r.end > s.start));
    const stepsWithout = cards.filter(c => !linked.includes(c) && c.name);
    box.innerHTML = `<div id="process-audit"></div><details class="coverage-details"><summary>Точные цитаты и ручная привязка</summary><h3>Связи с описанием</h3>
      <p class="hint">Связь означает, что цитата из описания найдена в тексте. Проверку смысла выполняет аналитик.</p>
      <div class="coverage-stats"><div><b>${linked.length} / ${cards.length}</b><span>шагов с цитатой</span></div>
      <div><b>${sentences.length - withoutLink.length} / ${sentences.length}</b><span>фрагментов с привязкой</span></div></div>
      ${withoutLink.length ? `<h4>Фрагменты без связи</h4><p class="hint">Выберите шаг на схеме и нажмите «Привязать», чтобы добавить точную цитату.</p>
        ${withoutLink.map((s, i) => `<div class="coverage-item"><p>${esc(s.text)}</p><button data-unlinked="${i}">Привязать к шагу</button></div>`).join('')}` : '<p class="soft-success">У всех длинных фрагментов есть хотя бы одна связь.</p>'}
      ${stepsWithout.length ? `<h4>Шаги без цитаты</h4>${stepsWithout.map(c => `<button class="coverage-step" data-node="${esc(c.id)}">${esc(c.name)}</button>`).join('')}` : ''}
      <div class="jev-audit"><button id="jev-check" data-help="Jev сравнит названия шагов с прикреплёнными цитатами и покажет сомнительные связи. Диаграмма не изменится.">Проверить смысл связей · Jev</button>
      <p class="hint">Необязательная проверка через OpenRouter. Схема и текст не изменяются.</p><div id="jev-results" role="status"></div></div>
      <p id="coverage-status" class="hint" role="status"></p></details>`;
    window.processAudit?.render({ report, ranges, text: state.text });
    box.querySelectorAll('[data-node]').forEach(button => button.onclick = () => {
      const el = modeler.get('elementRegistry').get(button.dataset.node);
      if (el) { modeler.get('selection').select(el); modeler.get('canvas').scrollToElement(el); }
    });
    box.querySelectorAll('[data-unlinked]').forEach(button => button.onclick = () => {
      const sentence = withoutLink[Number(button.dataset.unlinked)];
      const selected = modeler.get('selection').get();
      const element = selected.length === 1 ? selected[0] : null;
      const status = $('#coverage-status');
      if (!element || !element.businessObject.$instanceOf('bpmn:FlowNode')) {
        status.textContent = 'Сначала выберите нужный шаг на схеме.'; return;
      }
      const old = (element.businessObject.documentation || []).find(d => (d.text || '').startsWith('BPMN_AGENT_DETAILS:'));
      let details = {};
      try { if (old) details = JSON.parse(old.text.slice('BPMN_AGENT_DETAILS:'.length)); } catch { /* malformed metadata */ }
      if (details.source_quote) { status.textContent = 'У выбранного шага уже есть цитата. Измените её в карточке шага.'; return; }
      const all = (element.businessObject.documentation || []).filter(d => d !== old);
      const value = { ...details, source_quote: sentence.text };
      const doc = modeler.get('moddle').create('bpmn:Documentation', {
        textFormat: 'application/json', text: 'BPMN_AGENT_DETAILS:' + JSON.stringify(value)
      });
      modeler.get('modeling').updateProperties(element, { documentation: [...all, doc] });
      status.textContent = 'Цитата привязана к выбранному шагу.';
    });
    $('#jev-check').onclick = async () => {
      const button = $('#jev-check');
      const output = $('#jev-results');
      button.disabled = true;
      output.textContent = 'Сверяю цитаты и шаги…';
      try {
        const { xml } = await modeler.saveXML({ format: true });
        const result = await api('/api/jev-review', { xml, text: state.text.slice(0, 30000), privacy: window.privacyUI.options() });
        if (!output.isConnected) return;
        const doubtful = result.items.filter(item => item.support < 0.7);
        output.innerHTML = `<p><strong>Проверено ${result.checked} из ${result.total} связей.</strong> ${doubtful.length ? `Стоит просмотреть: ${doubtful.length}.` : 'Явно сомнительных связей не найдено.'}</p>
          ${doubtful.map(item => `<article class="jev-item"><b>${esc(item.name)}</b><span class="pill warn">${Math.round(item.support * 100)}% соответствия</span><blockquote>${esc(item.quote)}</blockquote><button data-jev-node="${esc(item.id)}">Показать шаг</button></article>`).join('')}
          <p class="hint">${esc(result.note)}${result.checked < result.total ? ' Остальные связи не вошли в эту проверку.' : ''}</p>`;
        output.querySelectorAll('[data-jev-node]').forEach(node => node.onclick = () => {
          const element = modeler.get('elementRegistry').get(node.dataset.jevNode);
          if (element) { modeler.get('selection').select(element); modeler.get('canvas').scrollToElement(element); }
        });
      } catch (error) {
        if (output.isConnected) output.textContent = 'Jev недоступен: ' + error.message;
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    };
  }
  function onSelect(id) {
    clearMarker('source-selected');
    const candidates = [...$reader.querySelectorAll('.source-fragment')];
    candidates.forEach(el => el.classList.remove('source-current'));
    if (!id || $reader.hidden) { focusLabel([]); return; }
    const button = candidates.find(el => el.dataset.ids.split(',').includes(id));
    if (!button) { focusLabel([]); return; }
    button.classList.add('source-current');
    activeIndex = fragments.findIndex(p => p.start === Number(button.dataset.start));
    updatePosition();
    mark([id], 'source-selected');
    focusLabel([report?.cards.find(c => c.id === id)?.name].filter(Boolean));
    if (!matchMedia('(max-width: 650px)').matches) button.scrollIntoView({ block: 'nearest' });
  }
  function revealRange(start, end) {
    if ($reader.hidden) return;
    $reader.querySelectorAll('.source-gap-current').forEach(node => node.classList.remove('source-gap-current'));
    const matches = [...$reader.children].filter(node => Number(node.dataset.start) < end && Number(node.dataset.end) > start);
    matches.forEach(node => node.classList.add('source-gap-current'));
    matches[0]?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (matchMedia('(max-width: 760px)').matches) $('#left').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function capture(label = 'Изменение схемы') {
    if (!state.hasDiagram || importing || restoring) return;
    const { xml } = await modeler.saveXML({ format: true });
    if (importing || restoring || !state.hasDiagram) return;
    if (versions[versionIndex]?.xml === xml && versions[versionIndex]?.text === state.text) return;
    if (versionIndex < versions.length - 1) versions = versions.slice(0, versionIndex + 1);
    versions.push({ xml, text: state.text, code: state.code, plan: state.plan,
      meta: { questions: state.lastResult?.questions || [], assumptions: state.lastResult?.assumptions || [] },
      label, at: new Date() });
    if (versions.length > 20) versions.shift();
    versionIndex = versions.length - 1;
    renderHistory();
  }
  function renderHistory() {
    const box = $('#tab-history');
    box.innerHTML = `<h3>История в этой вкладке</h3><p class="hint">Вернитесь к любой сохранённой версии схемы. Последние 20 изменений доступны, пока открыта вкладка браузера.</p>` +
      (versions.length ? versions.map((v, i) => `<div class="version ${i === versionIndex ? 'current' : ''}">
        <span><b>${esc(v.label)}</b><small>${v.at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</small></span>
        <button data-version="${i}" ${i === versionIndex ? 'disabled' : ''}>${i === versionIndex ? 'Текущая' : 'Вернуть'}</button></div>`).reverse().join('') : '<p class="muted">Схема пока не построена.</p>');
    box.querySelectorAll('[data-version]').forEach(b => b.onclick = () => restore(Number(b.dataset.version)));
  }
  async function restore(index) {
    if (index === versionIndex || !versions[index]) return;
    await capture();
    const saved = versions[index];
    restoring = true;
    try {
      state.text = saved.text;
      state.code = saved.code;
      state.plan = saved.plan;
      state.lastResult = saved.meta;
      $editor.value = saved.text;
      $('#code').value = saved.code;
      $('#plan-json').textContent = saved.plan ? JSON.stringify(saved.plan, null, 2) : 'Для этой версии план не сохранён.';
      await showXml(saved.xml);
      versionIndex = index;
      dirty = false; editMode = false;
      await window.agentFeatures.onResult();
      $('#summary').textContent = 'Версия восстановлена. Схема снова доступна для правок.';
      renderHistory();
    } catch (e) { $('#summary').textContent = 'Не удалось восстановить версию: ' + e.message; }
    finally { restoring = false; importing = false; }
  }
  function beforeImport() {
    importing = true;
    report = null;
    clearTimeout(versionTimer);
    sourceMarkers.forEach(clearMarker);
    focusLabel([]);
  }
  async function onResult() {
    importing = false;
    dirty = $editor.value !== state.text;
    editMode = !state.hasDiagram || !state.text || dirty;
    renderSource();
    if (!restoring) await capture(versions.length ? 'Новая версия схемы' : 'Исходная схема');
  }
  function onInspect(data) { report = data; renderSource(); }

  $('#edit-source').onclick = () => {
    if (dirty) return;
    editMode = !editMode;
    showReader();
    if (editMode) $editor.focus();
  };
  $('#jev-entry').onclick = () => {
    window.workspaceUI?.openInsights();
    document.querySelector('.tabs [data-tab="coverage"]').click();
    $('.coverage-details').open = true;
    const button = $('#jev-check');
    button?.scrollIntoView({ block: 'nearest' });
    button?.click();
  };
  $editor.addEventListener('input', () => {
    dirty = Boolean(state.hasDiagram && $editor.value !== state.text);
    if (dirty) { editMode = true; sourceMarkers.forEach(clearMarker); renderSource(); }
  });
  $('#source-prev').onclick = () => goTo(-1);
  $('#source-next').onclick = () => goTo(1);
  $reader.addEventListener('touchstart', e => { touchStart = [e.touches[0].clientX, e.touches[0].clientY]; }, { passive: true });
  $reader.addEventListener('touchend', e => {
    if (!touchStart) return;
    const dx = e.changedTouches[0].clientX - touchStart[0];
    const dy = e.changedTouches[0].clientY - touchStart[1];
    touchStart = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) goTo(dx < 0 ? 1 : -1);
  }, { passive: true });
  document.querySelectorAll('.advanced [data-tab]').forEach(b => b.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => { t.hidden = t.id !== 'tab-' + b.dataset.tab; });
  });
  modeler.get('eventBus').on('commandStack.changed', () => {
    clearTimeout(versionTimer);
    if (!importing && !restoring) versionTimer = setTimeout(() => capture(), 900);
  });
  window.sourceReview = { beforeImport, onResult, onInspect, onSelect, revealRange };
  renderHistory();
})();
