/* Evidence-first review of missing requirements and BPMN branches. */
(() => {
  const registry = modeler.get('elementRegistry');
  const canvas = modeler.get('canvas');
  const stopWords = new Set(['после', 'перед', 'через', 'который', 'которые', 'затем', 'этого', 'если', 'когда', 'также', 'стороне']);
  let gaps = [], branches = [], scores = null;

  function tokens(value) {
    return new Set((value.toLowerCase().match(/[а-яёa-z0-9]{4,}/g) || []).filter(word => !stopWords.has(word)));
  }
  function category(value) {
    if (/если|когда|при условии|в случае/i.test(value)) return 'Условие';
    if (/срок|\d+\s*(?:рабоч|календар|дн|час)|ожидан/i.test(value)) return 'Срок';
    if (/отказ|авари|недостат|не хватает|замечан|просроч/i.test(value)) return 'Исключение';
    return 'Действие';
  }
  function material(value) {
    return /если|когда|срок|\d+\s*(?:дн|час)|отказ|авари|не хватает|недостат|замечан|нужно|должен|необходимо|[а-яё]{4,}(?:ет|ют|ит|ат|яют|ётся|ется|ится|ать|ять|ить|еть)(?=$|[^а-яё])/i.test(value);
  }
  function coveredLength(start, end, ranges) {
    const intersections = ranges.map(range => [Math.max(start, range.start), Math.min(end, range.end)])
      .filter(([a, b]) => b > a).sort((a, b) => a[0] - b[0]);
    let total = 0, right = start;
    for (const [a, b] of intersections) {
      total += Math.max(0, b - Math.max(a, right));
      right = Math.max(right, b);
    }
    return total;
  }
  function closest(fragment, cards) {
    const source = tokens(fragment);
    return cards.map(card => {
      const target = tokens([card.name, card.source_quote || ''].join(' '));
      const common = [...source].filter(word => target.has(word)).length;
      return { name: card.name, score: common / Math.max(1, source.size) };
    }).filter(item => item.name && item.score > 0).sort((a, b) => b.score - a.score)
      .slice(0, 3).map(item => item.name);
  }
  function findGaps(text, ranges, cards) {
    return [...text.matchAll(/[^.!?;\n]+[.!?;]?/g)].map(match => {
      const raw = match[0], trimmed = raw.trim();
      const leading = raw.indexOf(trimmed);
      const start = match.index + Math.max(0, leading), end = start + trimmed.length;
      return { id: `gap_${start}`, start, end, fragment: trimmed,
        coverage: coveredLength(start, end, ranges) / Math.max(1, trimmed.length),
        category: category(trimmed), candidates: closest(trimmed, cards) };
    }).filter(item => item.fragment.length >= 24 && material(item.fragment) && item.coverage < .35)
      .slice(0, 12);
  }
  function sourceExcerpt(quote, text, gatewayName) {
    let position = quote ? text.indexOf(quote) : -1;
    if (position < 0 && gatewayName) {
      const word = [...tokens(gatewayName)][0];
      if (word) position = text.toLowerCase().indexOf(word);
    }
    if (position < 0) return '';
    return text.slice(Math.max(0, position - 120), Math.min(text.length, position + Math.max(quote.length, 1) + 180));
  }
  function stepName(element) {
    if (!element) return 'Следующий шаг';
    if (element.businessObject?.name) return element.businessObject.name;
    return ({'bpmn:ExclusiveGateway': 'Развилка', 'bpmn:InclusiveGateway': 'Развилка',
      'bpmn:ParallelGateway': 'Параллельные ветви', 'bpmn:EndEvent': 'Конец процесса',
      'bpmn:StartEvent': 'Начало процесса'})[element.type] || 'Следующий шаг';
  }
  function findBranches(text, cards) {
    const byId = new Map(cards.map(card => [card.id, card]));
    return registry.getAll().filter(element => element.type !== 'label' && element.businessObject?.$instanceOf('bpmn:Gateway'))
      .map(gateway => {
        const flows = (gateway.outgoing || []).filter(flow => flow.type === 'bpmn:SequenceFlow');
        if (flows.length < 2) return null;
        const card = byId.get(gateway.id);
        const quote = card?.source_quote || '';
        const excerpt = sourceExcerpt(quote, text, gateway.businessObject.name || '');
        const needsConditions = gateway.type === 'bpmn:ExclusiveGateway' || gateway.type === 'bpmn:InclusiveGateway';
        const outgoing = flows.map(flow => ({ id: flow.id,
          condition: flow.businessObject.name || '',
          destination: stepName(flow.target),
          targetId: flow.target?.id || '' }));
        const names = outgoing.map(flow => flow.condition.trim().toLowerCase()).filter(Boolean);
        return { id: gateway.id, gateway: gateway.businessObject.name ||
          (gateway.type === 'bpmn:ParallelGateway' ? 'Параллельные ветви' : 'Развилка без названия'),
          parallel: gateway.type === 'bpmn:ParallelGateway', quote,
          excerpt, outgoing, missingLabels: needsConditions && outgoing.some(flow => !flow.condition),
          duplicateLabels: needsConditions && new Set(names).size !== names.length,
          sourceFound: Boolean(quote && text.includes(quote)) };
      }).filter(Boolean);
  }
  function scoreFor(id, kind) {
    const item = scores?.[kind]?.find(result => result.id === id);
    return item ? `<span class="audit-score ${item.support < .6 ? 'low' : ''}" title="Вероятностная оценка Jev, проверьте вручную">Jev: ${Math.round(item.support * 100)}%</span>` : '';
  }
  function focusNode(id, flowId) {
    registry.getAll().forEach(element => {
      canvas.removeMarker(element, 'audit-node');
      canvas.removeMarker(element, 'audit-route');
    });
    const element = registry.get(id);
    if (!element) return;
    canvas.addMarker(element, 'audit-node');
    if (flowId && registry.get(flowId)) canvas.addMarker(registry.get(flowId), 'audit-route');
    modeler.get('selection').select(element);
    if (canvas.zoom() < .85) canvas.zoom(.85);
    canvas.scrollToElement(element, 100);
    if (matchMedia('(max-width: 760px)').matches) $('#center').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function render({ report, ranges, text }) {
    const mount = $('#process-audit');
    if (!mount || !report) return;
    scores = null;
    const cards = report.cards || [];
    gaps = findGaps(text, ranges, cards);
    branches = findBranches(text, cards);
    const missingQuotes = cards.filter(card => card.name && card.kind?.toLowerCase().includes('task') && !card.source_found).length;
    const riskyBranches = branches.filter(branch => branch.missingLabels || branch.duplicateLabels || !branch.sourceFound).length;
    mount.innerHTML = `<div class="audit-intro"><div><span class="eyebrow">АУДИТ ПРОЦЕССА</span><h3>Что могло потеряться?</h3><p>Сравниваем требования с шагами и развилками схемы. Отсутствие цитаты — повод проверить, а не доказательство ошибки.</p></div>
      <div class="audit-count"><b>${gaps.length + riskyBranches + missingQuotes}</b><span>мест для проверки</span></div></div>
      <div class="audit-switch" role="group" aria-label="Раздел аудита"><button data-audit-view="gaps" class="active">Текст <b>${gaps.length}</b></button><button data-audit-view="branches">Развилки <b>${branches.length}</b></button></div>
      <div id="audit-gaps" class="audit-view">${gaps.length ? gaps.map(gap => `<article class="audit-card"><div class="audit-card-head"><span class="audit-kind">${esc(gap.category)}</span>${scoreFor(gap.id, 'gaps')}</div><p>${esc(gap.fragment)}</p><small>Прямой привязки нет${gap.candidates.length ? ` · похожие шаги: ${esc(gap.candidates.join(', '))}` : ''}</small><button data-gap="${gap.id}">Показать в тексте ↗</button></article>`).join('') : '<div class="audit-empty">В исходном описании не найдено значимых фрагментов без прямой привязки.</div>'}
        ${missingQuotes ? `<p class="hint">Ещё ${missingQuotes} шаг(а) без точной цитаты — откройте «Точные цитаты и ручная привязка» ниже.</p>` : ''}</div>
      <div id="audit-branches" class="audit-view" hidden>${branches.length ? branches.map(branch => `<article class="audit-card branch-card"><div class="audit-card-head"><strong>${esc(branch.gateway)}</strong><span class="pill ${branch.missingLabels || branch.duplicateLabels || !branch.sourceFound ? 'warn' : 'ok'}">${branch.missingLabels ? 'Есть ветвь без подписи' : branch.duplicateLabels ? 'Повторяются условия' : branch.sourceFound ? 'Есть основание в тексте' : 'Нет точной цитаты'}</span></div>
        ${branch.quote ? `<blockquote>${esc(branch.quote)}</blockquote>` : '<p class="hint">Основание для этой развилки не привязано к описанию.</p>'}
        ${branch.parallel ? '<p class="hint">Параллельный шлюз запускает все ветви одновременно; подписи на потоках необязательны.</p>' : ''}
        <div class="branch-routes">${branch.outgoing.map(flow => `<button data-route="${esc(flow.id)}" data-target="${esc(flow.targetId)}"><span>${esc(flow.condition || 'Без подписи')}</span><span aria-hidden="true">→</span><b>${esc(flow.destination)}</b>${scoreFor(flow.id, 'branches')}</button>`).join('')}</div>
        <button class="audit-locate" data-gateway="${esc(branch.id)}">Показать развилку на схеме ↗</button></article>`).join('') : '<div class="audit-empty">В схеме нет развилок с несколькими исходящими ветвями.</div>'}</div>
      <div class="audit-jev"><button id="jev-audit" ${!gaps.length && !branches.length ? 'disabled' : ''} data-help="Jev оценит, покрыты ли спорные требования шагами и подтверждаются ли ветви исходным текстом.">Смысловая проверка Jev <span aria-hidden="true">↗</span></button><p class="hint">Необязательная проверка. Низкий процент — повод посмотреть вручную; схема не меняется.</p><p id="jev-audit-result" role="status"></p></div>`;
    mount.querySelectorAll('[data-audit-view]').forEach(button => button.onclick = () => {
      mount.querySelectorAll('[data-audit-view]').forEach(item => item.classList.toggle('active', item === button));
      $('#audit-gaps').hidden = button.dataset.auditView !== 'gaps';
      $('#audit-branches').hidden = button.dataset.auditView !== 'branches';
    });
    mount.querySelectorAll('[data-gap]').forEach(button => button.onclick = () => {
      const gap = gaps.find(item => item.id === button.dataset.gap);
      if (gap) window.sourceReview?.revealRange(gap.start, gap.end);
      window.workspaceUI?.closeInsights();
    });
    mount.querySelectorAll('[data-gateway]').forEach(button => button.onclick = () => {
      focusNode(button.dataset.gateway);
      window.workspaceUI?.closeInsights();
    });
    mount.querySelectorAll('[data-route]').forEach(button => button.onclick = () => {
      focusNode(button.dataset.target, button.dataset.route);
      window.workspaceUI?.closeInsights();
    });
    $('#jev-audit').onclick = async () => {
      const button = $('#jev-audit'), output = $('#jev-audit-result');
      button.disabled = true; output.textContent = 'Jev оценивает спорные требования и ветви…';
      try {
        const result = await api('/api/jev-audit', {
          gaps: gaps.map(({ id, fragment, candidates }) => ({ id, fragment, candidates })),
          branches: branches.flatMap(branch => branch.outgoing.map(flow => ({
            id: flow.id, excerpt: branch.excerpt, gateway: branch.gateway,
            condition: flow.condition, destination: flow.destination,
          }))).slice(0, 12),
        });
        scores = result;
        mount.querySelectorAll('.audit-score').forEach(node => node.remove());
        mount.querySelectorAll('[data-gap]').forEach(node => {
          const score = result.gaps.find(item => item.id === node.dataset.gap);
          if (score) node.parentElement.querySelector('.audit-card-head').insertAdjacentHTML('beforeend', scoreFor(score.id, 'gaps'));
        });
        mount.querySelectorAll('[data-route]').forEach(node => {
          const score = result.branches.find(item => item.id === node.dataset.route);
          if (score) node.insertAdjacentHTML('beforeend', scoreFor(score.id, 'branches'));
        });
        const currentView = mount.querySelector('[data-audit-view].active')?.dataset.auditView;
        const targetView = currentView === 'gaps' && result.gaps.length ? 'gaps' :
          currentView === 'branches' && result.branches.length ? 'branches' :
          result.branches.length ? 'branches' : 'gaps';
        mount.querySelector(`[data-audit-view="${targetView}"]`)?.click();
        output.textContent = `Jev проверил ${result.gaps.length} фрагментов текста и ${result.branches.length} ветвей. ` +
          (targetView === 'branches' ? 'Проценты видны у каждой проверенной ветви во вкладке «Развилки».' :
            'Проценты видны у проверенных фрагментов во вкладке «Текст».');
        mount.querySelector(`#audit-${targetView} .audit-score`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } catch (error) { output.textContent = 'Jev недоступен: ' + error.message; }
      finally { if (button.isConnected) button.disabled = false; }
    };
  }
  window.processAudit = { render };
})();
