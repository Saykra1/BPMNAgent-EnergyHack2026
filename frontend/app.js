/* BPMN Agent web UI: generation, dialogue edits, bpmn-js modeler, export. */
const $ = (s) => document.querySelector(s);
const modeler = new BpmnJS({ container: '#canvas' });

const state = { text: '', code: '', plan: null, lastResult: null, examples: [], hasDiagram: false };

// ------------------------------------------------------------------ helpers
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function download(name, content, type) {
  const a = document.createElement('a');
  a.href = content instanceof Blob ? URL.createObjectURL(content) : URL.createObjectURL(new Blob([content], { type }));
  a.download = name; a.click();
}
function busy(on, text = 'Генерация…') {
  state.busy = on;
  window.agentFeatures?.onBusy(on);
  $('#loading').hidden = !on; $('#loading-text').textContent = text;
  ['#generate', '#refine', '#rebuild', '#relayout', '#show-saved', '#mode', '#example', '#text', '#open-file', '#simulate'].forEach((s) => ($(s).disabled = on));
}
function fileBase() {
  const t = (state.plan && state.plan.title) || 'process';
  return t.replace(/[^\p{L}\p{N}]+/gu, '_').slice(0, 60);
}

// ------------------------------------------------------------------ steps
const STEPS = ['plan', 'code', 'sandbox', 'validate', 'layout', 'xsd'];
let stepTimer = null;
function setStep(name, cls) { const li = document.querySelector(`#steps li[data-step="${name}"]`); if (li) li.className = cls || ''; }
function animateSteps(mode) {
  STEPS.forEach((s) => setStep(s, ''));
  if (mode === 'direct') setStep('plan', 'ok');
  let i = mode === 'direct' ? 1 : 0;
  setStep(STEPS[i], 'run');
  clearInterval(stepTimer);
  stepTimer = setInterval(() => { if (i < 1) { setStep(STEPS[i], 'ok'); i++; setStep(STEPS[i], 'run'); } }, 6000);
}
function finishSteps(res) {
  clearInterval(stepTimer);
  const a = res.attempts || [];
  const planOk = a.some((x) => x.stage === 'plan' && x.ok) || !a.some((x) => x.stage === 'plan');
  const repairs = a.filter((x) => x.stage.startsWith('repair')).length;
  setStep('plan', planOk ? 'ok' : 'fail');
  setStep('code', res.code ? (repairs ? 'retry' : 'ok') : 'fail');
  const sandboxFail = !res.xml && /Строка \d+/.test(res.message || '');
  setStep('sandbox', res.xml ? 'ok' : sandboxFail ? 'fail' : (res.code ? 'ok' : ''));
  const errs = (res.issues || []).filter((i) => i.level === 'error');
  setStep('validate', res.xml ? (errs.length ? 'retry' : 'ok') : (res.code && !sandboxFail ? 'fail' : ''));
  setStep('layout', res.xml ? 'ok' : '');
  setStep('xsd', res.xml ? (res.xsd_errors && res.xsd_errors.length ? 'fail' : 'ok') : '');
  const src = { llm: 'с первой попытки', llm_repaired: `после ${repairs} исправл. (repair-loop)`, reviewed_plan: 'из согласованного плана', plan_compiler: 'детерминированный компилятор плана (fallback)', lenient: 'мягкий режим с автоисправлениями', manual: 'из кода', error: 'ошибка' }[res.source] || res.source;
  $('#summary').innerHTML = res.xml
    ? `<span class="pill ${res.ok === false || errs.length ? 'warn' : 'ok'}">${res.ok === false || errs.length ? 'требует проверки' : 'готово'}</span> ${esc(src)} · ${res.duration_s} с`
    : `<span class="pill err">ошибка</span> ${esc(res.message || '')}`;
}

// ------------------------------------------------------------------ rendering results
async function showXml(xml) {
  window.agentFeatures?.beforeImport();
  state.hasDiagram = false;
  const { warnings } = await modeler.importXML(xml);
  $('#empty').hidden = true;
  state.hasDiagram = true;
  modeler.get('canvas').zoom('fit-viewport', 'auto');
  return warnings;
}

