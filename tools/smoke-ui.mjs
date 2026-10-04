// Run with a local server already running: node tools/smoke-ui.mjs http://127.0.0.1:8094
// LLM planning is mocked; graph construction, XML checks and UI use the real backend.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const url = process.argv[2] || 'http://127.0.0.1:8094';
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
  assert.equal(await page.locator('#right').isVisible(), false);
  assert.equal(await page.locator('#open-demo').isVisible(), true);
  assert.equal(await page.locator('#dl-bpmn').isDisabled(), true);
  assert.equal(await page.locator('#chat-box').isVisible(), false);
  await page.locator('#open-demo').focus();
  assert.match(await page.locator('#help-popover').innerText(), /редактируемый пример/);
  await page.locator('#generate').hover();
  assert.match(await page.locator('#help-popover').innerText(), /редактируемую BPMN-схему/);
  await page.locator('#text').fill(text);
  assert.equal(await page.locator('#mode').inputValue(), 'two_stage');
  assert.match(await page.locator('#mode-name').innerText(), /Без уточнений/);
  await page.locator('.generation-settings summary').click();
  await page.locator('#mode').selectOption('guided');
  assert.match(await page.locator('#mode-name').innerText(), /С уточнениями/);
  await page.locator('#generate').click();
  await page.locator('#answer-0').fill('Заявку закрывают и уведомляют заявителя.');
  await page.locator('#answer-apply').click();
  await page.waitForFunction(() => document.querySelector('#plan-build')?.textContent === 'Построить схему');
  await page.locator('#plan-build').click();
  await page.waitForFunction(() => document.querySelector('#tab-report').textContent.includes('Проверка текущей схемы'));
  assert.equal(await page.locator('#diagram-status').isVisible(), true);
  assert.equal(await page.locator('#dl-bpmn').isEnabled(), true);
  assert.equal(await page.locator('#chat-box').isVisible(), true);
  await page.locator('.toolbar-more summary').click();
  await page.locator('#palette-toggle').click();
  assert.equal(await page.locator('#palette-toggle').getAttribute('aria-pressed'), 'true');
  await page.locator('.toolbar-more summary').click();
  await page.locator('#palette-toggle').click();
  await page.locator('#read-view').click();
  assert.equal(await page.evaluate(() => modeler.get('canvas').zoom() >= 1.05), true);
  await page.locator('#navigator-toggle').click();
  await page.locator('#step-search').fill('предоставить');
  assert.match(await page.locator('#search-count').innerText(), /Найдено: 1/);
  assert.equal(await page.locator('[data-element-id="UserTask_1"]').evaluate(e => e.classList.contains('nav-search')), true);
  assert.equal(await page.locator('#participant-filter option').count() > 1, true);
  await page.locator('#participant-filter').selectOption({ index: 1 });
  assert.equal(await page.locator('.nav-participant-hit').count() > 0, true);
  await page.locator('#search-results button').first().click();
  assert.equal(await page.locator('#navigator').isVisible(), false);
  await page.locator('#nav-focus-clear').click();
  await page.locator('.source-fragment').first().hover();
  const linkedFill = await page.locator('[data-element-id="UserTask_1"] .djs-visual > :first-child')
    .evaluate(element => getComputedStyle(element).fill);
  assert.notEqual(linkedFill, 'rgb(255, 255, 255)');
  await page.locator('#insights-toggle').click();
  await page.locator('#right').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#right').isVisible(), true);
  await page.locator('#show-source').click();
  assert.match(await page.locator('.source-current').first().innerText(), /Заявитель предоставляет документы/);
  await page.locator('#insights-toggle').click();
  await page.locator('#detail-documents').fill('Заявка\nУведомление');
  await page.locator('#detail-save').click();
  await page.waitForFunction(() => document.querySelector('#tab-energy').textContent.includes('Уведомление'));
  const xml = await page.evaluate(async () => (await modeler.saveXML({ format: true })).xml);
  assert.match(xml, /BPMN_AGENT_DETAILS:/);
  assert.match(xml, /Уведомление/);
  const valid = await page.request.post(url + '/api/validate', { data: { xml } });
  assert.equal((await valid.json()).xsd_valid, true);
  await page.locator('#insights-close').click();
  await page.locator('#tour-toggle').click();
  assert.equal(await page.locator('#tour-panel').isVisible(), true);
  assert.match(await page.locator('#tour-title').innerText(), /Начало|Start/i);
  assert.equal(await page.locator('.tour-node-cat').count(), 1);
  await page.locator('#tour-next').click();
  assert.match(await page.locator('#tour-title').innerText(), /Предоставить документы/);
  await page.locator('#tour-close').click();
  await page.locator('.toolbar-more summary').click();
  await page.locator('#relayout').click();
  await page.waitForFunction(() => document.querySelector('#loading').hidden);
  await page.locator('[data-element-id="UserTask_1"] .djs-hit').click();
  await page.locator('#insights-toggle').click();
  assert.equal(await page.locator('#detail-documents').inputValue(), 'Заявка\nУведомление');
  await page.locator('#insights-close').click();
  await page.locator('.toolbar-more summary').click();
  await page.locator('#simulate').click();
  assert.equal(await page.locator('#simulation-hint').isVisible(), true);
  assert.equal(await page.locator('#detail-save').isDisabled(), true);
  // Exercise the real simulator, not just the toolbar toggle.
  await page.getByRole('button', { name: 'Trigger Event', exact: true }).click();
  await page.locator('.bts-token[data-mascot] .bts-mascot').first().waitFor();
  await page.getByRole('button', { name: 'Set animation speed = Fast', exact: true }).click();
  await page.getByText('Finished', { exact: true }).waitFor();
  await page.locator('.toolbar-more summary').click();
  await page.locator('#simulate').click();
  await page.locator('#insights-toggle').click();
  await page.getByRole('button', { name: 'Риски', exact: true }).click();
  assert.match(await page.locator('#tab-energy').innerText(), /Истечение срока/);
  // Inspect a real hand edit; the backend must report the affected original ID.
  await page.evaluate(() => {
    const reg = modeler.get('elementRegistry');
    const flow = reg.filter(e => e.type === 'bpmn:SequenceFlow' && e.source.type === 'bpmn:UserTask')[0];
    modeler.get('modeling').removeConnection(flow);
  });
  await page.waitForFunction(() => document.querySelector('#tab-report').textContent.includes('Процесс обрывается'));
  assert.match(await page.locator('#diagram-details').innerText(), /ошибк/);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#insights-close').click();
  await page.locator('#insights-toggle').click();
  await page.locator('#right').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#right').isVisible(), true);
  const demoPage = await context.newPage();
  demoPage.on('pageerror', e => errors.push(e.message));
  await demoPage.goto(url);
  await demoPage.locator('#open-demo').click();
  await demoPage.locator('#diagram-status').waitFor({ state: 'visible' });
  assert.equal(await demoPage.locator('.source-fragment').count() > 0, true);
  if (process.env.SCREENSHOT_DIR) {
    await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
    await demoPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/desktop.png` });
  }
  await demoPage.locator('#insights-toggle').click();
  await demoPage.locator('.tabs [data-tab="coverage"]').click();
  assert.equal(await demoPage.locator('#process-audit').isVisible(), true);
  await demoPage.locator('[data-audit-view="branches"]').click();
  assert.equal(await demoPage.locator('.branch-card').count() > 0, true);
  if (process.env.SCREENSHOT_DIR) {
    await demoPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/audit.png` });
  }
  await demoPage.locator('#insights-close').click();
  await demoPage.locator('#tour-toggle').click();
  assert.match(await demoPage.locator('#tour-title').innerText(), /Начало/);
  await demoPage.locator('#tour-next').click();
  await demoPage.locator('#tour-next').click();
  await demoPage.locator('#tour-next').click();
  assert.equal(await demoPage.locator('#tour-choices button').count() >= 2, true);
  if (process.env.SCREENSHOT_DIR) {
    await demoPage.waitForTimeout(250); // let the analysis drawer finish its CSS exit transition
    await demoPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/tour.png` });
  }
  await demoPage.locator('#tour-choices button').first().click();
  assert.equal(await demoPage.locator('#tour-position').innerText(), 'Шаг 5');
  if (process.env.SCREENSHOT_DIR) {
    await demoPage.setViewportSize({ width: 390, height: 844 });
    await demoPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/mobile.png`, fullPage: true });
  }
  await demoPage.evaluate(() => window.processAudit.render({
    report: { cards: [] }, ranges: [],
    text: 'После проверки отправить клиенту решение в течение пяти рабочих дней.'
  }));
  assert.equal(await demoPage.locator('#audit-gaps .audit-card').count(), 1);
  await demoPage.close();
  assert.deepEqual(errors, []);
  console.log('PASS: interview, source highlight, search, cards, XSD, simulation, inspection, mobile UI, one-click demo');
} finally {
  await context.close();
  await browser.close();
}
