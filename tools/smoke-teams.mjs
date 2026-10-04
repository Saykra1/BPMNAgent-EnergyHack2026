// Accounts and teams smoke (server with login required):
//   REQUIRE_LOGIN=true UI_PORT=8095 python backend/tests/serve_ui_fixture.py
//   node tools/smoke-teams.mjs http://127.0.0.1:8095
// head registers, creates a team, configures a custom role and a project from the demo diagram;
// an administrator is invited by login, accepts, and can rename blocks but not change the structure
// or use the LLM; a third person declines an invitation by id; a member without roles sees nothing.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const url = process.argv[2] || 'http://127.0.0.1:8095';
const browser = process.env.BROWSER_CDP
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP)
  : await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];
const stamp = Date.now().toString(36).slice(-5);

async function person(login, name) {
  const context = await browser.newContext({ viewport: { width: 1450, height: 950 } });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(`${login}: ${e.message}`));
  page.on('dialog', d => d.accept(d.type() === 'prompt' ? d.defaultValue() : undefined));
  await page.goto(url);
  await page.locator('#auth-screen').waitFor();
  await page.locator('[data-mode="register"]').click();
  await page.fill('#auth-form [name=name]', name);
  await page.fill('#auth-form [name=email]', `${login}@example.com`);
  await page.fill('#auth-form [name=login]', login);
  await page.fill('#auth-form [name=password]', 'secret-pass-1');
  await page.fill('#auth-form [name=password2]', 'secret-pass-1');
  await page.locator('#auth-submit').click();
  await page.waitForFunction(() => window.session?.me && !document.getElementById('auth-screen'));
  return { page, context, login, id: await page.evaluate(() => window.session.me.user.id) };
}
const teamsOpen = (p) => p.page.locator('#teams-open').click();

