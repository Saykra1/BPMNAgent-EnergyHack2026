// Block properties smoke: gear on selection → dialog → new role + performer + all fields → saved XML,
// round trip through /api/ir. Run with the fixture server: node tools/smoke-block-editor.mjs http://127.0.0.1:8094
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const url = process.argv[2] || 'http://127.0.0.1:8094';
const browser = process.env.BROWSER_CDP
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP)
  : await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', d => d.accept());
try {
  await page.goto(url);
  await page.locator('#open-demo').click();
  await page.waitForFunction(() => state.hasDiagram && modeler.get('elementRegistry').filter(e => e.type === 'bpmn:UserTask' || e.type === 'bpmn:Task').length > 0);
  const id = await page.evaluate(() => {
    const t = modeler.get('elementRegistry').filter(e => /Task$/.test(e.type) && e.type !== 'bpmn:SubProcess')[0];
    modeler.get('selection').select(t);
    return t.id;
  });
  // gear shows on selection, opens the dialog
  await page.locator('.be-gear').click();
  await page.locator('.be-dialog').waitFor();
  await page.fill('#be-name', 'Проверить комплект документов');
  await page.selectOption('#be-type', 'bpmn:ManualTask');
  await page.fill('#be-desc', 'Сверить с перечнем.\nПроверить подписи.');
  await page.selectOption('#be-role', '__new');
  await page.fill('#be-newrole', 'Юрист');
  await page.selectOption('#be-person', '__new');
  await page.fill('#be-p-name', 'Петров П. П.');
  await page.fill('#be-p-pos', 'ведущий юрист');
  await page.fill('#be-deadline', '3 рабочих дня с получения заявки');
  await page.fill('#be-sla', '24');
  await page.fill('#be-dur', '45');
  await page.fill('#be-wait', '120');
  await page.check('#be-est');
  await page.fill('#be-docs', 'Заявка\nДоверенность');
  await page.fill('#be-assumption', 'Срок уточнить у заказчика');
  await page.locator('.be-save').click();
  assert.equal(await page.locator('#be-modal').count(), 0, await page.locator('.be-error').innerText().catch(() => ''));

  const info = await page.evaluate(() => {
    const el = modeler.get('elementRegistry').filter(e => e.businessObject.name === 'Проверить комплект документов')[0];
    const lane = modeler.get('elementRegistry').filter(e => e.type === 'bpmn:Lane' && (e.businessObject.flowNodeRef || []).includes(el.businessObject))[0];
    return { type: el.type, id: el.id, lane: lane?.businessObject.name, details: blockEditor.readDetails(el.businessObject),
      desc: blockEditor.readDescription(el.businessObject), people: blockEditor.readPeople() };
  });
  assert.equal(info.type, 'bpmn:ManualTask');
  assert.equal(info.lane, 'Юрист');
  assert.equal(info.desc, 'Сверить с перечнем.\nПроверить подписи.');
  assert.equal(info.details.sla_hours, 24);
  assert.equal(info.details.duration_min, 45);
  assert.deepEqual(info.details.documents, ['Заявка', 'Доверенность']);
  assert.equal(info.people.length, 1);
  assert.equal(info.people[0].name, 'Петров П. П.');
  assert.equal(info.details.performer, info.people[0].id);

  // directory: rename the performer, it stays assigned
  await page.evaluate(() => blockEditor.openDirectory());
  await page.locator('#d-people .d-name').first().fill('Петров Пётр');
  await page.locator('.be-save').click();
  assert.equal(await page.evaluate(() => blockEditor.readPeople()[0].name), 'Петров Пётр');

  // gateway branches: set a probability and the default flow
  const gw = await page.evaluate(() => {
    const g = modeler.get('elementRegistry').filter(e => e.type === 'bpmn:ExclusiveGateway' && e.outgoing.length > 1)[0];
    modeler.get('selection').select(g); return g.id;
  });
  await page.locator('.be-gear').click();
  await page.locator('[data-prob]').first().fill('0.3');
  await page.locator('input[name="be-default"]').nth(1).check();
  await page.locator('.be-save').click();
  assert.equal(await page.locator('#be-modal').count(), 0, await page.locator('.be-error').innerText().catch(() => ''));

  // the XML survives the server round trip (BPMN → IR) with the edited data
  const xml = await page.evaluate(async () => (await modeler.saveXML({ format: true })).xml);
  assert.match(xml, /BPMN_AGENT_PERFORMERS/);
  const res = await page.evaluate(async (xml) => (await fetch('/api/ir', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ xml }) })).json(), xml);
  const plan = res.plan || res;
  const step = plan.elements.find(e => e.name === 'Проверить комплект документов');
  assert.ok(step, JSON.stringify(res).slice(0, 400));
  assert.equal(step.type, 'manual_task');
  assert.equal(step.description, 'Сверить с перечнем.\nПроверить подписи.');
  assert.equal(step.sla_hours, 24);
  assert.equal(plan.performers[0].name, 'Петров Пётр');
  assert.equal(step.performer, plan.performers[0].id);
  assert.equal(plan.participants.find(p => p.id === step.participant)?.name, 'Юрист');
  assert.ok(plan.flows.some(f => f.from === gw && f.probability === 0.3), 'branch probability kept');

  // undo works through the command stack
  await page.evaluate(() => modeler.get('commandStack').undo());
  assert.deepEqual(errors, []);
  console.log('block editor smoke OK', id, '→', step.id);
} finally {
  await context.close();
  await browser.close();
}
