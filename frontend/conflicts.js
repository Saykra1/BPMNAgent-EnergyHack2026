/* Contradiction gate: the canvas does not start until the analyst says which rule is senior. */
(() => {
  const gate = $('#conflict-gate');
  let queue = [], decided = [], index = 0, done = null;

  function clear() {
    queue = []; decided = []; index = 0; done = null;
    gate.hidden = true;
    gate.replaceChildren();
  }
  function showInText(quote) {
    const input = $('#text');
    const start = input.value.indexOf(quote);
    if (start < 0) return;
    // With a diagram on screen the description is shown as a read-only reader; switch to the editor.
    if (input.hidden && !$('#edit-source').hidden) $('#edit-source').click();
    input.focus();
    input.setSelectionRange(start, start + quote.length);
    if (matchMedia('(max-width: 760px)').matches) input.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function card(key, label, quote) {
    return `<article class="conflict-card">
      <span class="conflict-tag">${label}</span>
      <blockquote>«${esc(quote)}»</blockquote>
      <button class="quiet" type="button" data-show="${key}" data-help="Выделить эту фразу в описании процесса.">Показать в тексте</button>
      <button class="primary" type="button" data-pick="${key}">Это правило старшее</button>
    </article>`;
  }
  function render() {
    const c = queue[index];
    gate.hidden = false;
    gate.innerHTML = `<div class="conflict-inner" role="group" aria-labelledby="conflict-question">
      <span class="eyebrow">ОПИСАНИЕ ПРОТИВОРЕЧИТ САМО СЕБЕ${queue.length > 1 ? ` · ${index + 1} ИЗ ${queue.length}` : ''}</span>
      <h2 id="conflict-question">Какое правило старшее?</h2>
      <p class="conflict-lead">${c.topic ? `<b>${esc(c.topic)}.</b> ` : ''}Эти правила нельзя выполнить одновременно. Помощник не будет угадывать: схема строится после вашего решения.</p>
      <div class="conflict-cards">${card('a', 'Правило А', c.rule_a)}<span class="conflict-or" aria-hidden="true">или</span>${card('b', 'Правило Б', c.rule_b)}</div>
      <button class="quiet conflict-both" type="button" data-pick="both" data-help="Если правила относятся к разным случаям, схема учтёт оба без сноски.">Это не противоречие — учесть оба правила</button>
      <p class="hint">Выбор останется сноской на схеме, чтобы правку текста не потеряли.</p>
    </div>`;
    gate.querySelectorAll('[data-show]').forEach(b => b.onclick = () => showInText(b.dataset.show === 'a' ? c.rule_a : c.rule_b));
    gate.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => pick(b.dataset.pick));
    gate.querySelector('[data-pick="a"]').focus({ preventScroll: true });
  }
  function pick(chosen) {
    const c = queue[index];
    decided.push({ topic: c.topic || '', rule_a: c.rule_a, rule_b: c.rule_b, chosen });
    if (++index < queue.length) { render(); return; }
    const callback = done, result = decided;
    clear();
    callback?.(result);
  }

  window.conflictGate = {
    // conflicts: [{topic, rule_a, rule_b}] -> onResolved([{topic, rule_a, rule_b, chosen: 'a'|'b'|'both'}])
    show(conflicts, onResolved) {
      clear();
      queue = conflicts; done = onResolved;
      render();
    },
    clear,
    get open() { return !gate.hidden; },
  };
  $('#text').addEventListener('input', () => { if (!gate.hidden) clear(); });
  $('#example').addEventListener('change', clear);
})();
