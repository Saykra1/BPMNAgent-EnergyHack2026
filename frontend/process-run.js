/* Process run panel: start the current diagram with initial data, let automatic steps run their code,
   let people complete their steps (data + document or attached file), pick branches where a gateway has
   no checks, fix an error and retry, download every document and the whole archive. */
(() => {
  const KEY = 'bpmn-agent-run-id';
  const VARS = 'bpmn-agent-run-variables';
  let run = null;
  const marked = [];
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };

  // ------------------------------------------------------------------ panel shell
  const panel = document.createElement('aside');
  panel.id = 'run-panel';
  panel.setAttribute('aria-label', 'Запуск процесса');
  panel.innerHTML = `<div class="run-head"><div><span class="eyebrow">ИСПОЛНЕНИЕ</span><h2>Запуск процесса</h2></div>
    <button class="icon-button" id="run-close" aria-label="Закрыть">×</button></div><div class="run-body" id="run-body"></div>`;
  document.body.append(panel);
  panel.querySelector('#run-close').onclick = () => close();

  function open() {
    if (!state.hasDiagram) { alert('Сначала постройте или откройте схему'); return; }
    document.body.classList.add('run-open');
    const saved = store.get(KEY);
    if (!run && saved) {
      api('/api/run/' + saved).then(r => { run = r; render(); }).catch(() => { store.set(KEY, null); render(); });
    }
    render();
  }
  function close() { document.body.classList.remove('run-open'); clearMarks(); }

  // ------------------------------------------------------------------ canvas markers
  function clearMarks() {
    const canvas = modeler.get('canvas');
    while (marked.length) { const [id, cls] = marked.pop(); try { canvas.removeMarker(id, cls); } catch { /* element gone */ } }
  }
  function mark() {
    clearMarks();
    if (!run || !document.body.classList.contains('run-open')) return;
    const canvas = modeler.get('canvas'); const reg = modeler.get('elementRegistry');
    const add = (id, cls) => { if (reg.get(id)) { canvas.addMarker(id, cls); marked.push([id, cls]); } };
    run.done_nodes.forEach(id => add(id, 'run-done'));
    run.waiting.forEach(w => add(w.node, 'run-waiting'));
    if (run.error?.node) add(run.error.node, 'run-error');
  }
  function focus(id) {
    const el = modeler.get('elementRegistry').get(id);
    if (!el) return;
    modeler.get('selection').select(el);
    try { modeler.get('canvas').scrollToElement(el); } catch { /* old bpmn-js */ }
  }

  // ------------------------------------------------------------------ rendering
  const STATUS_CLASS = { waiting: 'warn', done: 'ok', error: 'err', running: '' };
  const fmt = (v) => typeof v === 'string' ? v : JSON.stringify(v);

  function render() {
    const body = panel.querySelector('#run-body');
    if (!run) {
      body.innerHTML = `<p class="muted">Процесс выполняется по текущей схеме. Автоматические шаги (сервисные задачи)
        запускают свой код, развилки — проверки веток, люди выполняют свои шаги и формируют документы о работе.</p>
        <label>Исходные данные процесса (JSON)<textarea id="run-vars" class="be-code" rows="6">${esc(store.get(VARS) || '{}')}</textarea></label>
        <p class="hint">Например: {"позиции": [{"цена": 700, "кол": 2}], "лимит": 1000}. Код и проверки задаются в свойствах блоков (⚙).</p>
        <p class="be-error" id="run-err"></p>
        <button class="primary" id="run-start">▶ Запустить</button>`;
      body.querySelector('#run-start').onclick = start;
      mark();
      return;
    }
    const docs = run.documents;
    body.innerHTML = `
      <div class="run-status"><span class="pill ${STATUS_CLASS[run.status] || ''}">${esc(run.status_text)}</span>
        <span class="muted">«${esc(run.title)}» · ${esc(run.created)}</span></div>
      <p class="be-error" id="run-err"></p>
      ${run.error ? `<div class="run-card bad"><strong>Остановлено: ${esc(run.error.message)}</strong>
        <p class="hint">Исправьте код или проверку в свойствах блока (⚙) и нажмите «Повторить» — шаг выполнится заново по исправленной схеме.</p>
        <div class="be-row"><button class="primary" id="run-retry">Повторить шаг</button>${run.error.node ? `<button id="run-goto-err" class="quiet">Показать на схеме</button>` : ''}</div></div>` : ''}
      ${run.waiting.map(w => w.kind === 'task' ? taskCard(w) : choiceCard(w)).join('')}
      ${run.status === 'done' ? '<div class="run-card ok"><strong>Процесс завершён.</strong> Все документы — ниже и в архиве.</div>' : ''}
      <h3>Документы о выполненной работе (${docs.length})</h3>
      ${docs.length ? `<ul class="run-docs">${docs.map(d => `<li><a href="/api/run/${run.id}/documents/${d.n}" download="${esc(d.file)}">${esc(d.file)}</a>
        <span class="muted">${d.kind === 'auto' ? 'автоматически' : esc(d.author)} · ${esc(d.created)}</span>
        ${d.attachment ? `<br>📎 <a href="/api/run/${run.id}/attachments/${d.n}" download="${esc(d.attachment.name)}">${esc(d.attachment.name)}</a>` : ''}
        ${d.text ? `<details><summary>Текст</summary><p class="run-text">${esc(d.text)}</p></details>` : ''}</li>`).join('')}</ul>
        <a class="btn" href="/api/run/${run.id}/archive" download="запуск_${run.id}.zip">Скачать всё (zip): документы, приложения, журнал</a>` : '<p class="muted">Пока нет.</p>'}
      <h3>Данные процесса</h3><pre class="be-out">${esc(JSON.stringify(run.variables, null, 1))}</pre>
      <h3>Журнал</h3><ol class="run-log">${run.history.slice().reverse().map(h => `<li class="${esc(h.kind)}"><span class="muted">${esc(h.at.slice(11))}</span>
        ${h.node ? `<button class="quiet" data-goto="${esc(h.node)}">${esc(h.name)}</button>: ` : ''}${esc(h.text)}
        ${h.changed && Object.keys(h.changed).length ? `<br><span class="muted">${esc(Object.entries(h.changed).map(([k, v]) => `${k} = ${fmt(v)}`).join('; ').slice(0, 300))}</span>` : ''}
        ${h.checks?.length ? `<br><span class="muted">${esc(h.checks.join('; '))}</span>` : ''}</li>`).join('')}</ol>
      <div class="be-row"><button id="run-new">Новый запуск</button></div>`;
    body.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => focus(b.dataset.goto));
    body.querySelector('#run-goto-err')?.addEventListener('click', () => focus(run.error.node));
    body.querySelector('#run-retry')?.addEventListener('click', retry);
    body.querySelector('#run-new').onclick = () => { run = null; store.set(KEY, null); render(); };
    body.querySelectorAll('.run-task').forEach(bindTask);
    body.querySelectorAll('.run-choice').forEach(bindChoice);
    mark();
  }

  function taskCard(w) {
    return `<form class="run-card run-task" data-token="${esc(w.token)}" novalidate>
      <strong>${esc(w.name)}</strong> <button type="button" class="quiet" data-goto="${esc(w.node)}">на схеме</button>
      <p class="muted">Исполнитель: ${esc(w.actor)}${w.deadline ? ` · срок: ${esc(w.deadline)}` : ''}</p>
      ${w.description ? `<p>${esc(w.description)}</p>` : ''}
      ${w.fields.map(f => `<label>${esc(f.replace(/_/g, ' '))}<input data-field="${esc(f)}" required></label>`).join('')}
      <label>Документ о выполненной работе<textarea class="run-text-in" rows="4" placeholder="${esc(w.template || 'Что сделано, результат, номера документов')}"></textarea></label>
      ${w.documents?.length ? `<p class="hint">Документы по регламенту: ${esc(w.documents.join(', '))}</p>` : ''}
      <div class="be-row"><label>Прикрепить файл<input type="file" class="run-file"></label><label>Кто выполнил<input class="run-author" value="${esc(w.actor)}" maxlength="200"></label></div>
      <p class="hint">Нужно сформировать документ (текст выше — из него будет DOCX) или прикрепить файл.</p>
      <button class="primary" type="submit">Завершить шаг</button></form>`;
  }
  function choiceCard(w) {
    const type = w.multi ? 'checkbox' : 'radio';
    return `<form class="run-card run-choice" data-token="${esc(w.token)}"><strong>${esc(w.name)}</strong>
      <button type="button" class="quiet" data-goto="${esc(w.node)}">на схеме</button>
      <p class="muted">У развилки нет проверок — выберите ветку${w.multi ? ' (можно несколько)' : ''}.</p>
      ${w.options.map((o, i) => `<label class="be-check"><input type="${type}" name="opt-${esc(w.token)}" value="${i}"> ${esc(o)}</label>`).join('<br>')}
      <div><button class="primary" type="submit">Продолжить</button></div></form>`;
  }

  // ------------------------------------------------------------------ actions
  function fail(e) { const box = panel.querySelector('#run-err'); if (box) box.textContent = e.message; }
  async function act(fn) {
    try { busy(true, 'Выполнение процесса…'); run = await fn(); store.set(KEY, run.id); render(); }
    catch (e) { fail(e); }
    finally { busy(false); }
  }
  async function currentXml() { return (await modeler.saveXML({ format: true })).xml; }

  function start() {
    const text = panel.querySelector('#run-vars').value;
    let variables;
    try { variables = JSON.parse(text || '{}'); if (!variables || typeof variables !== 'object' || Array.isArray(variables)) throw 0; }
    catch { fail(new Error('Исходные данные — JSON-объект, например {"сумма": 1500}')); return; }
    store.set(VARS, text);
    act(async () => api('/api/run/start', { xml: await currentXml(), variables }));
  }
  function retry() { act(async () => api(`/api/run/${run.id}/retry`, { xml: await currentXml() })); }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      if (file.size > 10 * 1024 * 1024) { reject(new Error('Файл больше 10 МБ')); return; }
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1] || '');
      r.onerror = () => reject(new Error('Не удалось прочитать файл'));
      r.readAsDataURL(file);
    });
  }
  function bindTask(form) {
    form.onsubmit = async (e) => {
      e.preventDefault();
      const values = {};
      for (const i of form.querySelectorAll('[data-field]')) {
        if (!i.value.trim()) { fail(new Error(`Заполните «${i.dataset.field.replace(/_/g, ' ')}»`)); i.focus(); return; }
        values[i.dataset.field] = i.value.trim();
      }
      const text = form.querySelector('.run-text-in').value.trim();
      const file = form.querySelector('.run-file').files[0];
      if (!text && !file) { fail(new Error('Сформируйте документ о выполненной работе или прикрепите файл')); return; }
      const body = { token: form.dataset.token, text, values, author: form.querySelector('.run-author').value.trim() };
      try { if (file) { body.file_name = file.name; body.file_base64 = await readFile(file); } }
      catch (err) { fail(err); return; }
      act(() => api(`/api/run/${run.id}/complete`, body));
    };
  }
  function bindChoice(form) {
    form.onsubmit = (e) => {
      e.preventDefault();
      const options = [...form.querySelectorAll('input:checked')].map(i => Number(i.value));
      if (!options.length) { fail(new Error('Выберите ветку')); return; }
      act(() => api(`/api/run/${run.id}/choose`, { token: form.dataset.token, options }));
    };
  }

  // ------------------------------------------------------------------ entry points
  modeler.get('eventBus').on('import.done', () => { marked.length = 0; if (document.body.classList.contains('run-open')) mark(); });
  const menu = document.querySelector('.toolbar-menu');
  if (menu && !document.getElementById('run-open')) {
    const b = document.createElement('button');
    b.id = 'run-open'; b.type = 'button'; b.textContent = 'Выполнить процесс (код и документы)';
    b.dataset.help = 'Выполнить процесс: код автоматических шагов, проверки развилок, документы исполнителей.';
    b.onclick = () => { document.querySelector('.toolbar-more')?.removeAttribute('open'); open(); };
    menu.querySelector('#simulate')?.before(b);
  }
  window.processRun = { open, close, get: () => run };
})();
