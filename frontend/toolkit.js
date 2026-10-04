/* Analyst toolkit: linter + autofix, regulation, analytics (heat map, simulation), RACI, test paths,
   AI journal, exports, industry templates, document import, PII preview, diff highlighting. */
(() => {
  const SECTIONS = [
    ['lint', 'Линтер'], ['sop', 'Регламент'], ['analytics', 'Аналитика'], ['raci', 'RACI'],
    ['paths', 'Тест-пути'], ['journal', 'Журнал ИИ'], ['export', 'Экспорт'],
  ];
  let section = 'lint';
  let asIs = null;              // saved "as is" diagram for comparison
  let overlayIds = [];

  const fmt = (min) => {
    if (min == null || isNaN(min)) return '—';
    if (min < 60) return `${Math.round(min)} мин`;
    if (min < 60 * 24) return `${(min / 60).toFixed(1).replace('.0', '')} ч`;
    return `${(min / 60 / 24).toFixed(1).replace('.0', '')} сут`;
  };
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;

  async function currentXml() {
    if (!state.hasDiagram) throw new Error('Сначала постройте или откройте диаграмму');
    return (await modeler.saveXML({ format: true })).xml;
  }
  async function post(path, body) { return api(path, body); }
  async function downloadPost(path, body, fallback) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    download(m ? decodeURIComponent(m[1]) : fallback, await r.blob());
    return r;
  }

  // ------------------------------------------------------------------ markers / overlays
  function clearMarks() {
    const canvas = modeler.get('canvas'), reg = modeler.get('elementRegistry'), overlays = modeler.get('overlays');
    reg.getAll().forEach(e => ['heat-0', 'heat-1', 'heat-2', 'heat-3', 'heat-4', 'critical', 'diff-added', 'diff-changed', 'path-step']
      .forEach(c => canvas.removeMarker(e, c)));
    overlayIds.forEach(id => { try { overlays.remove(id); } catch { /* gone */ } });
    overlayIds = [];
  }
  function mark(ids, cls) {
    const canvas = modeler.get('canvas'), reg = modeler.get('elementRegistry');
    ids.forEach(id => { const el = reg.get(id); if (el) canvas.addMarker(el, cls); });
  }
  function badge(id, html, cls = '') {
    const reg = modeler.get('elementRegistry');
    if (!reg.get(id)) return;
    overlayIds.push(modeler.get('overlays').add(id, { position: { top: -12, right: 8 }, html: `<div class="tk-badge ${cls}">${html}</div>` }));
  }
  function focus(id) {
    const el = modeler.get('elementRegistry').get(id);
    if (el) modeler.get('canvas').scrollToElement(el);
  }

  // ------------------------------------------------------------------ shell
  function install() {
    const nav = document.querySelector('.tabs');
    if (!nav || document.querySelector('[data-tab="tools"]')) return;
    const btn = document.createElement('button');
    btn.dataset.tab = 'tools';
    btn.dataset.help = 'Линтер, регламент, аналитика, RACI, тест-пути, журнал ИИ и экспорт.';
    btn.textContent = 'Инструменты';
    btn.onclick = () => {
      document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === btn));
      document.querySelectorAll('.tab').forEach(t => (t.hidden = t.id !== 'tab-tools'));
      render();
    };
    nav.append(btn);
    const tab = document.createElement('div');
    tab.className = 'tab'; tab.id = 'tab-tools'; tab.hidden = true;
    tab.innerHTML = `<div class="tk-nav">${SECTIONS.map(([id, t]) => `<button data-sec="${id}">${t}</button>`).join('')}</div><div id="tk-body"></div>`;
    document.getElementById('tab-history').after(tab);
    tab.querySelectorAll('.tk-nav button').forEach(b => b.onclick = () => { section = b.dataset.sec; render(); });
    installLeft();
  }
  function open(sec) {
    section = sec || section;
    window.workspaceUI?.openInsights();
    document.querySelector('[data-tab="tools"]').click();
  }
  function body(html) { document.getElementById('tk-body').innerHTML = html; }
  function status(msg, cls = '') { const s = document.getElementById('tk-status'); if (s) { s.className = 'tk-status ' + cls; s.textContent = msg; } }
  async function run(label, fn) {
    status(label + '…');
    try { await fn(); } catch (e) { status(e.message, 'err'); }
  }
  function render() {
    document.querySelectorAll('.tk-nav button').forEach(b => b.classList.toggle('active', b.dataset.sec === section));
    ({ lint: renderLint, sop: renderSop, analytics: renderAnalytics, raci: renderRaci, paths: renderPaths,
      journal: renderJournal, export: renderExport })[section]();
  }

  // ------------------------------------------------------------------ linter
  const LEVEL = { error: ['err', 'Ошибка'], warning: ['warn', 'Предупреждение'], info: ['', 'Совет'] };
  function renderLint() {
    body(`<p class="hint">Детерминированная проверка процесса без модели: тупики, недостижимые шаги, развилки без условий
      и ветки «иначе», параллельные ветви без слияния, шаги без исполнителя, дубли.</p>
      <div class="row"><button id="tk-lint" class="primary">Проверить процесс</button><button id="tk-fixall">Исправить всё автоматически</button></div>
      <p id="tk-status" class="tk-status"></p><div id="tk-out"></div>`);
    document.getElementById('tk-lint').onclick = () => run('Проверяю', lintNow);
    document.getElementById('tk-fixall').onclick = () => run('Исправляю', () => fix({}));
    if (state.hasDiagram) run('Проверяю', lintNow);
  }
  async function lintNow() {
    const r = await post('/api/lint', { xml: await currentXml() });
    const s = r.summary;
    status(`Ошибок: ${s.errors} · предупреждений: ${s.warnings} · советов: ${s.info} · исправимо автоматически: ${s.fixable}`, s.errors ? 'err' : 'ok');
    const qs = r.clarifications || [];
    document.getElementById('tk-out').innerHTML = (r.lint.length ? r.lint.map((i, k) => `
      <div class="issue ${LEVEL[i.level][0]}" data-k="${k}"><b>${LEVEL[i.level][1]}</b> · ${esc(i.message)}
      <div class="hint">${esc(i.hint)}</div>${i.fixable ? `<button class="tk-fix" data-code="${esc(i.code)}" data-el="${esc(i.element || '')}">Исправить</button>` : ''}</div>`).join('')
      : '<p class="pill ok">Замечаний нет</p>') +
      (qs.length ? `<h4>Уточняющие вопросы к заказчику</h4><ul class="list">${qs.map(q => `<li>${esc(q.question)}${q.blocking ? ' <span class="pill warn">важно</span>' : ''}</li>`).join('')}</ul>` : '');
    document.querySelectorAll('#tk-out .issue').forEach(el => {
      const i = r.lint[+el.dataset.k];
      el.onclick = (e) => { if (e.target.tagName !== 'BUTTON' && i.element) { highlight([i.element]); focus(i.element); } };
    });
    document.querySelectorAll('.tk-fix').forEach(b => b.onclick = () => run('Исправляю', () => fix({ codes: [b.dataset.code], elements: b.dataset.el ? [b.dataset.el] : null })));
  }
  async function fix(opts) {
    const r = await post('/api/autofix', { xml: await currentXml(), text: state.text, ...opts });
    if (!r.xml) { status(r.message || 'Нечего исправлять', 'ok'); return; }
    await applyResult(r);
    showDiff(r.diff);
    chat((r.fixes || []).join('; ') || 'Исправлено', 'bot');
    section = 'lint'; open('lint');
  }

  // ------------------------------------------------------------------ regulation
  function mdToHtml(md) {
    const lines = md.split('\n'); let html = '', inList = false, inTable = false;
    const close = () => { if (inList) { html += '</ul>'; inList = false; } if (inTable) { html += '</table>'; inTable = false; } };
    for (const ln of lines) {
      if (ln.startsWith('# ')) { close(); html += `<h3>${esc(ln.slice(2))}</h3>`; }
      else if (ln.startsWith('## ')) { close(); html += `<h4>${esc(ln.slice(3))}</h4>`; }
      else if (ln.startsWith('- ')) { if (!inList) { close(); html += '<ul class="list">'; inList = true; } html += `<li>${esc(ln.slice(2))}</li>`; }
      else if (ln.startsWith('|')) {
        if (/^\|(-+\|)+$/.test(ln.replace(/\s/g, ''))) continue;
        if (!inTable) { close(); html += '<table class="tk-table">'; inTable = true; }
        html += '<tr>' + ln.split('|').slice(1, -1).map(c => `<td>${esc(c.trim())}</td>`).join('') + '</tr>';
      } else if (ln.trim()) { close(); html += `<p>${esc(ln)}</p>`; } else close();
    }
    close(); return html;
  }
  function renderSop() {
    body(`<p class="hint">Регламент собирается из схемы детерминированно: цель, участники, шаги, условия, исключения, сроки.</p>
      <div class="row"><button id="tk-sop" class="primary">Сформировать регламент</button><button id="tk-explain">Объяснить простым языком</button></div>
      <div class="row"><button id="tk-md">Скачать .md</button><button id="tk-docx">Скачать .docx</button></div>
      <p id="tk-status" class="tk-status"></p><div id="tk-out" class="tk-doc"></div>`);
    document.getElementById('tk-sop').onclick = () => run('Формирую', async () => {
      const r = await post('/api/sop', { xml: await currentXml() });
      document.getElementById('tk-out').innerHTML = mdToHtml(r.markdown); status('Готово', 'ok');
    });
    document.getElementById('tk-explain').onclick = () => run('Объясняю', async () => {
      const r = await post('/api/explain', { xml: await currentXml(), use_llm: true });
      document.getElementById('tk-out').innerHTML = `<div class="notice">${esc(r.text)}</div><p class="hint">${r.source === 'llm' ? 'Текст подготовлен моделью по схеме.' : 'Текст собран по правилам (модель недоступна).'}</p>`;
      status('Готово', 'ok');
    });
    document.getElementById('tk-md').onclick = () => run('Готовлю файл', async () => { await downloadPost('/api/sop', { xml: await currentXml(), format: 'md_file' }, 'регламент.md'); status('Скачано', 'ok'); });
    document.getElementById('tk-docx').onclick = () => run('Готовлю файл', async () => { await downloadPost('/api/sop', { xml: await currentXml(), format: 'docx' }, 'регламент.docx'); status('Скачано', 'ok'); });
  }

  // ------------------------------------------------------------------ analytics
  function renderAnalytics() {
    body(`<p class="hint">Без реальных логов: длительности из карточек шагов, оценки модели или типовые значения (помечаются).
      Тепловая карта красит шаги от «холодных» к «горячим».</p>
      <div class="row"><select id="tk-metric"><option value="total">Время (работа + ожидание)</option><option value="work">Только работа</option><option value="wait">Только ожидание</option></select>
      <button id="tk-an" class="primary">Рассчитать</button></div>
      <div class="row"><button id="tk-est">Оценить длительности моделью</button><button id="tk-rec">Рекомендации</button></div>
      <div class="row"><button id="tk-asis">Запомнить как «как есть»</button><button id="tk-cmp" ${asIs ? '' : 'disabled'}>Сравнить с «как есть»</button></div>
      <p id="tk-status" class="tk-status"></p><div id="tk-out"></div>`);
    document.getElementById('tk-an').onclick = () => run('Считаю', analyzeNow);
    document.getElementById('tk-est').onclick = () => run('Модель оценивает длительности', async () => {
      const r = await post('/api/estimate', { xml: await currentXml(), text: state.text });
      await applyResult(r); open('analytics'); chat(r.summary, 'bot'); await analyzeNow();
    });
    document.getElementById('tk-rec').onclick = () => run('Готовлю рекомендации', async () => {
      const r = await post('/api/recommend', { xml: await currentXml() });
      document.getElementById('tk-out').innerHTML = `<h4>Рекомендации <span class="pill warn">рекомендация, не факт</span></h4>` +
        r.recommendations.map(x => `<div class="issue" data-els="${esc((x.elements || []).join(','))}"><b>${esc(x.title)}</b>
        <div>${esc(x.action || '')}</div><div class="hint">${esc(x.rationale || '')}${x.effect ? ' · ' + esc(x.effect) : ''}</div></div>`).join('') +
        `<p class="hint">${r.source === 'llm' ? 'Сформированы моделью по результатам линтера и симуляции.' : 'Сформированы правилами (модель недоступна).'}</p>`;
      document.querySelectorAll('#tk-out .issue[data-els]').forEach(el => el.onclick = () => { const ids = el.dataset.els.split(',').filter(Boolean); highlight(ids); if (ids[0]) focus(ids[0]); });
      status('Готово', 'ok');
    });
    document.getElementById('tk-asis').onclick = () => run('Сохраняю', async () => {
      asIs = await currentXml(); document.getElementById('tk-cmp').disabled = false; status('Текущая схема сохранена как «как есть». Внесите изменения и сравните.', 'ok');
    });
    document.getElementById('tk-cmp').onclick = () => run('Сравниваю', async () => {
      const r = await post('/api/compare', { as_is: { xml: asIs }, to_be: { xml: await currentXml() } });
      const d = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`;
      document.getElementById('tk-out').innerHTML = `<h4>Как есть → как будет</h4><table class="tk-table">
        <tr><td></td><td>Как есть</td><td>Как будет</td><td>Δ</td></tr>
        <tr><td>Среднее время</td><td>${fmt(r.as_is.mean_min)}</td><td>${fmt(r.to_be.mean_min)}</td><td>${d(r.delta_pct.mean)}</td></tr>
        <tr><td>p90</td><td>${fmt(r.as_is.p90_min)}</td><td>${fmt(r.to_be.p90_min)}</td><td>${d(r.delta_pct.p90)}</td></tr>
        <tr><td>Критический путь</td><td>${fmt(r.as_is.critical_min)}</td><td>${fmt(r.to_be.critical_min)}</td><td>${d(r.delta_pct.critical)}</td></tr>
        <tr><td>Шагов</td><td>${r.as_is.steps}</td><td>${r.to_be.steps}</td><td></td></tr></table>`;
      status('Готово', 'ok');
    });
  }
  async function analyzeNow() {
    const metric = document.getElementById('tk-metric')?.value || 'total';
    const r = await post('/api/analytics', { xml: await currentXml(), metric, runs: 1000 });
    clearMarks();
    Object.entries(r.heat.buckets).forEach(([id, b]) => mark([id], 'heat-' + b));
    Object.entries(r.heat.values).forEach(([id, v]) => badge(id, fmt(v), r.durations[id]?.source === 'given' ? '' : 'est'));
    mark(r.critical_path.path, 'critical');
    const s = r.simulation, maxH = Math.max(...s.histogram.counts, 1);
    const names = Object.fromEntries((r.plan.elements || []).map(e => [e.id, e.name || e.id]));
    document.getElementById('tk-out').innerHTML = `
      <div class="kpis"><div class="kpi"><b>${fmt(s.mean_min)}</b><span>среднее</span></div>
      <div class="kpi"><b>${fmt(s.p90_min)}</b><span>p90</span></div><div class="kpi"><b>${fmt(r.critical_path.total_min)}</b><span>крит. путь</span></div></div>
      ${r.note ? `<div class="notice">${esc(r.note)}</div>` : ''}
      <h4>Распределение времени (Monte Carlo, ${s.runs} прогонов)</h4>
      <div class="tk-hist">${s.histogram.counts.map((c, i) => `<i style="height:${Math.max(2, c / maxH * 60)}px" title="${fmt(s.histogram.from + i * s.histogram.step)}: ${c}"></i>`).join('')}</div>
      <div class="hint">${fmt(s.min_min)} … ${fmt(s.max_min)}</div>
      <h4>Критический путь</h4><p>${r.critical_path.path.map(id => esc(names[id] || id)).join(' → ')}</p>
      <h4>Загрузка участников</h4>${s.utilization.map(u => `<div class="tk-bar"><span>${esc(u.name)}</span><i style="width:${Math.min(100, u.share_of_time * 100)}%"></i><b>${pct(u.share_of_time)}</b></div>`).join('') || '<p class="muted">Нет данных</p>'}
      <h4>Доли ветвей</h4>${s.branches.map(b => `<p><b>${esc(b.name)}</b>: ${b.branches.map(x => `${esc(x.label || x.to_name)} — ${pct(x.share)}`).join(' · ')}</p>`).join('') || '<p class="muted">Развилок нет</p>'}
      <p class="hint">Шаги с пометкой «≈» — длительность оценена (моделью или типовым значением), а не задана аналитиком.</p>`;
    status('Готово. Цвет шагов — тепловая карта, жирная рамка — критический путь.', 'ok');
  }

  // ------------------------------------------------------------------ RACI
  function renderRaci() {
    body(`<p class="hint">R — исполняет, A — отвечает за результат, C — консультирует, I — информируется.
      Значения не из схемы выводятся автоматически и помечаются.</p>
      <div class="row"><button id="tk-raci" class="primary">Построить RACI</button><button id="tk-rcsv">CSV</button><button id="tk-rxls">XLSX</button></div>
      <p id="tk-status" class="tk-status"></p><div id="tk-out" class="tk-scroll"></div>`);
    document.getElementById('tk-raci').onclick = () => run('Строю', async () => {
      const m = await post('/api/raci', { xml: await currentXml() });
      document.getElementById('tk-out').innerHTML = `<table class="tk-table raci"><tr><td>Шаг</td>${m.participants.map(p => `<td>${esc(p.name)}</td>`).join('')}</tr>
        ${m.rows.map(r => `<tr title="${esc(r.inferred.join('; '))}"><td>${esc(r.step)}${r.inferred.length ? ' <span class="muted">*</span>' : ''}</td>${m.participants.map(p => `<td class="raci-${(r.cells[p.id] || '')[0] || ''}">${esc(r.cells[p.id])}</td>`).join('')}</tr>`).join('')}</table>
        <p class="hint">* — часть букв выведена автоматически (наведите на строку).</p>`;
      status('Готово', 'ok');
    });
    document.getElementById('tk-rcsv').onclick = () => run('Готовлю', async () => { await downloadPost('/api/raci', { xml: await currentXml(), format: 'csv' }, 'RACI.csv'); status('Скачано', 'ok'); });
    document.getElementById('tk-rxls').onclick = () => run('Готовлю', async () => { await downloadPost('/api/raci', { xml: await currentXml(), format: 'xlsx' }, 'RACI.xlsx'); status('Скачано', 'ok'); });
  }

  // ------------------------------------------------------------------ test paths
  function renderPaths() {
    body(`<p class="hint">Все сценарии через развилки (циклы — не более одного раза) — для тестирования процесса.</p>
      <div class="row"><button id="tk-paths" class="primary">Перечислить сценарии</button><button id="tk-pcsv">CSV</button><button id="tk-pmd">Markdown</button></div>
      <p id="tk-status" class="tk-status"></p><div id="tk-out"></div>`);
    document.getElementById('tk-paths').onclick = () => run('Перечисляю', async () => {
      const r = await post('/api/paths', { xml: await currentXml(), limit: 60 });
      document.getElementById('tk-out').innerHTML = r.paths.map((p, k) => `<div class="issue" data-k="${k}"><b>${p.id}</b> · ${pct(p.probability)} · ≈${fmt(p.time_min)} · итог: ${esc(p.end)}
        <div class="hint">${esc(p.conditions.join('; ') || 'без развилок')}</div><div class="hint">${p.steps.length} шагов</div></div>`).join('') +
        (r.truncated ? `<p class="hint">Показаны первые ${r.limit} сценариев.</p>` : '');
      document.querySelectorAll('#tk-out .issue').forEach(el => el.onclick = () => {
        clearMarks(); mark(r.paths[+el.dataset.k].step_ids, 'path-step'); if (r.paths[+el.dataset.k].step_ids[0]) focus(r.paths[+el.dataset.k].step_ids[0]);
      });
      status(`Сценариев: ${r.paths.length}. Нажмите сценарий, чтобы подсветить его шаги.`, 'ok');
    });
    document.getElementById('tk-pcsv').onclick = () => run('Готовлю', async () => { await downloadPost('/api/paths', { xml: await currentXml(), format: 'csv', limit: 200 }, 'тест-сценарии.csv'); status('Скачано', 'ok'); });
    document.getElementById('tk-pmd').onclick = () => run('Готовлю', async () => { await downloadPost('/api/paths', { xml: await currentXml(), format: 'md', limit: 200 }, 'тест-сценарии.md'); status('Скачано', 'ok'); });
  }

  // ------------------------------------------------------------------ AI journal
  function renderJournal() {
    body(`<p class="hint">Каждый запрос к модели: модель, версии промптов, токены, время, найденные ошибки, число самоисправлений.
      Секреты в журнал не пишутся, персональные данные маскируются до отправки.</p>
      <button id="tk-jr">Обновить</button><p id="tk-status" class="tk-status"></p><div id="tk-out"></div>`);
    document.getElementById('tk-jr').onclick = () => run('Загружаю', loadJournal);
    run('Загружаю', loadJournal);
  }
  async function loadJournal() {
    const runs = await api('/api/runs?limit=40');
    document.getElementById('tk-out').innerHTML = runs.length ? `<table class="tk-table"><tr><td>Запуск</td><td>Модель</td><td>Вызовы / токены</td><td>Время</td><td>Итог</td></tr>
      ${runs.map(m => `<tr class="tk-run" data-id="${esc(m.id)}"><td>${esc(m.kind)}<div class="muted">${esc(m.id.slice(0, 15))}</div></td>
      <td>${esc((m.models || []).join(', ') || '—')}</td><td>${m.llm_calls ?? 0} / ${(m.input_tokens || 0) + (m.output_tokens || 0)}</td>
      <td>${(m.duration_s ?? 0).toFixed ? m.duration_s.toFixed(1) : m.duration_s} с</td>
      <td>${m.ok ? '<span class="pill ok">ok</span>' : '<span class="pill err">ошибка</span>'}${m.repairs ? ` · ремонтов ${m.repairs}` : ''}${(m.residual || []).length ? ' · остаток' : ''}</td></tr>`).join('')}</table>
      <div id="tk-run"></div>` : '<p class="muted">Журнал пуст.</p>';
    document.querySelectorAll('.tk-run').forEach(tr => tr.onclick = () => run('Открываю', () => showRun(tr.dataset.id)));
    status(`Записей: ${runs.length}`, 'ok');
  }
  async function showRun(id) {
    const d = await api('/api/runs/' + encodeURIComponent(id));
    const m = d.meta;
    document.getElementById('tk-run').innerHTML = `<h4>Запуск ${esc(id)}</h4>
      <p>Промпты: ${esc(Object.entries(m.prompts || {}).map(([k, v]) => `${k} ${v}`).join(', ') || '—')}</p>
      <p>Токены: вход ${m.input_tokens || 0}, выход ${m.output_tokens || 0} · время модели ${m.llm_seconds || 0} с · ПДн замаскировано: ${m.pii_masked ?? 0}</p>
      ${m.error ? `<div class="issue err">${esc(m.error)}</div>` : ''}
      ${(m.fixes || []).length ? `<p>Автоисправления: ${esc(m.fixes.join('; '))}</p>` : ''}
      ${(m.residual || []).length ? `<div class="issue warn">Остались проблемы:<br>${esc(m.residual.join('\n'))}</div>` : ''}
      ${d.llm_calls.map(c => `<details class="attempt"><summary>${esc(c.stage)} · ${esc(c.model)} · ${c.latency_s} с · ${c.input_tokens ?? '?'}/${c.output_tokens ?? '?'} ток. · ${esc(c.output_mode || '')}</summary>
        <pre>${esc((c.messages || []).map(x => `[${x.role}]\n${x.content}`).join('\n\n').slice(0, 6000))}</pre><pre>${esc((c.response || '').slice(0, 6000))}</pre></details>`).join('')}
      <button id="tk-jexp">Экспорт JSON</button>`;
    document.getElementById('tk-jexp').onclick = () => { window.authDownload('/api/runs/' + encodeURIComponent(id) + '/export'); };
    status('Готово', 'ok');
  }

  // ------------------------------------------------------------------ export
  function renderExport() {
    body(`<div class="tk-grid">
      <button id="tk-e-bpmn" class="primary">BPMN 2.0 (.bpmn)</button><button id="tk-e-svg">SVG</button><button id="tk-e-png">PNG</button>
      <button id="tk-e-ir">JSON IR</button><button id="tk-e-md">Регламент .md</button><button id="tk-e-docx">Регламент .docx</button></div>
      <label class="tk-check"><input type="checkbox" id="tk-cam"> Пометка совместимости с Camunda 8 (атрибуты Zeebe)</label>
      <button id="tk-e-cam" disabled>Скачать BPMN для Camunda 8</button>
      <p class="hint">Совместимость с Camunda 8 — заготовка для инженера: условия ветвей нужно задать в FEEL.</p>
      <p id="tk-status" class="tk-status"></p>`);
    const go = (fn) => () => run('Готовлю файл', async () => { await fn(); status('Скачано', 'ok'); });
    document.getElementById('tk-e-bpmn').onclick = () => document.getElementById('dl-bpmn').click();
    document.getElementById('tk-e-svg').onclick = () => document.getElementById('dl-svg').click();
    document.getElementById('tk-e-png').onclick = () => document.getElementById('dl-png').click();
    document.getElementById('tk-e-ir').onclick = go(async () => downloadPost('/api/export', { xml: await currentXml(), format: 'ir' }, 'process.ir.json'));
    document.getElementById('tk-e-md').onclick = go(async () => downloadPost('/api/sop', { xml: await currentXml(), format: 'md_file' }, 'регламент.md'));
    document.getElementById('tk-e-docx').onclick = go(async () => downloadPost('/api/sop', { xml: await currentXml(), format: 'docx' }, 'регламент.docx'));
    document.getElementById('tk-cam').onchange = (e) => { document.getElementById('tk-e-cam').disabled = !e.target.checked; };
    document.getElementById('tk-e-cam').onclick = go(async () => {
      const r = await downloadPost('/api/export', { xml: await currentXml(), format: 'camunda' }, 'process_camunda8.bpmn');
      const notes = JSON.parse(r.headers.get('X-Notes') || '[]'); if (notes.length) chat(notes.join(' '), 'bot');
    });
  }

  // ------------------------------------------------------------------ diff after dialogue edits
  function showDiff(d) {
    if (!d || d.empty) return;
    clearMarks();
    mark(d.highlight.added, 'diff-added');
    mark(d.highlight.changed, 'diff-changed');
    const parts = [];
    if (d.added.length) parts.push('➕ ' + d.added.map(x => x.name || x.id).join(', '));
    if (d.removed.length) parts.push('➖ ' + d.removed.map(x => x.name || x.id).join(', '));
    if (d.changed.length) parts.push('✎ ' + d.changed.map(x => x.name || x.id).join(', '));
    if (d.flows_added.length || d.flows_removed.length) parts.push(`связи +${d.flows_added.length}/−${d.flows_removed.length}`);
    if (parts.length) chat('Изменения: ' + parts.join(' · ') + ' (зелёным — новое, оранжевым — изменённое; откат — вкладка «История»)', 'bot diff');
  }

  // ------------------------------------------------------------------ left panel: templates, documents, PII
  function installLeft() {
    const after = document.getElementById('example');
    if (!after || document.getElementById('template')) return;
    const box = document.createElement('div');
    box.className = 'tk-left';
    box.innerHTML = `<label for="template">Отраслевой шаблон (энергетика)</label>
      <div class="row"><select id="template"><option value="">Выберите шаблон</option></select><button id="template-open" disabled>Открыть</button></div>`;
    after.after(box);
    api('/api/templates').then(list => list.forEach(t => {
      const o = document.createElement('option'); o.value = t.id; o.textContent = t.title; o.title = t.description;
      document.getElementById('template').append(o);
    })).catch(() => {});
    document.getElementById('template').onchange = (e) => { document.getElementById('template-open').disabled = !e.target.value; };
    document.getElementById('template-open').onclick = async () => {
      const id = document.getElementById('template').value; if (!id) return;
      busy(true, 'Открываю шаблон…');
      try {
        const r = await api('/api/templates/' + encodeURIComponent(id));
        state.text = ''; $('#text').value = '';
        window.agentFeatures?.clearInterview();
        await applyResult(r);
        $('#summary').innerHTML = `<span class="pill ok">шаблон</span> ${esc(r.summary || '')}`;
      } catch (e) { $('#summary').textContent = e.message; } finally { busy(false); }
    };
    const text = document.getElementById('text');
    const tools = document.createElement('div');
    tools.className = 'tk-doc-tools';
    tools.innerHTML = `<label class="btn tk-upload" data-help="Загрузить описание из файла: txt, docx, pdf или расшифровку интервью.">Загрузить документ<input id="doc-file" type="file" accept=".txt,.md,.docx,.pdf,.srt,.vtt" hidden></label>
      <button id="pii-check" type="button" data-help="Показать, какие персональные данные будут замаскированы перед отправкой модели.">Проверить ПДн</button>
      <span id="doc-info" class="hint"></span>`;
    text.after(tools);
    document.getElementById('doc-file').onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const info = document.getElementById('doc-info');
      info.textContent = 'Читаю файл…';
      try {
        const b64 = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1] || ''); r.onerror = rej; r.readAsDataURL(f); });
        const r = await api('/api/upload', { filename: f.name, content_base64: b64 });
        text.value = r.text;
        info.textContent = `${f.name}: ${r.chars.toLocaleString('ru')} символов${r.parts > 1 ? ` · будет обработан частями (${r.parts})` : ''}${r.kind === 'transcript' ? ' · расшифровка интервью' : ''}`;
      } catch (err) { info.textContent = 'Ошибка: ' + err.message; }
      e.target.value = '';
    };
    document.getElementById('pii-check').onclick = async () => {
      const info = document.getElementById('doc-info');
      try {
        const r = await api('/api/pii', { text: text.value });
        info.innerHTML = !r.enabled ? 'Маскирование ПДн выключено (PII_MASK=false).' : r.items.length
          ? `Будет замаскировано: ${r.items.map(i => `<span class="pill warn" title="${esc(i.original)}">${esc(i.token)}</span>`).join(' ')}`
          : 'Персональные данные не найдены.';
      } catch (err) { info.textContent = err.message; }
    };
  }

  // ------------------------------------------------------------------ hooks from app.js
  async function onResult(res) {
    if (!res) return;
    if (res.diff) showDiff(res.diff);
    const extra = [];
    if ((res.pii || []).length) extra.push(`<h4>Защита данных</h4><p>Перед отправкой модели замаскировано: ${res.pii.map(i => `<span class="pill warn" title="${esc(i.original)}">${esc(i.token)}</span>`).join(' ')}</p>`);
    if ((res.fixes || []).length) extra.push(`<h4>Исправлено правилами</h4><ul class="list">${res.fixes.map(f => `<li>${esc(f)}</li>`).join('')}</ul>`);
    if ((res.residual || []).length) extra.push(`<h4>Не удалось исправить автоматически</h4><div class="issue err">${esc(res.residual.join('\n'))}</div><p class="hint">Схема построена как черновик. Исправьте вручную или уточните описание.</p>`);
    if (res.lint_summary && (res.lint_summary.errors || res.lint_summary.warnings)) extra.push(`<p><a href="#" id="tk-to-lint">Линтер: ошибок ${res.lint_summary.errors}, предупреждений ${res.lint_summary.warnings} →</a></p>`);
    if (extra.length) {
      document.getElementById('tab-report').insertAdjacentHTML('beforeend', extra.join(''));
      const a = document.getElementById('tk-to-lint'); if (a) a.onclick = (e) => { e.preventDefault(); open('lint'); };
    }
  }

  window.toolkit = { onResult, showDiff, clearMarks, open };
  install();
})();
