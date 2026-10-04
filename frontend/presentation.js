/* Guided, editable BPMN walkthrough. The graph remains the source of truth. */
(() => {
  const registry = modeler.get('elementRegistry');
  const canvas = modeler.get('canvas');
  const overlays = modeler.get('overlays');
  const panel = $('#tour-panel');
  let route = [], index = 0, timer = null, overlayId = null, marked = null;

  function flowNodes() {
    return registry.getAll().filter(el => el.type !== 'label' && el.businessObject?.$instanceOf('bpmn:FlowNode'));
  }
  function outgoing(el) {
    return (el?.outgoing || []).filter(flow => flow.type === 'bpmn:SequenceFlow' && flow.target);
  }
  function firstStep() {
    const nodes = flowNodes();
    const starts = nodes.filter(el => el.businessObject.$instanceOf('bpmn:StartEvent'));
    const topLevel = starts.filter(el => {
      let parent = el.parent;
      while (parent) { if (parent.type === 'bpmn:SubProcess') return false; parent = parent.parent; }
      return true;
    });
    return (topLevel.length ? topLevel : starts.length ? starts : nodes)
      .sort((a, b) => (a.x || 0) - (b.x || 0))[0];
  }
  function metadata(el) {
    const raw = (el.businessObject.documentation || []).find(item => (item.text || '').startsWith('BPMN_AGENT_DETAILS:'))?.text;
    try { return raw ? JSON.parse(raw.slice('BPMN_AGENT_DETAILS:'.length)) : {}; }
    catch { return {}; }
  }
  function quoteFor(el) {
    const quote = metadata(el).source_quote || '';
    return quote && state.text?.includes(quote) ? `«${quote}»` : '';
  }
  function roleFor(el) {
    const lane = el.businessObject.lanes?.[0];
    if (lane?.name) return lane.name;
    let parent = el.parent;
    while (parent) {
      if (parent.type === 'bpmn:Participant' || parent.type === 'bpmn:Lane') return parent.businessObject?.name || '';
      parent = parent.parent;
    }
    return '';
  }
  function clearVisual() {
    if (marked) canvas.removeMarker(marked, 'tour-current');
    if (overlayId !== null) overlays.remove(overlayId);
    marked = null; overlayId = null;
  }
  function stopAuto() {
    clearInterval(timer); timer = null;
    $('#tour-play').textContent = '▶ Автопоказ';
  }
  function stop() {
    stopAuto(); clearVisual(); route = []; index = 0;
    panel.hidden = true;
    document.body.classList.remove('tour-open');
    $('#tour-toggle').setAttribute('aria-pressed', 'false');
  }
  function show() {
    const step = route[index], el = step && registry.get(step.id);
    if (!el) { stop(); return; }
    clearVisual();
    canvas.addMarker(el, 'tour-current'); marked = el;
    const cat = document.createElement('img');
    cat.src = '/static/assets/mascot-cat.png'; cat.alt = '';
    cat.className = 'tour-node-cat';
    overlayId = overlays.add(el.id, { position: { top: -36, left: -12 }, html: cat });
    modeler.get('selection').select(el);
    if (canvas.zoom() < .8) canvas.zoom(.8);
    canvas.scrollToElement(el, 100);
    const name = el.businessObject.name || ({'bpmn:StartEvent': 'Начало процесса',
      'bpmn:EndEvent': 'Конец процесса', 'bpmn:ExclusiveGateway': 'Развилка',
      'bpmn:ParallelGateway': 'Параллельные ветви'})[el.type] || 'Шаг процесса';
    $('#tour-title').textContent = name;
    const role = roleFor(el);
    $('#tour-role').textContent = role ? `Участник: ${role}` : 'Шаг процесса';
    const quote = quoteFor(el);
    const isStart = el.businessObject.$instanceOf('bpmn:StartEvent');
    const isEnd = el.businessObject.$instanceOf('bpmn:EndEvent');
    $('#tour-quote').textContent = quote || (isStart ? 'Здесь начинается маршрут.' :
      isEnd ? 'Здесь маршрут завершается.' : 'У этого шага нет точной цитаты из исходного описания.');
    $('#tour-quote').classList.toggle('unlinked', !quote && !isStart && !isEnd);
    $('#tour-position').textContent = `Шаг ${index + 1}`;
    $('#tour-prev').disabled = index === 0;
    const flows = outgoing(el), choices = $('#tour-choices');
    choices.innerHTML = '';
    const forward = route[index + 1];
    if (forward) {
      choices.hidden = true;
      $('#tour-next').disabled = false;
      $('#tour-next').textContent = 'Далее →';
    } else if (flows.length > 1) {
      stopAuto(); choices.hidden = false;
      const title = document.createElement('p');
      title.textContent = el.type === 'bpmn:ParallelGateway' ?
        'Ветви идут параллельно. Выберите, какую посмотреть первой.' : 'Выберите ветвь процесса';
      choices.append(title);
      flows.forEach(flow => {
        const button = document.createElement('button');
        const condition = flow.businessObject.name || 'Без подписи';
        const destination = flow.target.businessObject.name || ({'bpmn:EndEvent': 'Конец процесса',
          'bpmn:ExclusiveGateway': 'Развилка', 'bpmn:ParallelGateway': 'Параллельные ветви'})[flow.target.type] || 'Следующий шаг';
        button.textContent = `${condition} → ${destination}`;
        button.onclick = () => advance(flow);
        choices.append(button);
      });
      $('#tour-next').disabled = true;
      $('#tour-next').textContent = 'Выберите ветвь';
    } else {
      choices.hidden = true;
      $('#tour-next').disabled = !flows.length;
      $('#tour-next').textContent = flows.length ? 'Далее →' : 'Конец маршрута';
    }
    if (matchMedia('(max-width: 760px)').matches) {
      $('#center').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }
  function advance(flow) {
    if (!route[index + 1]) {
      const target = flow?.target;
      if (!target) return;
      // Let a loop appear twice, then stop rather than cycling forever.
      if (route.filter(item => item.id === target.id).length >= 2) { stopAuto(); return; }
      route.push({ id: target.id });
    }
    index += 1; show();
  }
  function next() {
    const el = registry.get(route[index]?.id);
    const flows = outgoing(el);
    if (!route[index + 1] && flows.length !== 1) return;
    advance(flows[0]);
  }
  function play() {
    if (timer) { stopAuto(); return; }
    $('#tour-play').textContent = 'Ⅱ Пауза';
    timer = setInterval(() => {
      const el = registry.get(route[index]?.id);
      if (!route[index + 1] && outgoing(el).length !== 1) { stopAuto(); return; }
      next();
    }, 2600);
  }
  function start() {
    if (!state.hasDiagram) return;
    if (!panel.hidden) { stop(); return; }
    if ($('#simulate').textContent.includes('Завершить')) $('#simulate').click();
    const first = firstStep();
    if (!first) return;
    route = [{ id: first.id }]; index = 0;
    panel.hidden = false; document.body.classList.add('tour-open');
    $('#tour-toggle').setAttribute('aria-pressed', 'true');
    window.workspaceUI?.closeInsights();
    show();
  }
  $('#tour-toggle').onclick = start;
  $('#tour-close').onclick = stop;
  $('#tour-prev').onclick = () => { if (index > 0) { stopAuto(); index -= 1; show(); } };
  $('#tour-next').onclick = next;
  $('#tour-play').onclick = play;
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) stop(); });
  modeler.get('eventBus').on('tokenSimulation.toggleMode', event => { if (event.active) stop(); });
  window.presentation = { stop };
})();
