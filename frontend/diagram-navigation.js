/* Search BPMN steps and focus on the work of one lane without changing the XML. */
(() => {
  const toggle = $('#navigator-toggle');
  const dialog = $('#navigator');
  const search = $('#step-search');
  const participant = $('#participant-filter');
  const results = $('#search-results');
  const count = $('#search-count');
  const chip = $('#nav-focus');
  const chipText = $('#nav-focus-text');
  const registry = modeler.get('elementRegistry');
  const canvas = modeler.get('canvas');
  const markers = ['nav-search', 'nav-participant-hit', 'nav-participant-dim'];
  const normalize = value => (value || '').toLocaleLowerCase('ru-RU').replaceAll('ё', 'е').trim();
  let matches = [], refreshTimer = null;

  function flowNodes() {
    return registry.getAll().filter(el => el.type.startsWith('bpmn:') &&
      el.businessObject?.$instanceOf('bpmn:FlowNode') && el.businessObject.name &&
      registry.getGraphics(el)?.getClientRects().length);
  }
  function inLane(node, lane) {
    const refs = lane.businessObject.flowNodeRef || [];
    let businessObject = node.businessObject;
    while (businessObject) {
      if (refs.some(ref => ref.id === businessObject.id)) return true;
      businessObject = businessObject.$parent;
    }
    let parent = node.parent;
    while (parent) {
      if (parent.id === lane.id) return true;
      parent = parent.parent;
    }
    return false;
  }
  function clearMarkers() {
    for (const element of registry.getAll()) {
      for (const marker of markers) canvas.removeMarker(element, marker);
    }
  }
  function renderResults(nodes, lane) {
    results.replaceChildren();
    const query = normalize(search.value);
    if (!state.hasDiagram) { count.textContent = 'Сначала постройте или откройте схему.'; return; }
    if (!query) { count.textContent = 'Введите название действия или события.'; return; }
    count.textContent = nodes.length ? `Найдено: ${nodes.length}` : 'Совпадений нет. Попробуйте другое слово.';
    for (const element of nodes.slice(0, 30)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.searchId = element.id;
      button.dataset.help = 'Найти этот шаг на схеме и открыть его карточку.';
      const name = document.createElement('span');
      name.textContent = element.businessObject.name;
      const owner = document.createElement('small');
      owner.textContent = lane?.businessObject.name ||
        registry.getAll().find(item => item.type === 'bpmn:Lane' && inLane(element, item))?.businessObject.name || '';
      button.append(name, owner);
      button.onclick = () => goTo(element);
      results.append(button);
    }
    if (nodes.length > 30) count.textContent += ' · показаны первые 30';
  }
  function refresh() {
    const nodes = state.hasDiagram ? flowNodes() : [];
    const previous = participant.value;
    const lanes = registry.getAll().filter(el => el.type === 'bpmn:Lane' &&
      el.businessObject.name && nodes.some(node => inLane(node, el)));
    participant.replaceChildren(new Option('Все участники', ''));
    for (const lane of lanes) {
      const amount = nodes.filter(node => inLane(node, lane)).length;
      participant.add(new Option(`${lane.businessObject.name} · ${amount}`, lane.id));
    }
    participant.value = lanes.some(lane => lane.id === previous) ? previous : '';
    participant.disabled = !lanes.length;
    const lane = lanes.find(item => item.id === participant.value);
    const eligible = lane ? nodes.filter(node => inLane(node, lane)) : nodes;
    const query = normalize(search.value);
    matches = query ? eligible.filter(node => normalize(node.businessObject.name).includes(query)) : [];
    clearMarkers();
    if (lane) {
      for (const node of nodes) canvas.addMarker(node, inLane(node, lane) ? 'nav-participant-hit' : 'nav-participant-dim');
    }
    for (const node of matches) canvas.addMarker(node, 'nav-search');
    renderResults(matches, lane);
    const labels = [];
    if (lane) labels.push(lane.businessObject.name);
    if (query) labels.push('Поиск: ' + search.value.trim());
    chip.hidden = !labels.length;
    chipText.textContent = labels.join(' · ');
  }
  function goTo(element) {
    modeler.get('selection').select(element);
    if (canvas.zoom() < .72) canvas.zoom(.72);
    canvas.scrollToElement(element, matchMedia('(max-width: 760px)').matches ? 32 : 90);
    setOpen(false);
  }
  function setOpen(open) {
    dialog.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) { refresh(); search.focus(); }
  }
  function clear() {
    search.value = '';
    participant.value = '';
    refresh();
  }
  toggle.onclick = () => setOpen(dialog.hidden);
  $('#navigator-close').onclick = () => { setOpen(false); toggle.focus(); };
  $('#clear-navigation').onclick = clear;
  $('#nav-focus-clear').onclick = clear;
  search.addEventListener('input', refresh);
  participant.addEventListener('change', refresh);
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter' && matches.length) {
      event.preventDefault(); goTo(matches[0]);
    }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); setOpen(true);
    } else if (event.key === 'Escape' && !dialog.hidden) {
      event.stopPropagation(); setOpen(false); toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!dialog.hidden && !dialog.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
  });
  modeler.get('eventBus').on('import.done', () => {
    search.value = ''; participant.value = '';
    setTimeout(refresh, 0);
  });
  modeler.get('eventBus').on('commandStack.changed', () => {
    clearTimeout(refreshTimer);
    if (!dialog.hidden || search.value || participant.value) refreshTimer = setTimeout(refresh, 160);
  });
  window.diagramNavigation = { refresh, clear };
})();