try {
  // ---- unauthenticated: the API is closed, the login screen is shown
  const anon = await browser.newContext();
  const ap = await anon.newPage();
  await ap.goto(url);
  await ap.locator('#auth-screen').waitFor();
  assert.equal(await ap.evaluate(async () => (await fetch('/api/examples')).status), 401);
  await ap.locator('#auth-form [name=login]').fill('nobody');
  await ap.locator('#auth-form [name=password]').fill('wrong-pass');
  await ap.locator('#auth-submit').click();
  await ap.waitForFunction(() => /Неверный логин/.test(document.querySelector('#auth-error').textContent));
  await anon.close();

  const head = await person('head' + stamp, 'Глава Г.');
  const admin = await person('admin' + stamp, 'Админ А.');
  const third = await person('third' + stamp, 'Третий Т.');

  // ---- head: demo diagram, team, custom role, project from the canvas
  const hp = head.page;
  await hp.locator('#open-demo').click();
  await hp.waitForFunction(() => state.hasDiagram);
  await teamsOpen(head);
  await hp.fill('#team-new-name', 'Сетевая компания');
  await hp.locator('#team-create button').click();
  await hp.waitForFunction(() => /создан/.test(document.querySelector('#team-status')?.textContent || ''));
  assert.match(await hp.locator('.team-view').innerText(), /Разработчик[\s\S]*Администратор[\s\S]*Наблюдатель/);
  await hp.locator('[data-role-new]').click();
  await hp.fill('#role-name', 'Юрист');
  await hp.locator('input[name=perm][value=run]').check();
  assert.equal(await hp.locator('input[name=perm][value=view]').isChecked(), true);   // run implies view
  await hp.locator('.be-save').click();
  await hp.waitForFunction(() => /Юрист/.test(document.querySelector('.team-view').textContent));
  await hp.locator('[data-tab=projects]').click();
  await hp.fill('#project-new', 'Техприсоединение');
  await hp.locator('#project-create button').click();
  await hp.waitForFunction(() => window.session.project?.title === 'Техприсоединение' && window.session.project.version === 2);

  // ---- invitations: administrator by login, third person by personal id
  await teamsOpen(head);
  await hp.locator('[data-tab=invites]').click();
  await hp.fill('#invite-who', admin.login);
  const adminRole = await hp.evaluate(() => [...document.querySelectorAll('input[name="inv-role"]')].find(i => i.closest('label').textContent.includes('Администратор')).value);
  await hp.locator(`input[name="inv-role"][value="${adminRole}"]`).check();
  await hp.locator('#invite-form button[type=submit]').click();
  await hp.waitForFunction(() => /Приглашение отправлено/.test(document.querySelector('#team-status').textContent));
  await hp.fill('#invite-who', '#' + third.id.slice(0, 4) + '-' + third.id.slice(4));
  await hp.locator('#invite-form button[type=submit]').click();
  await hp.waitForFunction(() => document.querySelectorAll('[data-cancel-invite]').length === 2);

  // third declines
  await third.page.reload();
  await third.page.waitForFunction(() => window.session?.me);
  assert.match(await third.page.locator('#teams-open').innerText(), /1/);
  await teamsOpen(third);
  await third.page.locator('[data-decline]').click();
  await third.page.waitForFunction(() => /отклонено/.test(document.querySelector('#team-status').textContent));
  assert.equal(await third.page.locator('[data-team]').count(), 0);

  // administrator accepts and opens the project
  const pp = admin.page;
  await pp.reload();
  await pp.waitForFunction(() => window.session?.me);
  await teamsOpen(admin);
  await pp.locator('[data-accept]').click();
  await pp.waitForFunction(() => document.querySelector('.team-view'));
  assert.match(await pp.locator('.team-view').innerText(), /Администратор/);
  await pp.locator('[data-open]').click();
  await pp.waitForFunction(() => window.session.project && state.hasDiagram);
  // structure is locked: no palette, generation off, structural commands are refused
  assert.equal(await pp.evaluate(() => document.body.classList.contains('lock-structure')), true);
  assert.equal(await pp.locator('#generate').isDisabled(), true);
  const before = await pp.evaluate(() => modeler.get('elementRegistry').getAll().length);
  await pp.evaluate(() => { const t = modeler.get('elementRegistry').filter(e => /Task$/.test(e.type))[0]; modeler.get('modeling').removeElements([t]); });
  assert.equal(await pp.evaluate(() => modeler.get('elementRegistry').getAll().length), before);
  // the server refuses a structural change even if the UI is bypassed
  const forced = await pp.evaluate(async () => {
    const xml = (await modeler.saveXML()).xml.replace(/<bpmn:sequenceFlow id="[^"]+"[^>]*\/>/, '');
    const r = await fetch('/api/projects/' + window.session.project.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: window.session.project.version, xml, text: document.querySelector('#text').value }) });
    return [r.status, (await r.json()).detail];
  });
  assert.equal(forced[0], 403, JSON.stringify(forced));
  // and refuses the LLM inside the project
  const gen = await pp.evaluate(async () => (await fetch('/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'Процесс согласования договора' }) })).status);
  assert.equal(gen, 403);
  // renaming through the block dialog is allowed: type and role are locked, name is not
  await pp.evaluate(() => { const t = modeler.get('elementRegistry').filter(e => e.businessObject.name === 'Проверить комплектность документов')[0]; modeler.get('selection').select(t); });
  await pp.locator('.be-gear').click();
  assert.equal(await pp.locator('#be-type').isDisabled(), true);
  assert.equal(await pp.locator('#be-role').isDisabled(), true);
  assert.equal(await pp.locator('#be-name').isDisabled(), false);
  await pp.fill('#be-name', 'Проверить документы заявителя');
  await pp.fill('#be-desc', 'Сверка по перечню из регламента');
  await pp.locator('.be-save').click();
  await pp.locator('#project-save').click();
  await pp.waitForFunction(() => window.session.project.version === 3);

  // head reloads the project and sees the administrator's edit
  await hp.locator('#project-reload').click();
  await hp.waitForFunction(() => modeler.get('elementRegistry').filter(e => e.businessObject.name === 'Проверить документы заявителя').length === 1);

  // ---- a member without roles sees nothing; then the head gives the custom role
  await teamsOpen(head);
  await hp.locator('[data-tab=invites]').click();
  await hp.fill('#invite-who', third.login);
  await hp.locator('#invite-form button[type=submit]').click();
  await hp.waitForFunction(() => /Приглашение отправлено/.test(document.querySelector('#team-status').textContent));
  await third.page.reload();
  await third.page.waitForFunction(() => window.session?.me);
  await teamsOpen(third);
  await third.page.locator('[data-accept]').click();
  await third.page.waitForFunction(() => /нет ролей/.test(document.querySelector('.team-view')?.textContent || ''));
  assert.equal(await third.page.locator('[data-open]').count(), 0);
  await hp.locator('[data-tab=members]').click();
  await hp.locator(`[data-member-roles="${third.id}"]`).click();
  const lawyerRole = await hp.evaluate(() => [...document.querySelectorAll('input[name="mr"]')].find(i => i.closest('label').textContent.includes('Юрист')).value);
  await hp.locator(`input[name="mr"][value="${lawyerRole}"]`).check();
  await hp.locator('.be-save').click();
  await hp.waitForFunction(() => /Юрист/.test(document.querySelector('.team-view').textContent));
  await third.page.locator('[data-team]').click();
  await third.page.waitForFunction(() => document.querySelectorAll('[data-open]').length === 1);

  assert.deepEqual(errors, []);
  console.log('teams smoke OK');
} finally {
  await browser.close();
}
