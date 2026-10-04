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
  return (async () => {
    if (preparations === 1) await new Promise(resolve => setTimeout(resolve, 1200));
    await route.fulfill({ json: { ok: true, plan: reviewed } });
  })();
});
try {
  await page.goto(url);
  const introStarted = Date.now();
  await page.locator('#intro-screen').waitFor({ state: 'visible' });
  if (process.env.SCREENSHOT_DIR) {
    await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
    await page.waitForTimeout(650); // capture the completed entrance, before automatic dismissal
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/intro.png` });
  }
  await page.locator('#intro-screen').waitFor({ state: 'detached' });
  assert.equal(Date.now() - introStarted >= 1200, true);
  await page.reload();
  assert.equal(await page.locator('#intro-screen').count(), 0);
  const quizAudit = await page.evaluate(() => {
    window.waitQuiz.start();
    const seen = new Set();
    for (let i = 0; i < 35; i++) {
      seen.add(document.querySelector('#wait-quiz-question').textContent);
      const buttons = [...document.querySelectorAll('.wait-quiz-option')];
      if (buttons.length !== 3) throw new Error(`Question ${i + 1} does not have three options`);
      buttons[0].click();
      if (document.querySelectorAll('.wait-quiz-option.is-correct').length !== 1) throw new Error(`Question ${i + 1} has no unique answer`);
      if (!document.querySelector('#wait-quiz-feedback a')?.href.startsWith('https://')) throw new Error(`Question ${i + 1} has no source`);
      document.querySelector('#wait-quiz-next').click();
    }
    return { unique: seen.size, progress: document.querySelector('#wait-quiz-progress').textContent };
  });
  assert.equal(quizAudit.unique, 35);
  assert.match(quizAudit.progress, /1 \/ 35/);
  assert.equal(await page.locator('#right').isVisible(), false);
  assert.equal(await page.locator('#open-demo').isVisible(), true);
  assert.equal(await page.locator('#dl-bpmn').isDisabled(), true);
  assert.equal(await page.locator('#chat-box').isVisible(), false);
  await page.locator('#open-demo').focus();
  assert.match(await page.locator('#help-popover').innerText(), /редактируемый пример/);
  await page.locator('#generate').hover();
  assert.match(await page.locator('#help-popover').innerText(), /редактируемую BPMN-схему/);
  await page.locator('#text').fill(text);
  assert.equal(await page.locator('#mode').inputValue(), 'ir');
  assert.match(await page.locator('#mode-name').innerText(), /Без уточнений/);
  await page.locator('.generation-settings summary').click();
  await page.locator('[data-mode-choice="direct"]').hover();
  assert.match(await page.locator('#help-popover').innerText(), /пропуская отдельный план/);
  if (process.env.SCREENSHOT_DIR) {
    await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/mode-picker.png` });
  }
  await page.locator('[data-mode-choice="direct"]').click();
  assert.equal(await page.locator('#mode').inputValue(), 'direct');
  await page.locator('.generation-settings summary').click();
  await page.locator('[data-mode-choice="guided"]').click();
  assert.equal(await page.locator('#mode').inputValue(), 'guided');
  assert.match(await page.locator('#mode-name').innerText(), /С уточнениями/);
  await page.locator('#generate').click();
  await page.locator('#loading').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#loading').isVisible(), true);
  assert.equal(await page.locator('.wait-quiz-option').count(), 3);
  await page.locator('.wait-quiz-option').first().click();
  assert.equal(await page.locator('#wait-quiz-feedback').isVisible(), true);
  assert.match(await page.locator('#wait-quiz-feedback').innerText(), /Источник факта/);
  if (process.env.SCREENSHOT_DIR) {
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/wait-quiz.png` });
  }
  await page.locator('#wait-quiz-next').click();
  assert.match(await page.locator('#wait-quiz-progress').innerText(), /2 \/ 35/);
  await page.locator('#answer-0').fill('Заявку закрывают и уведомляют заявителя.');
  assert.equal(await page.locator('#loading').isVisible(), false);
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
  assert.equal(await page.locator('[data-element-id="supply"]').evaluate(e => e.classList.contains('nav-search')), true);
  assert.equal(await page.locator('#participant-filter option').count() > 1, true);
  await page.locator('#participant-filter').selectOption({ index: 1 });
  assert.equal(await page.locator('.nav-participant-hit').count() > 0, true);
  await page.locator('#search-results button').first().click();
  assert.equal(await page.locator('#navigator').isVisible(), false);
  await page.locator('#nav-focus-clear').click();
  await page.locator('.source-fragment').first().hover();
  const linkedFill = await page.locator('[data-element-id="supply"] .djs-visual > :first-child')
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
  await page.locator('[data-element-id="supply"] .djs-hit').click();
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
  let reviewedBranches = 0;
  await demoPage.route('**/api/jev-audit', route => {
    const request = route.request().postDataJSON();
    reviewedBranches = request.branches.length;
    return route.fulfill({ json: { ok: true, gaps: [],
      branches: request.branches.map(item => ({ id: item.id, support: 0.72 })) } });
  });
  await demoPage.locator('[data-audit-view="gaps"]').click();
  await demoPage.locator('#jev-audit').click();
  await demoPage.waitForFunction(() => document.querySelector('#jev-audit-result')?.textContent.includes('Jev проверил'));
  assert.equal(await demoPage.locator('[data-audit-view="branches"]').getAttribute('class'), 'active');
  assert.equal(await demoPage.locator('#audit-branches .audit-score').count(), reviewedBranches);
  assert.match(await demoPage.locator('#jev-audit-result').innerText(), /во вкладке «Развилки»/);
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
  const compact = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const compactPage = await compact.newPage();
  await compactPage.goto(url);
  assert.equal(await compactPage.locator('#intro-screen').isVisible(), true);
  assert.equal(await compactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.SCREENSHOT_DIR) {
    await compactPage.waitForTimeout(650); // capture the completed entrance on mobile too
    await compactPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/intro-mobile.png` });
  }
  await compactPage.locator('#intro-skip').click();
  await compactPage.locator('#intro-screen').waitFor({ state: 'detached' });
  await compactPage.evaluate(() => busy(true, 'Ассистент строит схему…'));
  await compactPage.locator('#loading').waitFor({ state: 'visible' });
  assert.equal(await compactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.SCREENSHOT_DIR) {
    await compactPage.waitForTimeout(350);
    await compactPage.screenshot({ path: `${process.env.SCREENSHOT_DIR}/wait-quiz-mobile.png` });
  }
  await compactPage.evaluate(() => busy(false));
  await compact.close();
  const reduced = await browser.newContext({ reducedMotion: 'reduce' });
  const reducedPage = await reduced.newPage();
  await reducedPage.goto(url);
  assert.equal(await reducedPage.locator('#intro-screen').count(), 0);
  await reduced.close();
  assert.deepEqual(errors, []);
  console.log('PASS: intro, modes, interview, source highlight, search, cards, XSD, simulation, inspection, mobile UI, one-click demo');
} finally {
  await context.close();
  await browser.close();
}
