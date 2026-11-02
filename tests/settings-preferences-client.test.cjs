'use strict';
// Self-contained Puppeteer verification: disposable browser contexts, real CSS,
// and one mocked read-only registry endpoint. No running CCC instance required.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const ROOT = path.resolve(__dirname, '..');
const SCRIPT = fs.readFileSync(path.join(ROOT, 'static/settings-preferences.js'));
const CSS = fs.readFileSync(path.join(ROOT, 'static/app.css'));
const CATALOG = JSON.parse(execFileSync('python3', ['-c', 'import json; from ccc_server.settings_preferences import registry, factory_defaults; print(json.dumps(dict(registry=registry(),defaults=factory_defaults())))'], {cwd: ROOT, encoding: 'utf8'}));
const HISTORY = 'ccc-preferences-history-v1';
const PROFILE = values => ({format: 'ccc-preferences', version: 1, values});
let browser, server, baseURL;
const requests = [];
const screenshotDir = fs.mkdtempSync(path.join(os.tmpdir(), 'preferences-browser-'));

function fixture() {
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><style>body{margin:0}.settings-modal-overlay{display:flex!important}.settings-modal{width:min(100%,1000px);max-height:100vh}#settingsPane{overflow-y:auto}</style></head><body>
<div class="settings-modal-overlay open" id="settingsModal"><div class="settings-modal"><div class="settings-modal-header">Settings</div><div class="settings-modal-body">
<nav class="settings-rail" id="settingsRail" role="tablist" aria-label="Settings"><div class="settings-rail-group">Workspace</div><button type="button" class="settings-rail-item" data-section-target="appearance">Appearance</button><button type="button" class="settings-rail-item" data-section-target="voice">Voice &amp; sound</button><div class="settings-rail-group" id="agentsGroup">Agents &amp; automation</div></nav>
<main class="settings-pane" id="settingsPane"><section class="settings-section is-active-section" data-section-id="appearance">Appearance fixture</section></main></div></div></div>
<script>
window.__events=[];window.addEventListener('ccc:preferences-applied',event=>window.__events.push(event.detail));
document.getElementById('settingsRail').addEventListener('click',event=>{const tab=event.target.closest('[data-section-target]');if(!tab)return;document.querySelectorAll('.settings-section').forEach(section=>{const active=section.dataset.sectionId===tab.dataset.sectionTarget;section.classList.toggle('is-active-section',active);section.setAttribute('aria-hidden',String(!active))});document.querySelectorAll('.settings-rail-item').forEach(item=>item.setAttribute('aria-selected',String(item===tab)))});
</script><script src="/static/settings-preferences.js"></script></body></html>`;
}

test.before(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/static/settings-preferences.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(SCRIPT); }
    if (url.pathname === '/static/app.css') { res.setHeader('Content-Type', 'text/css'); return res.end(CSS); }
    if (url.pathname === '/api/settings/preferences/registry') {
      requests.push({method: req.method, path: url.pathname});
      const mode = req.headers.cookie || '';
      res.setHeader('Content-Type', 'application/json');
      if (mode.includes('failure=1')) {res.statusCode = 503; return res.end('{}');}
      const data = structuredClone(CATALOG);
      if (mode.includes('poison=1')) data.registry.theme.source_key = 'private-token';
      return res.end(JSON.stringify(data));
    }
    if (url.pathname === '/') {res.setHeader('Content-Type', 'text/html'); return res.end(fixture());}
    res.statusCode = 404; res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseURL = 'http://127.0.0.1:' + server.address().port;
  browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
});
test.after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
  assert(requests.length > 0);
  assert(requests.every(request => request.method === 'GET' && request.path === '/api/settings/preferences/registry'));
  console.log('Preference screenshots:', screenshotDir);
});

async function open(t, seed = {}, options = {}) {
  const context = await browser.createBrowserContext();
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  const errors = []; page.on('pageerror', error => errors.push(String(error)));
  t.after(async () => {await context.close(); assert.deepEqual(errors, []);});
  if (options.cookie) await page.setCookie({name: options.cookie, value: '1', url: baseURL});
  await page.setViewport(options.viewport || {width: 1180, height: 1000});
  await page.evaluateOnNewDocument(seed => {for (const [key, value] of Object.entries(seed)) localStorage.setItem(key, value);}, seed);
  await page.goto(baseURL, {waitUntil: 'load'});
  await page.waitForSelector('#settingsRailTab-preferences');
  await page.click('#settingsRailTab-preferences');
  if (!options.cookie) await waitStatus(page, 'Ready.');
  else await waitStatus(page, 'Could not load');
  return page;
}
async function waitStatus(page, text) {
  await page.waitForFunction(text => document.querySelector('[data-pref-status]')?.textContent.includes(text), {}, text);
}
async function action(page, name) { await page.click(`[data-pref-action="${name}"]`); }
async function editor(page, profile) {
  await page.$eval('textarea', (element, text) => {element.value = text; element.dispatchEvent(new Event('input', {bubbles: true}));}, typeof profile === 'string' ? profile : JSON.stringify(profile));
}
async function importPreview(page, values) {
  await editor(page, PROFILE(values)); await action(page, 'import');
  await page.waitForSelector('[data-pref-preview]:not([hidden])');
}
async function apply(page, operation) {
  await action(page, 'apply'); await waitStatus(page, `Applied ${operation}`);
}
async function storage(page) {
  return page.evaluate(() => Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])));
}

test('mounts once under Workspace, labels scope and supported preferences, detects actual encoded changes', async t => {
  const page = await open(t, {'ccc-theme': 'light', 'ccc-separate-tabs': 'on', 'ccc-sounds-enabled': '0'});
  assert.equal(await page.$eval('#settingsRailTab-preferences', element => element.nextElementSibling.id), 'agentsGroup');
  assert.match(await page.$eval('.ccc-prefs', element => element.textContent), /This browser\./);
  assert.equal(await page.$$eval('[data-pref-changed] tbody tr', rows => rows.length), 3);
  assert.match(await page.$eval('[data-pref-changed]', element => element.textContent), /Separate issue and queue tabs/);
  await page.evaluate(() => {window.cccSettingsPreferences.mount(); window.cccSettingsPreferences.mount();});
  assert.equal(await page.$$eval('#settingsSection-preferences', elements => elements.length), 1);
  assert.equal(await page.$eval('[data-pref-action="undo"]', element => element.disabled), true);
  await page.click('summary');
  assert.match(await page.$eval('[data-pref-supported]', element => element.textContent), /Worker row density/);
});

test('exports only registered browser values, never arbitrary storage, server keys, or history', async t => {
  const page = await open(t, {'ccc-theme': 'light', 'private-token': 'sk-ant-test-XXXX', 'repo-path': '/example/project', 'ccc-wrap-titles': '1'});
  await page.evaluate(() => {const original = URL.createObjectURL; URL.createObjectURL = blob => {blob.text().then(text => {window.__blob = text;}); return original(blob);};});
  await action(page, 'export'); await waitStatus(page, 'Exported');
  await page.waitForFunction(() => Boolean(window.__blob));
  const exported = JSON.parse(await page.evaluate(() => window.__blob));
  assert.deepEqual(Object.keys(exported).sort(), ['format', 'values', 'version']);
  assert.equal(Object.keys(exported.values).length, 12);
  assert.equal(exported.values.theme, 'light'); assert.equal(exported.values.wrap_titles, true);
  assert.equal(exported.values.auto_compact_k, undefined);
  assert(!JSON.stringify(exported).includes('sk-ant-test'));
  assert(!JSON.stringify(exported).includes('/example'));
  assert.equal(await page.$eval('textarea', element => element.value), JSON.stringify(exported, null, 2));
  assert.equal((await storage(page))[HISTORY], undefined);
});

test('import preview is read-only, cancellation is safe, apply writes real encodings and emits one host event', async t => {
  const page = await open(t, {'unrelated': 'preserve'});
  const proposed = {theme: 'light', separate_tabs: true, queue_rhs_list: false, sounds_enabled: false, compact_rows: false, wrap_titles: true, voice_mobile_button: false};
  await importPreview(page, proposed);
  assert.deepEqual(await storage(page), {unrelated: 'preserve'});
  assert.match(await page.$eval('[data-pref-preview]', element => element.textContent), /Before.*After/);
  await action(page, 'cancel');
  assert.equal(await page.$eval('[data-pref-preview]', element => element.hidden), true);
  await importPreview(page, proposed); await apply(page, 'import');
  const saved = await storage(page);
  for (const [key, value] of Object.entries({'ccc-theme': 'light', 'ccc-separate-tabs': 'on', 'ccc-queue-rhs-list': 'off', 'ccc-sounds-enabled': '0', 'ccc-compact-rows': '0', 'ccc-wrap-titles': '1', 'ccc-voice-mobile-btn': '0', unrelated: 'preserve'})) assert.equal(saved[key], value);
  const history = JSON.parse(saved[HISTORY]);
  assert.equal(history.sequence, 1); assert.equal(history.entries[0].operation, 'import');
  assert.equal(history.entries[0].after.separate_tabs, true);
  assert(!saved[HISTORY].includes('unrelated'));
  const events = await page.evaluate(() => window.__events);
  assert.equal(events.length, 1);
  assert.equal(events[0].operation, 'import');
  assert.deepEqual(events[0].keys.sort(), Object.keys(proposed).sort());
});

test('invalid profiles fail before writes, including mixed valid/invalid, duplicates and server keys', async t => {
  const page = await open(t, {'ccc-theme': 'dark'});
  const invalid = [PROFILE({theme: 'light', api_key: 'sk-ant-test-XXXX'}), PROFILE({auto_compact_k: 500}), PROFILE({sounds_enabled: '0'}), PROFILE({theme: null}), PROFILE({theme: '<img src=x onerror=alert(1)>'}), {...PROFILE({}), machine: 'example'}, {...PROFILE({}), version: true}, PROFILE({__proto__: null, constructor: 'bad'}), '{bad json', '{"format":"ccc-preferences","version":1,"values":{"theme":"dark","th\\u0065me":"light"}}', ' '.repeat(65537)];
  for (const profile of invalid) {
    await editor(page, profile); await action(page, 'import');
    await page.waitForSelector('[data-pref-status].is-error');
    assert.deepEqual(await storage(page), {'ccc-theme': 'dark'});
    assert.equal(await page.$eval('[data-pref-preview]', element => element.hidden), true);
    assert(!await page.$eval('[data-pref-status]', element => element.textContent.includes('sk-ant-test')));
  }
});

test('reset previews removal of supported overrides only; undo restores presence and redo works', async t => {
  const page = await open(t, {'ccc-theme': 'dark', 'ccc-queue-rhs-list': 'on', 'private-token': 'sk-ant-test-XXXX', 'ccc-session-ids': '["example"]'});
  await action(page, 'reset'); await page.waitForSelector('[data-pref-preview]:not([hidden])');
  assert.equal((await storage(page))['ccc-theme'], 'dark');
  assert.match(await page.$eval('[data-pref-preview]', element => element.textContent), /dark \(saved\).*dark \(default\)/);
  await apply(page, 'reset');
  let saved = await storage(page);
  assert.equal(saved['ccc-theme'], undefined); assert.equal(saved['ccc-queue-rhs-list'], undefined);
  assert.equal(saved['private-token'], 'sk-ant-test-XXXX');
  await action(page, 'undo'); await page.waitForSelector('[data-pref-preview]:not([hidden])'); await apply(page, 'undo');
  saved = await storage(page);
  assert.equal(saved['ccc-theme'], 'dark'); assert.equal(saved['ccc-queue-rhs-list'], 'on');
  await action(page, 'undo'); await page.waitForSelector('[data-pref-preview]:not([hidden])'); await apply(page, 'undo');
  assert.equal((await storage(page))['ccc-theme'], undefined);
  assert.equal(JSON.parse((await storage(page))[HISTORY]).entries.length, 3);
});

test('stale previews reject same-tab, other-tab and history changes without overwriting live values', async t => {
  const page = await open(t);
  await importPreview(page, {theme: 'light'});
  await page.evaluate(() => localStorage.setItem('ccc-theme', 'system'));
  await action(page, 'apply'); await waitStatus(page, 'Preferences changed since');
  assert.equal((await storage(page))['ccc-theme'], 'system'); assert.equal((await storage(page))[HISTORY], undefined);
  await importPreview(page, {font: 'mono'});
  const peer = await page.browserContext().newPage();
  await peer.goto(baseURL, {waitUntil: 'load'});
  await peer.evaluate(() => localStorage.setItem('ccc-wrap-titles', '1'));
  await action(page, 'apply'); await waitStatus(page, 'Preferences changed since');
  assert.equal((await storage(page))['ccc-font'], undefined);
  await peer.close();
  await importPreview(page, {font: 'mono'});
  await page.evaluate(key => localStorage.setItem(key, JSON.stringify({version: 1, sequence: 0, entries: []})), HISTORY);
  await action(page, 'apply'); await waitStatus(page, 'Preferences changed since');
  assert.equal((await storage(page))['ccc-font'], undefined);
});

test('outside edits block latest undo but allow a new transaction with a fresh baseline', async t => {
  const page = await open(t);
  await importPreview(page, {theme: 'light'}); await apply(page, 'import');
  await page.evaluate(() => localStorage.setItem('ccc-font', 'mono'));
  await action(page, 'undo'); await waitStatus(page, 'Preferences changed outside');
  assert.equal((await storage(page))['ccc-font'], 'mono');
  await importPreview(page, {theme: 'system'}); await apply(page, 'import');
  await action(page, 'undo'); await page.waitForSelector('[data-pref-preview]:not([hidden])'); await apply(page, 'undo');
  assert.equal((await storage(page))['ccc-theme'], 'light');
  assert.equal((await storage(page))['ccc-font'], 'mono');
});

test('cooperating tabs applying one revision produce one transaction and one success event', async t => {
  const page = await open(t);
  const peer = await page.browserContext().newPage();
  await peer.goto(baseURL, {waitUntil: 'load'});
  await peer.click('#settingsRailTab-preferences'); await waitStatus(peer, 'Ready.');
  assert.equal(await page.evaluate(() => Boolean(navigator.locks)), true);
  await importPreview(page, {theme: 'light'});
  await importPreview(peer, {theme: 'system'});
  await Promise.all([action(page, 'apply'), action(peer, 'apply')]);
  for (const candidate of [page, peer]) {
    await candidate.waitForFunction(() => /Applied import|Preferences changed since/.test(document.querySelector('[data-pref-status]').textContent));
  }
  const successes = await Promise.all([page, peer].map(candidate => candidate.evaluate(() => window.__events.length)));
  assert.equal(successes.reduce((total, value) => total + value, 0), 1);
  assert.equal(JSON.parse((await storage(page))[HISTORY]).sequence, 1);
  await peer.close();
});

test('browser without Web Locks still validates previews and can apply', async t => {
  const page = await open(t);
  await page.evaluate(() => Object.defineProperty(navigator, 'locks', {value: undefined}));
  await importPreview(page, {theme: 'light'}); await apply(page, 'import');
  assert.equal((await storage(page))['ccc-theme'], 'light');
  assert.equal(JSON.parse((await storage(page))[HISTORY]).sequence, 1);
});

test('local history is bounded, survives reload and excludes unregistered and unsupported raw data', async t => {
  const page = await open(t, {'ccc-theme': 'sk-ant-test-XXXX', unrelated: 'private'});
  await action(page, 'export'); await waitStatus(page, 'Exported');
  assert.equal(JSON.parse(await page.$eval('textarea', element => element.value)).values.theme, 'dark');
  for (let index = 0; index < 23; index++) { await importPreview(page, {theme: index % 2 ? 'light' : 'system'}); await apply(page, 'import'); }
  const historyRaw = (await storage(page))[HISTORY]; const history = JSON.parse(historyRaw);
  assert.equal(history.sequence, 23); assert.equal(history.entries.length, 20);
  assert.equal(history.entries[0].sequence, 4);
  assert(!historyRaw.includes('sk-ant-test')); assert(!historyRaw.includes('unrelated'));
  // New page in the same browser context preserves storage without reseeding.
  const reopened = await page.browserContext().newPage();
  await reopened.goto(baseURL, {waitUntil: 'load'}); await reopened.click('#settingsRailTab-preferences'); await waitStatus(reopened, 'Ready.');
  assert.match(await reopened.$eval('[data-pref-history]', element => element.textContent), /20 retained/);
  await reopened.close();
});

test('failed history write rolls back changed preference keys and emits no success event', async t => {
  const page = await open(t, {'ccc-theme': 'dark'});
  await importPreview(page, {theme: 'light', font: 'mono'});
  await page.evaluate(key => {const original = Storage.prototype.setItem; Storage.prototype.setItem = function(name, value) {if (name === key) throw new DOMException('Test quota failure', 'QuotaExceededError'); return original.call(this, name, value);};}, HISTORY);
  await action(page, 'apply'); await waitStatus(page, 'Changes were rolled back');
  assert.deepEqual(await storage(page), {'ccc-theme': 'dark'});
  assert.deepEqual(await page.evaluate(() => window.__events), []);
});

test('reload disclosure covers each apply, and host event sees fully persisted preferences and history', async t => {
  const page = await open(t);
  await page.evaluate(key => {
    window.__atDispatch = [];
    window.addEventListener('ccc:preferences-applied', event => {
      window.__atDispatch.push({operation: event.detail.operation,
        theme: localStorage.getItem('ccc-theme'), history: JSON.parse(localStorage.getItem(key))});
    });
  }, HISTORY);
  await importPreview(page, {theme: 'light'});
  for (const operation of ['import', 'reset', 'undo']) {
    if (operation !== 'import') {
      await action(page, operation);
      await page.waitForSelector('[data-pref-preview]:not([hidden])');
    }
    assert.match(await page.$eval('[data-pref-preview]', element => element.textContent), /reloads the dashboard/);
    assert.equal(await page.$eval('[data-pref-action="apply"]', element => element.textContent), `Apply ${operation} and reload`);
    await apply(page, operation);
  }
  const observed = await page.evaluate(() => window.__atDispatch);
  assert.deepEqual(observed.map(entry => [entry.operation, entry.theme, entry.history.sequence]),
    [['import', 'light', 1], ['reset', null, 2], ['undo', 'light', 3]]);
  for (const entry of observed) {
    const latest = entry.history.entries.at(-1);
    assert.equal(latest.operation, entry.operation);
    assert.equal(latest.sequence, entry.history.sequence);
    assert.deepEqual(latest.after, entry.theme === null ? {} : {theme: entry.theme});
  }
  await importPreview(page, {theme: 'light'});
  assert.equal(await page.$eval('[data-pref-action="apply"]', element => element.disabled), true);
  await page.$eval('[data-pref-action="apply"]', element => element.dispatchEvent(new MouseEvent('click', {bubbles: true})));
  assert.equal(await page.evaluate(() => window.__atDispatch.length), 3);
  assert.equal(JSON.parse((await storage(page))[HISTORY]).sequence, 3);
});

test('registry HTTP failures and poisoned source keys fail closed with refresh recovery', async t => {
  for (const cookie of ['failure', 'poison']) {
    const page = await open(t, {'private-token': 'sk-ant-test-XXXX'}, {cookie});
    assert.equal(await page.$eval('[data-pref-action="export"]', element => element.disabled), true);
    assert.equal(await page.$eval('[data-pref-action="import"]', element => element.disabled), true);
    assert.deepEqual(await storage(page), {'private-token': 'sk-ant-test-XXXX'});
    await page.deleteCookie({name: cookie, url: baseURL}); await action(page, 'refresh'); await waitStatus(page, 'Ready.');
    assert.equal(await page.$eval('[data-pref-action="export"]', element => element.disabled), false);
  }
});

test('corrupt local history is never overwritten', async t => {
  const page = await open(t, {[HISTORY]: '{broken'}, {cookie: 'failure'});
  await page.deleteCookie({name: 'failure', url: baseURL}); await action(page, 'refresh'); await waitStatus(page, 'Could not load');
  assert.equal(await page.$eval('[data-pref-action="reset"]', element => element.disabled), true);
  assert.equal((await storage(page))[HISTORY], '{broken');
});

test('empty/no-op import has no apply action; editing profile cancels an existing preview', async t => {
  const page = await open(t);
  await importPreview(page, {});
  assert.equal(await page.$eval('[data-pref-action="apply"]', element => element.disabled), true);
  assert.equal((await storage(page))[HISTORY], undefined);
  assert.deepEqual(await page.evaluate(() => window.__events), []);
  await importPreview(page, {theme: 'light'});
  await editor(page, PROFILE({font: 'mono'}));
  assert.equal(await page.$eval('[data-pref-preview]', element => element.hidden), true);
});

test('file import loads JSON without applying it, then follows the same preview flow', async t => {
  const page = await open(t);
  const target = path.join(screenshotDir, 'import.json');
  fs.writeFileSync(target, JSON.stringify(PROFILE({font: 'mono'})));
  await (await page.$('input[type=file]')).uploadFile(target);
  await waitStatus(page, 'Profile loaded.');
  assert.deepEqual(await storage(page), {});
  await action(page, 'import');
  await page.waitForSelector('[data-pref-preview]:not([hidden])');
  await apply(page, 'import');
  assert.equal((await storage(page))['ccc-font'], 'mono');
});

test('desktop and phone layouts support keyboard preview and have no horizontal overflow', async t => {
  const page = await open(t, {'ccc-theme': 'light', 'ccc-separate-tabs': 'on'});
  await page.screenshot({path: path.join(screenshotDir, 'desktop-overview.png'), fullPage: true});
  await importPreview(page, {theme: 'system', sounds_enabled: false});
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('data-pref-preview')), '');
  await page.screenshot({path: path.join(screenshotDir, 'desktop.png'), fullPage: true});
  await page.setViewport({width: 390, height: 844});
  await page.screenshot({path: path.join(screenshotDir, 'phone.png'), fullPage: true});
  await page.$eval('#settingsPane', element => {element.scrollTop = 0;});
  await page.screenshot({path: path.join(screenshotDir, 'phone-overview.png'), fullPage: true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.$eval('.ccc-prefs', element => element.scrollWidth <= element.clientWidth), true);
  await page.focus('[data-pref-action="cancel"]'); await page.keyboard.press('Enter');
  assert.equal(await page.$eval('[data-pref-preview]', element => element.hidden), true);
});