function renderReport(res, warnings = []) {
  const st = res.stats || {};
  const issues = res.issues || [];
  const count = (lvl) => issues.filter((i) => i.level === lvl).length;
  const xsdOk = res.xml && !(res.xsd_errors || []).length;
  let h = `<div class="kpis">
    <div class="kpi"><b>${st.nodes ?? '–'}</b><span>элементов</span></div>
    <div class="kpi"><b>${st.lanes ?? 0}/${st.pools ?? 0}</b><span>дорожек/пулов</span></div>
    <div class="kpi"><b>${(st.sequence_flows ?? 0) + (st.message_flows ?? 0)}</b><span>связей</span></div>
  </div>
  <div><span class="pill ${xsdOk ? 'ok' : 'err'}">XSD BPMN 2.0: ${xsdOk ? 'валиден' : 'ошибки'}</span>
  <span class="pill ${warnings.length ? 'warn' : 'ok'}">bpmn-js: ${warnings.length ? warnings.length + ' предупр.' : 'открывается без ошибок'}</span>
  <span class="pill ${count('error') ? 'err' : 'ok'}">семантика: ${count('error') ? count('error') + ' ошибок' : 'ок'}</span></div>`;
  if (res.summary) h += `<h4>Что изменено</h4><p>${esc(res.summary)}</p>`;
  if ((res.questions || []).length) h += `<h4>Вопросы к заказчику</h4><ul class="list">${res.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>`;
  if ((res.assumptions || []).length) h += `<h4>Допущения ассистента</h4><ul class="list">${res.assumptions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>`;
  const shown = issues.filter((i) => i.level !== 'fix');
  if (shown.length) h += `<h4>Замечания валидатора</h4>` + shown.map((i) => `<div class="issue ${i.level}" data-els="${esc((i.elements || []).join(','))}">${esc(i.message)}</div>`).join('');
  const fixes = issues.filter((i) => i.level === 'fix');
  if (fixes.length) h += `<h4>Автоисправления</h4>` + fixes.map((i) => `<div class="issue fix" data-els="${esc((i.elements || []).join(','))}">${esc(i.message)}</div>`).join('');
  if ((res.xsd_errors || []).length) h += `<h4>Ошибки XSD</h4><pre class="code">${esc(res.xsd_errors.join('\n'))}</pre>`;
  if (warnings.length) h += `<h4>Предупреждения bpmn-js</h4><pre class="code">${esc(warnings.map((w) => w.message).join('\n'))}</pre>`;
  if (res.run_id) h += `<p class="muted">Прогон: ${esc(res.run_id)} (runs/${esc(res.run_id)})</p>`;
  $('#tab-report').innerHTML = h;
  document.querySelectorAll('.issue[data-els]').forEach((el) => el.addEventListener('click', () => highlight(el.dataset.els.split(',').filter(Boolean))));
}

function highlight(ids) {
  const canvas = modeler.get('canvas'), reg = modeler.get('elementRegistry');
  reg.getAll().forEach((e) => canvas.removeMarker(e, 'highlight'));
  const found = ids.map((id) => reg.get(id)).filter(Boolean);
  found.forEach((e) => canvas.addMarker(e, 'highlight'));
  if (found[0] && found[0].x !== undefined) canvas.scrollToElement(found[0]);
}

function renderAttempts(res) {
  $('#attempts').innerHTML = (res.attempts || []).map((a, i) => `
    <details class="attempt" ${!a.ok ? 'open' : ''}><summary><span class="pill ${a.ok ? 'ok' : 'err'}">${a.ok ? 'ok' : 'ошибка'}</span> ${i + 1}. ${esc(a.stage)}</summary>
    ${a.error ? `<pre>${esc(a.error)}</pre>` : ''}${a.code ? `<pre>${esc(a.code)}</pre>` : ''}</details>`).join('') || '<p class="muted">Нет данных</p>';
}

async function applyResult(res) {
  state.lastResult = res;
  state.plan = res.plan || null;
  $('#plan-json').textContent = res.plan ? JSON.stringify(res.plan, null, 2) : 'После правок актуальна схема; исходный план не обновлялся.';
  if (res.plan) { state.plan = res.plan; $('#plan-json').textContent = JSON.stringify(res.plan, null, 2); }
  if (res.code) { state.code = res.code; $('#code').value = res.code; }
  let warnings = [];
  let imported = false;
  if (res.xml) {
    try { warnings = await showXml(res.xml); imported = true; } catch (e) { warnings = [{ message: 'importXML: ' + e.message }]; }
  }
  finishSteps(res);
  renderReport(res, warnings);
  renderAttempts(res);
  if (imported) await window.agentFeatures?.onResult(res);
}

function chat(text, cls) { const d = document.createElement('div'); d.className = 'msg ' + cls; d.textContent = text; $('#chat').append(d); $('#chat').scrollTop = 1e9; }

// ------------------------------------------------------------------ actions
$('#generate').onclick = async () => {
  const text = $('#text').value.trim();
  if (text.length < 10) { alert('Опишите процесс подробнее'); return; }
  if ($('#mode').value === 'guided') { await window.agentFeatures.prepare(text); return; }
  state.text = text;
  const mode = $('#mode').value;
  busy(true, 'Ассистент строит схему…'); animateSteps(mode);
  try { await applyResult(await api('/api/generate', { text, mode })); }
  catch (e) { clearInterval(stepTimer); $('#summary').innerHTML = `<span class="pill err">ошибка</span> ${esc(e.message)}`; }
  finally { busy(false); }
};

$('#refine').onclick = async () => {
  const instruction = $('#instruction').value.trim();
  if (!instruction) return;
  if (!state.hasDiagram) { alert('Сначала постройте или откройте диаграмму'); return; }
  chat(instruction, 'user'); $('#instruction').value = '';
  busy(true, 'Вношу изменения…'); animateSteps('direct');
  try {
    const { xml } = await modeler.saveXML({ format: true });
    const res = await api('/api/refine', { instruction, xml, code: state.code, text: state.text });
    if (res.xml) state.text += '\n\nУточнение аналитика: ' + instruction;
    await applyResult(res);
    chat(res.xml ? (res.summary || 'Готово, схема обновлена') : ('Не получилось: ' + (res.message || '')), res.xml ? 'bot' : 'bot err');
  } catch (e) { chat('Ошибка: ' + e.message, 'bot err'); }
  finally { busy(false); }
};
$('#instruction').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#refine').click(); });

