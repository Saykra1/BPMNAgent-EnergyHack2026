// Run with a local server already running: node tools/smoke-ui.mjs http://127.0.0.1:8093
// LLM planning is mocked; graph construction, XML checks and UI use the real backend.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const url = process.argv[2] || 'http://127.0.0.1:8093';
const browser = process.env.BROWSER_CDP
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP)
  : await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const text = 'Заявитель предоставляет документы в течение 20 рабочих дней с получения уведомления.';
const plan = {
  title: 'Проверка документов', participants: [{ id: 'a', name: 'Заявитель' }],
  elements: [{ id: 'supply', type: 'user_task', name: 'Предоставить документы', participant: 'a',
    source_quote: text, deadline: '20 рабочих дней с получения уведомления', documents: ['Заявка'] }],
  flows: [{ from: 'start', to: 'supply' }, { from: 'supply', to: 'end' }],
  questions: ['Что делать при истечении срока?'], assumptions: []
};
let preparations = 0;
await page.route('**/api/prepare', route => {
  preparations++;
  const reviewed = structuredClone(plan);
  if (preparations > 1) reviewed.questions = [];
  return route.fulfill({ json: { ok: true, plan: reviewed } });
});
try {
  await page.goto(url);
  await page.locator('#text').fill(text);
  await page.locator('#generate').click();
  await page.locator('#answer-0').fill('Заявку закрывают и уведомляют заявителя.');
  await page.locator('#answer-apply').click();
  await page.waitForFunction(() => document.querySelector('#plan-build')?.textContent === 'Построить схему');
  await page.locator('#plan-build').click();
  await page.waitForFunction(() => document.querySelector('#tab-report').textContent.includes('Проверка текущей схемы'));
  const task = page.locator('[data-element-id="UserTask_1"] .djs-visual');
  await task.click();
  await page.locator('#show-source').click();
  const selection = await page.locator('#text').evaluate(e => e.value.slice(e.selectionStart, e.selectionEnd));
  assert.equal(selection, text);
  await page.locator('#detail-documents').fill('Заявка\nУведомление');
  await page.locator('#detail-save').click();
  await page.waitForFunction(() => document.querySelector('#tab-energy').textContent.includes('Уведомление'));
  const xml = await page.evaluate(async () => (await modeler.saveXML({ format: true })).xml);
  assert.match(xml, /BPMN_AGENT_DETAILS:/);
  assert.match(xml, /Уведомление/);
  const valid = await page.request.post(url + '/api/validate', { data: { xml } });
  assert.equal((await valid.json()).xsd_valid, true);
  await page.locator('#relayout').click();
  await page.waitForFunction(() => document.querySelector('#loading').hidden);
  await page.locator('[data-element-id="UserTask_1"] .djs-visual').click();
  assert.equal(await page.locator('#detail-documents').inputValue(), 'Заявка\nУведомление');
  await page.locator('#simulate').click();
  assert.equal(await page.locator('#simulation-hint').isVisible(), true);
  assert.equal(await page.locator('#detail-save').isDisabled(), true);
  // Exercise the real simulator, not just the toolbar toggle.
  await page.getByRole('button', { name: 'Trigger Event', exact: true }).click();
  await page.getByRole('button', { name: 'Set animation speed = Fast', exact: true }).click();
  await page.getByText('Finished', { exact: true }).waitFor();
  await page.locator('#simulate').click();
  await page.getByRole('button', { name: 'Исключения', exact: true }).click();
  assert.match(await page.locator('#tab-energy').innerText(), /Истечение срока/);
  // Inspect a real hand edit; the backend must report the affected original ID.
  await page.evaluate(() => {
    const reg = modeler.get('elementRegistry');
    const flow = reg.filter(e => e.type === 'bpmn:SequenceFlow' && e.source.type === 'bpmn:UserTask')[0];
    modeler.get('modeling').removeConnection(flow);
  });
  await page.waitForFunction(() => document.querySelector('#tab-report').textContent.includes('Процесс обрывается'));
  await page.setViewportSize({ width: 900, height: 950 });
  await page.getByRole('button', { name: 'Отчёт', exact: true }).click();
  assert.equal(await page.locator('#right').isVisible(), true);
  assert.deepEqual(errors, []);
  console.log('PASS: interview, exact source, persistent cards, XSD, simulation, live inspection, responsive report');
} finally {
  await context.close();
  await browser.close();
}
