// Process run smoke: in the block dialog give a human step a data field, turn the next step into a
// service task with code and a document template, give the gateway checks; then run the process:
// the person fills data + document + file, the code runs, the gateway takes the checked branch,
// documents can be downloaded. Run with the fixture server: node tools/smoke-process-run.mjs http://127.0.0.1:8094
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const url = process.argv[2] || 'http://127.0.0.1:8094';
const browser = process.env.BROWSER_CDP
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP)
  : await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1500, height: 950 }, acceptDownloads: true });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', d => d.accept());
const select = (name) => page.evaluate((name) => {
  const el = modeler.get('elementRegistry').filter(e => e.businessObject.name === name && !e.labelTarget)[0];
  modeler.get('selection').select(el); return el.id;
}, name);
const saveDialog = async () => {
  await page.locator('.be-save').click();
  assert.equal(await page.locator('#be-modal').count(), 0, await page.locator('.be-error').innerText().catch(() => ''));
};
try {
  await page.goto(url);
  await page.evaluate(() => { try { localStorage.clear(); } catch { /* ignore */ } });
  await page.locator('#open-demo').click();
  await page.waitForFunction(() => state.hasDiagram);

  // 1. human step asks for data
  await select('Подать заявку на присоединение');
  await page.locator('.be-gear').click();
  assert.equal(await page.locator('#be-fields').isVisible(), true);
  assert.equal(await page.locator('#be-code').isVisible(), false);
  await page.fill('#be-fields', 'мощность_квт');
  await page.fill('#be-report', 'Укажите номер заявки и приложите скан');
  await saveDialog();

  // 2. the check becomes automatic, with code and a document template; try it on test data first
  await select('Проверить комплектность документов');
  await page.locator('.be-gear').click();
  await page.selectOption('#be-type', 'bpmn:ServiceTask');
  assert.equal(await page.locator('#be-code').isVisible(), true);
  await page.fill('#be-code', 'документы_полные = мощность_квт <= 150\nlog(f"мощность {мощность_квт} кВт")');
  await page.fill('#be-report', 'Комплектность проверена автоматически: {документы_полные}');
  await page.fill('#be-testdata', '{"мощность_квт": 100}');
  await page.locator('#be-try').click();
  await page.waitForFunction(() => /документы_полные/.test(document.querySelector('#be-try-out').textContent));
  assert.match(await page.locator('#be-try-out').innerText(), /Комплектность проверена автоматически: да/);
  await page.fill('#be-code', 'документы_полные = (');       // a syntax error is reported, not saved silently
  await page.locator('#be-try').click();
  await page.waitForFunction(() => document.querySelector('#be-try-out').classList.contains('bad'));
  await page.fill('#be-code', 'документы_полные = мощность_квт <= 150\nlog(f"мощность {мощность_квт} кВт")');
  await saveDialog();

  // 3. gateway checks: «Да» by the check, «Нет» is the default branch
  const gw = await page.evaluate(() => {
    const t = modeler.get('elementRegistry').filter(e => e.businessObject.name === 'Проверить комплектность документов')[0];
    const g = t.outgoing[0].target; modeler.get('selection').select(g); return g.id;
  });
  await page.locator('.be-gear').click();
  const rows = await page.locator('[data-cond]').evaluateAll(xs => xs.map(x => x.value));
  const yes = rows.findIndex(v => /да/i.test(v)), no = rows.findIndex(v => /нет/i.test(v));
  assert.ok(yes >= 0 && no >= 0, JSON.stringify(rows));
  await page.locator('[data-flowcheck]').nth(yes).fill('документы_полные');
  await page.locator('input[name="be-default"]').nth(no).check();
  await page.fill('#be-gw-testdata', '{"документы_полные": true}');
  await page.locator('#be-gw-try').click();
  await page.waitForFunction(() => /ДА/.test(document.querySelector('#be-gw-out').textContent));
  await saveDialog();

  // 4. run the process
  await page.evaluate(() => processRun.open());
  await page.fill('#run-vars', '{}');
  await page.locator('#run-start').click();
  const task = page.locator('.run-task').first();
  await task.waitFor();
  assert.match(await task.innerText(), /Подать заявку/);
  // the person must give data and a document or a file
  await task.locator('button[type=submit]').click();
  assert.match(await page.locator('#run-err').innerText(), /Заполните/);
  await task.locator('[data-field="мощность_квт"]').fill('120');
  await task.locator('.run-text-in').fill('Заявка №15 подана через личный кабинет');
  await task.locator('.run-file').setInputFiles({ name: 'скан_заявки.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') });
  await task.locator('button[type=submit]').click();
  await page.waitForFunction(() => processRun.get()?.documents.length >= 2);
  const r = await page.evaluate(() => processRun.get());
  assert.equal(r.variables['мощность_квт'], 120);
  assert.equal(r.variables['документы_полные'], true);
  const auto = r.documents.find(d => d.kind === 'auto');
  assert.equal(auto.text, 'Комплектность проверена автоматически: да');
  assert.deepEqual(auto.logs, ['мощность 120 кВт']);
  assert.equal(r.documents.find(d => d.kind === 'human').attachment.name, 'скан_заявки.pdf');
  assert.ok(r.history.some(h => h.node === gw && h.kind === 'chosen' && /Да/.test(h.text)), 'gateway took «Да» by its check');
  assert.ok(r.waiting.length >= 1 && r.status === 'waiting', r.status);
  assert.equal(await page.locator('.djs-element.run-waiting').count() >= 1, true);
  // documents download: real DOCX / ZIP files with the right names
  const files = await page.evaluate(async () => {
    const links = [...document.querySelectorAll('.run-docs a[download], a[href$="/archive"]')];
    return Promise.all(links.map(async a => {
      const r = await fetch(a.href); const b = new Uint8Array(await r.arrayBuffer());
      return { name: a.getAttribute('download'), magic: String.fromCharCode(b[0], b[1]), size: b.length,
               disposition: r.headers.get('content-disposition') };
    }));
  });
  const docx = files.filter(f => f.name.endsWith('.docx'));
  assert.ok(docx.length >= 2 && docx.every(f => f.magic === 'PK'), JSON.stringify(files));
  assert.ok(files.some(f => f.name === 'скан_заявки.pdf' && f.magic === '%P'));
  assert.ok(files.some(f => f.name.endsWith('.zip') && f.magic === 'PK'));
  assert.deepEqual(errors, []);
  console.log('process run smoke OK:', r.documents.map(d => d.file).join(', '));
} finally {
  await context.close();
  await browser.close();
}