$('#rebuild').onclick = async () => {
  busy(true, 'Перестраиваю из кода…');
  try { await applyResult(await api('/api/build', { code: $('#code').value })); }
  catch (e) { alert(e.message); } finally { busy(false); }
};

$('#relayout').onclick = async () => {
  if (!state.hasDiagram) return;
  busy(true, 'Пересчитываю раскладку…');
  try { const { xml } = await modeler.saveXML(); await applyResult(await api('/api/import', { xml })); }
  catch (e) { alert(e.message); } finally { busy(false); }
};

$('#open-file').onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const xml = await f.text();
  try {
    state.text = ''; state.plan = null; state.code = '';
    $('#text').value = ''; $('#plan-json').textContent = ''; $('#code').value = '';
    window.agentFeatures?.clearInterview();
    const warnings = await showXml(xml);
    const v = await api('/api/validate', { xml });
    renderReport({ xml, xsd_errors: v.xsd_errors, stats: {}, issues: [] }, warnings);
    const imp = await api('/api/import', { xml }).catch(() => null);
    if (imp && imp.code) { state.code = imp.code; $('#code').value = imp.code; }
    await window.agentFeatures?.onResult({});
  } catch (err) { alert('Не удалось открыть файл: ' + err.message); }
  e.target.value = '';
};

$('#check-xsd').onclick = async () => {
  if (!state.hasDiagram) return;
  const { xml } = await modeler.saveXML({ format: true });
  const v = await api('/api/validate', { xml });
  alert(v.xsd_valid ? 'Файл соответствует XSD-схеме BPMN 2.0 (OMG)' : 'Ошибки XSD:\n' + v.xsd_errors.slice(0, 10).join('\n'));
};

$('#fit').onclick = () => modeler.get('canvas').zoom('fit-viewport', 'auto');
$('#dl-bpmn').onclick = async () => { const { xml } = await modeler.saveXML({ format: true }); download(fileBase() + '.bpmn', xml, 'application/xml'); };
$('#dl-svg').onclick = async () => { const { svg } = await modeler.saveSVG(); download(fileBase() + '.svg', svg, 'image/svg+xml'); };
$('#dl-png').onclick = async () => {
  const { svg } = await modeler.saveSVG();
  const img = new Image();
  const m = svg.match(/width="([\d.]+)"[^>]*height="([\d.]+)"/);
  const scale = 2, w = m ? +m[1] : 1600, h = m ? +m[2] : 900;
  img.onload = () => {
    const c = document.createElement('canvas'); c.width = w * scale; c.height = h * scale;
    const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    c.toBlob((b) => download(fileBase() + '.png', b));
  };
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
};

document.querySelectorAll('.tabs button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tab').forEach((t) => (t.hidden = t.id !== 'tab-' + b.dataset.tab));
});

$('#example').onchange = () => {
  const ex = state.examples.find((x) => x.id === $('#example').value);
  if (!ex) { $('#show-saved').hidden = true; return; }
  $('#text').value = ex.text;
  $('#show-saved').hidden = !ex.has_result;
};
$('#show-saved').onclick = async () => {
  const id = $('#example').value; if (!id) return;
  const ex = await api('/api/examples/' + encodeURIComponent(id));
  state.text = ex.text;
  $('#text').value = ex.text;
  window.agentFeatures?.clearInterview();
  const r = ex.report || {};
  await applyResult({ xml: ex.xml, code: ex.code, plan: ex.plan, issues: r.issues || [], xsd_errors: r.xsd_errors || [],
    stats: r.stats || {}, assumptions: r.assumptions || [], questions: r.questions || [], attempts: [], source: 'manual', duration_s: 0 });
  $('#summary').innerHTML = '<span class="pill ok">сохранённый результат</span> ' + esc(r.source || '');
};

// ------------------------------------------------------------------ init
(async () => {
  try {
    const h = await api('/api/health');
    const b = $('#llm-status');
    b.textContent = h.llm ? `LLM: ${h.provider} · ${h.model || ''}` : 'LLM не настроен — наведите для причины';
    b.title = h.llm ? `Настройки: ${h.env_file || 'переменные окружения'}` : (h.llm_error || '');
    if (!h.llm && h.llm_error) $('#summary').innerHTML = `<span class="pill err">LLM</span> ${esc(h.llm_error)}`;
    b.className = 'badge ' + (h.llm ? 'ok' : 'off');
  } catch { /* backend unavailable */ }
  try {
    state.examples = await api('/api/examples');
    for (const ex of state.examples) { const o = document.createElement('option'); o.value = ex.id; o.textContent = ex.title; $('#example').append(o); }
  } catch { /* ignore */ }
})();
