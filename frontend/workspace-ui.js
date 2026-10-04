/* Workspace controls and compact, consistent help for static and generated actions. */
(() => {
  const panel = $('#right');
  const toggle = $('#insights-toggle');
  const backdrop = $('#panel-backdrop');
  const popover = $('#help-popover');
  let tooltipTarget = null;

  function setInsights(open) {
    document.body.classList.toggle('insights-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    panel.setAttribute('aria-hidden', String(!open));
    panel.inert = !open;
    backdrop.hidden = !open;
    hideHelp();
  }
  window.workspaceUI = { openInsights: () => setInsights(true), closeInsights: () => setInsights(false) };
  toggle.onclick = () => setInsights(!document.body.classList.contains('insights-open'));
  $('#insights-close').onclick = () => { setInsights(false); toggle.focus(); };
  backdrop.onclick = () => setInsights(false);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('insights-open')) {
      setInsights(false); toggle.focus();
    }
  });
  panel.inert = true;

  const paletteButton = $('#palette-toggle');
  paletteButton.onclick = () => {
    const open = document.body.classList.toggle('palette-open');
    paletteButton.setAttribute('aria-pressed', String(open));
    paletteButton.dataset.help = open ? 'Скрыть инструменты рисования и освободить место на схеме.' :
      'Показать инструменты для добавления событий, задач, шлюзов и участников.';
  };
  function zoom(factor) {
    const canvas = modeler.get('canvas');
    canvas.zoom(Math.max(.2, Math.min(4, canvas.zoom() * factor)));
  }
  $('#zoom-in').onclick = () => zoom(1.25);
  $('#zoom-out').onclick = () => zoom(.8);
  $('.open-button').addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault(); $('#open-file').click();
    }
  });
  $('#inspect-current').addEventListener('click', () => {
    setInsights(true);
    $('.toolbar-more').open = false;
  });
  document.querySelectorAll('.toolbar-menu button').forEach(button => {
    button.addEventListener('click', () => { $('.toolbar-more').open = false; });
  });

  const helpById = {
    'answer-apply': 'Учесть ответы в плане процесса перед построением схемы.',
    'plan-build': 'Построить схему по проверенному плану.',
    'show-source': 'Найти цитату выбранного шага в исходном описании.',
    'detail-save': 'Сохранить срок, документы и основание шага внутри BPMN-файла.',
  };
  const paletteHelp = {
    'Activate hand tool': 'Передвигать рабочее поле.',
    'Activate lasso tool': 'Выделить несколько элементов рамкой.',
    'Activate create/remove space tool': 'Добавить или убрать свободное место на схеме.',
    'Activate global connect tool': 'Соединить элементы потоком.',
    'Create start event': 'Добавить событие начала процесса.',
    'Create intermediate/boundary event': 'Добавить промежуточное событие.',
    'Create end event': 'Добавить событие завершения процесса.',
    'Create gateway': 'Добавить развилку или объединение ветвей.',
    'Create task': 'Добавить действие участника.',
    'Create expanded sub-process': 'Добавить вложенный процесс.',
    'Create data object reference': 'Добавить документ или данные.',
    'Create data store reference': 'Добавить хранилище данных.',
    'Create pool/participant': 'Добавить участника или организацию.',
    'Create group': 'Визуально сгруппировать элементы.',
    'Append end event': 'Добавить завершение после выбранного шага.',
    'Append gateway': 'Добавить развилку после выбранного шага.',
    'Append task': 'Добавить следующее действие.',
    'Append intermediate/boundary event': 'Добавить событие после выбранного шага.',
    'Add text annotation': 'Прикрепить пояснение к элементу.',
    'Change element': 'Поменять тип выбранного элемента.',
    'Delete': 'Удалить выбранный элемент.',
    'Connect to other element': 'Провести связь к другому элементу.',
    'Trigger Event': 'Запустить проигрывание с этого события.',
  };
  function helpFor(element) {
    if (element.dataset.help) return element.dataset.help;
    if (helpById[element.id]) return helpById[element.id];
    if (element.matches('.source-fragment')) return 'Показать на схеме: ' + (element.dataset.titleCache || element.getAttribute('title') || 'связанный шаг');
    if (element.hasAttribute('data-check')) return 'Подсветить на схеме элементы, связанные с этим риском.';
    if (element.hasAttribute('data-discuss')) return 'Подставить вопрос в поле правки схемы.';
    if (element.hasAttribute('data-card')) return 'Выбрать этот шаг и открыть его карточку.';
    if (element.hasAttribute('data-unlinked')) return 'Связать этот фрагмент текста с выбранным шагом схемы.';
    if (element.hasAttribute('data-node')) return 'Найти на схеме шаг без цитаты.';
    if (element.hasAttribute('data-version')) return 'Восстановить эту версию схемы.';
    const label = element.getAttribute('aria-label') || element.getAttribute('title');
    if (label && paletteHelp[label]) return paletteHelp[label];
    if (label?.startsWith('Open ')) return 'Открыть содержимое подпроцесса.';
    if (label?.startsWith('Set animation speed')) return 'Изменить скорость проигрывания маршрута.';
    if (element.hasAttribute('title')) return element.getAttribute('title');
    if (element.dataset.titleCache) return element.dataset.titleCache;
    return null;
  }
  function targetFrom(node) {
    return node instanceof Element ? node.closest('button, [role="button"], summary, label.btn, .djs-palette .entry, .djs-context-pad .entry') : null;
  }
  function showHelp(element) {
    if (!element || element.disabled || element.closest('[inert]')) return;
    const message = helpFor(element);
    if (!message) return;
    hideHelp();
    tooltipTarget = element;
    if (element.hasAttribute('title')) {
      element.dataset.titleCache = element.getAttribute('title');
      element.removeAttribute('title');
    }
    popover.textContent = message;
    popover.hidden = false;
    element.setAttribute('aria-describedby', 'help-popover');
    const rect = element.getBoundingClientRect();
    const width = popover.offsetWidth, height = popover.offsetHeight;
    const left = Math.max(10, Math.min(innerWidth - width - 10, rect.left + rect.width / 2 - width / 2));
    const below = rect.bottom + 9 + height <= innerHeight - 10;
    popover.style.left = left + 'px';
    popover.style.top = (below ? rect.bottom + 9 : Math.max(10, rect.top - height - 9)) + 'px';
  }
  function hideHelp() {
    if (tooltipTarget?.getAttribute('aria-describedby') === 'help-popover') tooltipTarget.removeAttribute('aria-describedby');
    tooltipTarget = null;
    popover.hidden = true;
  }
  document.addEventListener('mouseover', e => {
    const target = targetFrom(e.target);
    if (target && target !== tooltipTarget) showHelp(target);
  });
  document.addEventListener('mouseout', e => {
    if (tooltipTarget && !tooltipTarget.contains(e.relatedTarget)) hideHelp();
  });
  document.addEventListener('focusin', e => showHelp(targetFrom(e.target)));
  document.addEventListener('focusout', e => {
    if (tooltipTarget === targetFrom(e.target)) hideHelp();
  });
  document.addEventListener('click', hideHelp, true);
  document.addEventListener('scroll', hideHelp, true);
  window.addEventListener('resize', hideHelp);
  let compactLayout = matchMedia('(max-width: 760px)').matches;
  window.addEventListener('resize', () => {
    const canvas = modeler.get('canvas');
    canvas.resized();
    const nextLayout = matchMedia('(max-width: 760px)').matches;
    if (state.hasDiagram && nextLayout !== compactLayout) canvas.zoom('fit-viewport', 'auto');
    compactLayout = nextLayout;
  });
})();
