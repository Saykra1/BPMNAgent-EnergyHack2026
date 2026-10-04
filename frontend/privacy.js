/* Personal data protection: what the model will see, analyst overrides and the run summary. */
(() => {
  // Same set as backend/app/privacy.py: zero-width, bidi overrides, variation selectors, Unicode tags.
  const INVISIBLE = /[­᠎​-‏‪-‮⁠-⁤⁦-⁩︀-️﻿\u{E0000}-\u{E007F}]/gu;
  const panel = $('#privacy-panel');
  let data = null, requestId = 0, timer = null, notice = '';

  const options = () => ({ hide: state.privacy.hide, show: state.privacy.show, trusted: state.privacy.trusted });
  function clean(text) { return text.replace(INVISIBLE, ''); }
  function counts(findings) {
    const by = new Map();
    for (const f of findings) {
      const key = f.title;
      if (!by.has(key)) by.set(key, new Set());
      by.get(key).add(f.placeholder);
    }
    return [...by].map(([title, set]) => ({ title, count: set.size }));
  }
  const listCounts = (items) => items.map(i => i.count > 1 ? `${i.title} ×${i.count}` : i.title).join(', ');

  function renderStrip() {
    const strip = $('#privacy-strip'), alert = $('#privacy-alert');
    const text = $('#text').value;
    strip.hidden = !text.trim() || !data;
    alert.hidden = true;
    if (strip.hidden) return;
    const hidden = data.findings.filter(f => f.kind !== 'injection');
    const items = counts(hidden);
    $('#privacy-summary').innerHTML = items.length
      ? `<b>Модель не увидит:</b> ${esc(listCounts(items))}`
      : 'Персональные данные не найдены';
    strip.classList.toggle('found', items.length > 0);
    $('#privacy-engine').hidden = data.ner;
    const warnings = [];
    if (data.invisible.removed) {
      warnings.push(`В тексте ${data.invisible.removed} невидимых символов — они будут удалены перед отправкой.` +
        (data.invisible.hidden_message ? ` Скрытое сообщение: «${esc(data.invisible.hidden_message.replace(/[\s.]+$/, ''))}».` : ''));
    }
    const commands = data.findings.filter(f => f.kind === 'injection');
    if (commands.length) warnings.push(`Найдена команда для ИИ (${commands.length}) — этот фрагмент не попадёт в модель.`);
    if (warnings.length) {
      alert.hidden = false;
      alert.innerHTML = warnings.map(w => `<span>${w}</span>`).join('') +
        `<button id="privacy-review" class="quiet" type="button">Посмотреть</button>`;
      $('#privacy-review').onclick = open;
    }
  }

  function marked(text, findings, released) {
    const spans = [...findings.map(f => ({ ...f, state: 'hidden' })), ...released.map(f => ({ ...f, state: 'released' }))]
      .sort((a, b) => a.start - b.start);
    let html = '', position = 0;
    spans.forEach((span, index) => {
      if (span.start < position) return;
      html += esc(text.slice(position, span.start));
      const help = span.state === 'released' ? 'Отправляется как есть. Нажмите, чтобы снова скрыть.'
        : span.kind === 'analyst' ? 'Скрыто вручную. Нажмите, чтобы отменить.'
        : span.releasable ? `${span.title}: нажмите, чтобы отправить без маскировки.`
        : `${span.title}: такие данные скрываются всегда.`;
      html += `<mark class="pii pii-${span.state} pii-${esc(span.kind)}" data-span="${index}" tabindex="0" data-help="${esc(help)}">${esc(text.slice(span.start, span.end))}</mark>`;
      position = span.end;
    });
    return { html: html + esc(text.slice(position)), spans };
  }

  function toggle(span) {
    const value = span.text.trim();
    if (span.state === 'released') state.privacy.show = state.privacy.show.filter(v => v !== value);
    else if (span.kind === 'analyst') state.privacy.hide = state.privacy.hide.filter(v => v !== value);
    else if (span.releasable) state.privacy.show.push(value);
    else { notice = `${span.title} скрывается всегда: такие данные не отправляются в модель.`; renderPanel(); return; }
    notice = '';
    refresh();
  }

  function renderPanel() {
    if (panel.hidden || !data) return;
    const text = data.clean_text;
    const view = marked(text, data.findings, data.released);
    const items = counts(data.findings);
    const masked = esc(data.masked_text).replace(/\[[A-ZА-ЯЁ_]+_\d+\]/g, label => `<span class="pii-label">${label}</span>`);
    panel.innerHTML = `<div class="privacy-inner" role="dialog" aria-labelledby="privacy-title">
      <div class="privacy-head"><div><span class="eyebrow">ЗАЩИТА ПЕРСОНАЛЬНЫХ ДАННЫХ</span><h2 id="privacy-title">Что увидит модель</h2></div>
        <button id="privacy-close" class="icon-button" type="button" aria-label="Закрыть" data-help="Вернуться к схеме.">×</button></div>
      <p class="privacy-lead">Персональные данные заменяются метками на этом сервере до отправки. Модель работает только с метками, а настоящие значения возвращаются на схему у вас.</p>
      <div class="privacy-pills">${items.length ? items.map(i => `<span class="pill">${esc(i.title)}${i.count > 1 ? ' ×' + i.count : ''}</span>`).join('') : '<span class="pill ok">Ничего не найдено</span>'}
        <span class="pill ${data.ner ? 'ok' : 'warn'}">${data.ner ? 'Имена ищет локальная модель Natasha' : 'Модель поиска имён недоступна: только правила'}</span></div>
      <div class="privacy-columns">
        <section><h3>Ваш текст</h3><div id="privacy-source" class="privacy-text">${view.html}</div>
          <div class="privacy-actions"><button id="privacy-hide" type="button" data-help="Выделите мышью фрагмент в левой колонке, чтобы скрыть его от модели.">Скрыть выделенное</button>
          <span class="hint">Нажмите на подсветку, чтобы отправить фрагмент как есть.</span></div></section>
        <section><h3>Уйдёт в модель</h3><div class="privacy-text privacy-masked">${masked}</div></section>
      </div>
      <p id="privacy-note" class="hint" role="status">${esc(notice)}</p>
      <div class="privacy-footer"><span class="hint">Телефоны, документы, счета и другие идентификаторы скрываются всегда. Перед каждым запросом сервер проверяет его ещё раз.</span>
        <button id="privacy-done" class="primary" type="button">Готово</button></div>
    </div>`;
    $('#privacy-close').onclick = close;
    $('#privacy-done').onclick = close;
    panel.querySelectorAll('[data-span]').forEach(mark => {
      const activate = () => toggle(view.spans[+mark.dataset.span]);
      mark.onclick = activate;
      mark.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } };
    });
    $('#privacy-hide').onclick = () => {
      const selection = window.getSelection();
      const value = selection && $('#privacy-source').contains(selection.anchorNode) ? selection.toString().trim() : '';
      if (value.length < 2) { notice = 'Сначала выделите фрагмент в колонке «Ваш текст».'; renderPanel(); return; }
      state.privacy.hide.push(value);
      state.privacy.show = state.privacy.show.filter(v => !value.includes(v) && !v.includes(value));
      notice = '';
      refresh();
    };
  }

  async function refresh() {
    clearTimeout(timer);
    const text = $('#text').value;
    const id = ++requestId;
    if (!text.trim()) { data = null; renderStrip(); return; }
    try {
      const result = await api('/api/privacy', { text: text.slice(0, 30000), privacy: options() });
      if (id !== requestId) return;
      data = result;
      renderStrip();
      renderPanel();
    } catch { if (id === requestId) { data = null; renderStrip(); } }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 450); }
  async function open() {
    panel.hidden = false;
    if (!data) await refresh();
    renderPanel();
    $('#privacy-done')?.focus({ preventScroll: true });
  }
  function close() { panel.hidden = true; panel.replaceChildren(); $('#privacy-open').focus({ preventScroll: true }); }

  // Summary of the last generation: shown in «Анализ → Обзор».
  function renderReport() {
    const p = state.lastResult?.privacy;
    if (!p || !p.requests) return;
    const hidden = p.hidden.map(h => h.count > 1 ? `${h.title} ×${h.count}` : h.title).join(', ');
    $('#tab-report').insertAdjacentHTML('beforeend', `<section class="privacy-report"><h4>Конфиденциальность</h4>
      <p>Запросов к модели: <b>${p.requests}</b>. Каждый проверен перед отправкой${p.egress_caught ? `, на последнем рубеже скрыто ещё ${p.egress_caught}` : ''}.</p>
      <p>${hidden ? `Скрыто от модели: ${esc(hidden)}.` : 'Персональных данных в описании не найдено.'}${p.injections ? ` Команды для ИИ в тексте: ${p.injections} — не отправлены.` : ''}</p>
      <button id="privacy-report-open" type="button">Что увидела модель</button></section>`);
    $('#privacy-report-open').onclick = () => { window.workspaceUI?.closeInsights(); open(); };
  }

  $('#text').addEventListener('input', schedule);
  $('#privacy-open').onclick = open;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden) close(); });
  window.privacyUI = { refresh, clean, options, renderReport, open };
})();
