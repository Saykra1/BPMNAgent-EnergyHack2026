/* Facts the description does not contain: report cards and the «Проверка фактов» diagram mode. */
(() => {
  const MARKERS = ['trust-grounded', 'trust-assumption', 'trust-unbased'];
  const button = $('#trust-toggle');
  const legend = $('#trust-legend');
  let inspection = null, active = false;

  function clearMarkers() {
    const canvas = modeler.get('canvas');
    modeler.get('elementRegistry').getAll().forEach(el => MARKERS.forEach(m => canvas.removeMarker(el, m)));
  }
  function paint() {
    clearMarkers();
    legend.hidden = !active;
    if (!active) return;
    if (!inspection || !state.text.trim()) {
      legend.innerHTML = '<span>Проверка фактов работает для схем, построенных по описанию.</span>' + closeButton();
      bindLegend();
      return;
    }
    const registry = modeler.get('elementRegistry'), canvas = modeler.get('canvas');
    const total = { grounded: 0, assumption: 0, unbased: 0 };
    for (const card of inspection.cards) {
      const element = registry.get(card.id);
      if (!element || !(card.trust in total)) continue;
      canvas.addMarker(element, 'trust-' + card.trust);
      total[card.trust]++;
    }
    for (const fact of inspection.facts.filter(f => !f.acknowledged && f.field === 'condition')) {
      const flow = registry.get(fact.id);
      if (flow) canvas.addMarker(flow, 'trust-unbased');
    }
    legend.innerHTML = `<span class="trust-key grounded">Подтверждено текстом <b>${total.grounded}</b></span>
      <span class="trust-key assumption">Допущение <b>${total.assumption}</b></span>
      <button class="trust-key unbased" type="button" data-help="Открыть список фактов, которых нет в описании.">Нет в описании <b>${total.unbased}</b></button>` + closeButton();
    bindLegend();
  }
  const closeButton = () => '<button class="icon-button trust-close" type="button" aria-label="Выключить проверку фактов" data-help="Вернуть обычный вид схемы.">×</button>';
  function bindLegend() {
    legend.querySelector('.trust-close').onclick = () => setActive(false);
    const unbased = legend.querySelector('.trust-key.unbased');
    if (unbased) unbased.onclick = () => { window.workspaceUI?.openInsights(); $('.tabs [data-tab="report"]').click(); $('#facts-section')?.scrollIntoView({ block: 'start' }); };
  }
  function setActive(on) {
    active = on;
    button.setAttribute('aria-pressed', String(on));
    paint();
  }

  function focus(id) {
    const element = modeler.get('elementRegistry').get(id);
    if (!element) return;
    highlight([id]);
    modeler.get('canvas').scrollToElement(element, 120);
    window.workspaceUI?.closeInsights();
  }
  function update(fact, action) {
    const element = modeler.get('elementRegistry').get(fact.owner);
    const details = element && window.agentFeatures.details.read(element);
    if (!details) return;
    if (action === 'drop' && fact.field === 'deadline') details.deadline = '';
    if (action === 'drop' && fact.field === 'document') details.documents = (details.documents || []).filter(d => d !== fact.value);
    if (action === 'keep') details.assumption = `${details.assumption || ''} Не из описания: «${fact.value}».`.trim();
    window.agentFeatures.details.write(element, details);   // the change re-runs the inspection
  }
  function renderReport() {
    if (!inspection || !state.text.trim()) return;
    const open = inspection.facts.filter(f => !f.acknowledged);
    const accepted = inspection.facts.filter(f => f.acknowledged);
    const card = (f, i) => `<article class="fact-card"><div class="fact-head"><span class="pill err">${esc(f.field_title)}</span><b>${esc(f.name || 'Без названия')}</b></div>
      <p>«${esc(f.value)}»</p><p class="hint">${esc(f.reason)}</p>
      <div class="row"><button data-fact-show="${i}" type="button">Показать</button>${f.editable ? `<button data-fact-drop="${i}" type="button" data-help="Удалить это значение из карточки шага.">Убрать</button><button data-fact-keep="${i}" type="button" data-help="Оставить значение и записать его в допущения шага.">Оставить как допущение</button>` : ''}</div></article>`;
    const html = `<section id="facts-section" class="facts-section"><h4>Факты не из описания</h4>
      ${open.length ? `<p class="hint">Сроки, числа, нормативы и документы, которых нет в исходном тексте. Модель могла взять их из своих знаний: проверьте каждый.</p>${open.map(card).join('')}`
        : '<p class="facts-ok">Все сроки, числа, нормативы и документы на схеме найдены в описании.</p>'}
      ${accepted.length ? `<details><summary>Принятые допущения (${accepted.length})</summary><ul class="list">${accepted.map(f => `<li>${esc(f.name)}: «${esc(f.value)}»</li>`).join('')}</ul></details>` : ''}
    </section>`;
    $('#tab-report').insertAdjacentHTML('afterbegin', html);
    const section = $('#facts-section');
    section.querySelectorAll('[data-fact-show]').forEach(b => b.onclick = () => focus(open[+b.dataset.factShow].id));
    section.querySelectorAll('[data-fact-drop]').forEach(b => b.onclick = () => update(open[+b.dataset.factDrop], 'drop'));
    section.querySelectorAll('[data-fact-keep]').forEach(b => b.onclick = () => update(open[+b.dataset.factKeep], 'keep'));
  }

  button.onclick = () => setActive(!active);
  window.factCheck = {
    onInspect(res) { inspection = res; renderReport(); paint(); },
    beforeImport() { inspection = null; clearMarkers(); },
  };
})();
