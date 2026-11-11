// Real browser regressions for On-call, backed only by a disposable local API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const menuStart = app.indexOf('  function _wireSimpleActionsMenu() {');
const menuEnd = app.indexOf('\n  _wireSimpleActionsMenu();', menuStart);
assert.ok(menuStart >= 0 && menuEnd > menuStart);
const menuFunction = app.slice(menuStart, menuEnd);
let status, home, history, testFails, configFails, answerFails, historyFails;
let answers = [];
let channelAdds = [];
function reset() {
  status = {ok: true, enabled: true, wt: true, minor_decisions: true, queue: 'SHOP',
    config: {repo: '', channels: [{id: 'phone', type: 'ntfy', topic: 'sample', server: 'http://127.0.0.1'}]},
    channels: [{id: 'phone', type: 'ntfy', enabled: true, owner: 'sample', last_ok: null}],
    sources: [], waiting: [], channel_types: {ntfy: [
      {name: 'topic', label: 'Topic', optional: false},
      {name: 'server', label: 'Server', optional: true}], email: [
      {name: 'address', label: 'Mailbox address', optional: false},
      {name: 'password_vault', label: 'App password', secret: true, optional: false},
      {name: 'owner', label: 'Your email', optional: false}]}, source_types: {}};
  history = [{ref: 'SHOP-5', question: 'Which helper name?', recommend: 'bindExport',
    undo: 'Rename the helper.', state: 'applied'}];
  home = {ok: true, enabled: true, needs_you: [{id: 1, headline: 'Export question', detail: 'Keep the button?',
    choices: [{label: 'Keep it', reply: 'Keep it'}], free_text: true}],
    working: [], fixed: [], working_count: 0, fixed_count: 0, channels: [], sources: []};
  testFails = configFails = answerFails = historyFails = false;
  answers = [];
  channelAdds = [];
}
const server = http.createServer(async (req, res) => {
  const json = (body, code = 200) => {res.writeHead(code, {'Content-Type': 'application/json'}); res.end(JSON.stringify(body));};
  if (req.url.startsWith('/static/')) {
    const name = req.url.slice('/static/'.length);
    if (!['app.css', 'oncall.css', 'oncall.js', 'oncall-home.css', 'oncall-home.js'].includes(name)) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
    return res.end(fs.readFileSync(path.join(root, 'static', name)));
  }
  if (req.url === '/api/oncall/status') return json(status);
  if (req.method === 'GET' && req.url === '/api/oncall/decisions') {
    return historyFails ? json({ok: false, error: 'History fixture unavailable.'}, 500)
      : json({ok: true, decisions: history});
  }
  if (req.url === '/api/oncall/home') return json(home);
  let body = '';
  for await (const part of req) body += part;
  const data = body ? JSON.parse(body) : {};
  if (req.url === '/api/oncall/test') return json({ok: !testFails, results: [
    {channel: 'phone', ok: !testFails, error: testFails ? 'Local channel refused. Test again.' : ''}]});
  if (req.url === '/api/oncall/config') {
    if (configFails) return json({ok: false, error: 'Add a message channel first.'}, 400);
    if (data.minor_decisions) status.minor_decisions = data.minor_decisions.enabled;
    if ('enabled' in data) status.enabled = data.enabled;
    return json({ok: true});
  }
  if (req.url === '/api/oncall/answer') {
    if (answerFails) return json({ok: false, error: 'Session unavailable. Try again.'}, 400);
    answers.push(data); home.needs_you = [];
    return json({ok: true, message: 'Sent to the agent.'});
  }
  if (req.url === '/api/oncall/channel') {channelAdds.push(data); return json({ok: true});}
  if (req.url.startsWith('/api/')) return json({ok: true});
  res.end(`<html><head><link rel="stylesheet" href="/static/app.css"></head>
    <body><div id="settingsPane" class="settings-pane"></div><aside class="ccc-side-nav"><nav></nav></aside>
    <script>window.__CCC_FLAGS__={oncall:true};window.cccSettingsDestinations=[];</script>
    <script src="/static/oncall.js"></script><script src="/static/oncall-home.js"></script></body></html>`);
});
(async () => {
  let browser, checked = 0;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    browser = await puppeteer.launch({headless: true, executablePath: findChromePath(), args: ['--no-sandbox']});
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      reset();
      status.minor_decisions = false;
      historyFails = true;
      const context = await browser.createBrowserContext();
      const page = await context.newPage();
      // Settings redraws when its background fetches land; a click that races
      // a redraw is retried against the fresh node instead of failing.
      const tap = async selector => {
        for (let attempt = 0; ; attempt++) {
          try { await page.waitForSelector(selector); return await page.click(selector); }
          catch (error) { if (attempt >= 4 || !/detached|not clickable/i.test(error.message)) throw error; }
        }
      };
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setViewport({width, height: 900});
      await page.setRequestInterception(true);
      page.on('request', req => req.url().startsWith(base + '/') ? req.continue() : req.abort());
      await page.evaluateOnNewDocument(theme => {
        localStorage.setItem('ccc-theme', theme);
        if (theme === 'light') document.addEventListener('DOMContentLoaded', () => document.documentElement.dataset.theme = 'light');
      }, theme);
      await page.goto(base + '/?ccc_settings=oncall');
      await page.waitForFunction(() => document.querySelector('#settingsSection-oncall')?.textContent.includes('Decision history is unavailable.'));
      // The fixture has no full-app Settings router; expose the component it mounted.
      await page.$eval('#settingsSection-oncall', el => el.classList.add('is-active-section'));
      const section = '#settingsSection-oncall';
      const unavailable = /Decision history is unavailable\. Try again later\./;
      assert.match(await page.$eval(section, el => el.innerText), unavailable);
      assert.doesNotMatch(await page.$eval(section, el => el.innerText), /No decisions recorded\./);
      assert.equal(await page.$eval(section + ' [aria-label="Queue for bug tickets"]', el => el.value), 'SHOP');
      assert.equal(await page.$eval(section + ' [aria-label="On-call"]', el => el.getAttribute('aria-checked')), 'true');
      assert.equal(await page.$eval(section + ' [aria-label="Agents decide small reversible things"]', el => el.getAttribute('aria-checked')), 'false');
      checked++;
      await tap(section + ' [aria-label="On-call"]');
      await page.waitForFunction(() => {
        const toggle = document.querySelector('#settingsSection-oncall [aria-label="On-call"]');
        return toggle.getAttribute('aria-checked') === 'false' && !toggle.disabled;
      });
      assert.equal(status.enabled, false);
      assert.equal(status.minor_decisions, false);
      assert.match(await page.$eval(section, el => el.innerText), unavailable);
      checked++;
      historyFails = false;
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /SHOP-5/);
      assert.doesNotMatch(await page.$eval(section, el => el.innerText), unavailable);
      assert.equal(await page.$eval(section + ' [aria-label="On-call"]', el => el.getAttribute('aria-checked')), 'false');
      assert.equal(await page.$eval(section + ' [aria-label="Agents decide small reversible things"]', el => el.getAttribute('aria-checked')), 'false');
      checked++;
      status.enabled = status.minor_decisions = true;
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /Which helper name\?[\s\S]*Applied\./);
      checked++;
      await tap('[aria-label="Agents decide small reversible things"]');
      await page.waitForFunction(() => {
        const toggle = document.querySelector('[aria-label="Agents decide small reversible things"]');
        return toggle.getAttribute('aria-checked') === 'false' && !toggle.disabled;
      });
      assert.match(await page.$eval(section, el => el.innerText), /SHOP-5/);
      checked++;
      history[0].state = 'applied reversed';
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /Undone\./);
      history = [];
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /No decisions recorded\./);
      checked++;
      const channelButton = section + ' .oncall-list .settings-action-btn';
      await tap(channelButton);
      await page.waitForFunction(() => document.querySelector('#settingsSection-oncall').textContent.includes('Test send accepted.'));
      await page.evaluate(() => document.activeElement.blur());
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /Test send accepted\. Check the channel\./);
      checked++;
      testFails = true;
      await tap(channelButton);
      await page.waitForFunction(() => document.querySelector('#settingsSection-oncall').textContent.includes('Local channel refused.'));
      await page.waitForNetworkIdle();
      await page.evaluate(() => document.activeElement.blur());
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /Local channel refused\. Test again\./);
      const savedChannel = status.channels.pop();
      await page.evaluate(() => window.cccOncallSettings.refresh());
      status.channels.push(savedChannel);
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.doesNotMatch(await page.$eval(section, el => el.innerText), /Local channel refused/);
      checked++;
      configFails = true;
      await tap(section + ' [aria-label="On-call"]');
      await page.waitForFunction(() => document.querySelector('#settingsSection-oncall').textContent.includes('Add a message channel first.'));
      await page.evaluate(() => window.cccOncallSettings.refresh());
      assert.match(await page.$eval(section, el => el.innerText), /Add a message channel first\./);
      assert.equal(await page.$eval(section + ' [aria-label="On-call"]', el => el.getAttribute('aria-checked')), 'true');
      checked++;
      assert.match(await page.$eval(section, el => el.innerText), /Subscribe in ntfy: http:\/\/127\.0\.0\.1\/sample/);
      const selects = await page.$$(section + ' .oncall-add-row select');
      await selects[0].select('ntfy');
      const input = section + ' .oncall-form input';
      await page.type(input, 'sample');
      assert.equal(await page.$eval(input, el => el.labels[0].innerText.includes('Topic')), true);
      assert.equal(await page.$eval(section + ' [aria-label="Queue for bug tickets"]', el => el.tagName), 'INPUT');
      checked++;
      await page.$eval(input, el => {el.value = ''; el.dispatchEvent(new Event('input'));});
      await tap(section + ' .oncall-form button');
      assert.equal(await page.$eval(input, el => el === document.activeElement && el.getAttribute('aria-invalid') === 'true'), true);
      checked++;
      // Email recipients can default to the mailbox on the server. Metadata
      // marked required must not add a new client-side setup requirement.
      await selects[0].select('email');
      await page.type(section + ' .oncall-form [aria-label="Mailbox address"]', 'tester@example.com');
      await page.type(section + ' .oncall-form [aria-label="App password"]', 'ExampleTestOnly!');
      await tap(section + ' .oncall-form button');
      await page.waitForFunction(() => !document.querySelector('#settingsSection-oncall .oncall-form input'));
      assert.equal(channelAdds.length, 1);
      assert.deepEqual(channelAdds[0].channel, {type: 'email', address: 'tester@example.com'});
      checked++;
      await page.goto(base + '/?oncall-home=1');
      await page.waitForSelector('#oncallHome:not([hidden]) .oncall-home-input');
      const reply = '#oncallHome .oncall-home-input';
      await page.type(reply, 'Sample test text');
      await page.$eval(reply, el => el.blur());
      await page.evaluate(() => window.cccOncallHome.refresh());
      assert.equal(await page.$eval(reply, el => el.value), 'Sample test text');
      checked++;
      const choice = '#oncallHome .oncall-home-acts button';
      await page.focus(choice);
      await page.evaluate(() => window.cccOncallHome.refresh());
      assert.equal(await page.$eval(choice, el => el === document.activeElement), true);
      checked++;
      // While a control keeps focus the list still follows the server: a new
      // question appears and an answered one leaves, without moving focus.
      home.needs_you.push({id: 2, headline: 'Second question', detail: 'Ship it?', choices: [], free_text: true});
      await page.evaluate(() => window.cccOncallHome.refresh());
      assert.equal(await page.$$eval('#oncallHome .oncall-home-card.is-you', cards => cards.length), 2);
      assert.equal(await page.$eval(choice, el => el === document.activeElement), true);
      home.needs_you.pop();
      await page.evaluate(() => window.cccOncallHome.refresh());
      assert.equal(await page.$$eval('#oncallHome .oncall-home-card.is-you', cards => cards.length), 1);
      assert.equal(await page.$eval(choice, el => el === document.activeElement), true);
      checked++;
      await page.$eval(reply, el => {el.value = ''; el.dispatchEvent(new Event('input'));});
      await page.$eval('#oncallHome form', el => el.requestSubmit());
      assert.equal(answers.length, 0);
      assert.equal(await page.$eval(reply, el => el === document.activeElement && el.getAttribute('aria-invalid') === 'true'), true);
      assert.match(await page.$eval('#oncallHome', el => el.innerText), /Type an answer first\./);
      checked++;
      answerFails = true;
      await page.type(reply, 'Sample test text');
      await tap('#oncallHome form button');
      await page.waitForFunction(() => document.querySelector('#oncallHome').textContent.includes('Session unavailable.'));
      await page.evaluate(() => document.activeElement.blur());
      await page.evaluate(() => window.cccOncallHome.refresh());
      assert.equal(await page.$eval(reply, el => el.value), 'Sample test text');
      assert.match(await page.$eval('#oncallHome', el => el.innerText), /Session unavailable\. Try again\./);
      answerFails = false;
      await tap(choice);
      await page.waitForFunction(() => !document.querySelector('#oncallHome .oncall-home-card'));
      assert.deepEqual(answers, [{id: 1, text: 'Keep it'}]);
      assert.equal(await page.$eval('#oncallHome .oncall-home-change', el => el.innerText), 'On-call settings');
      checked++;
      assert.deepEqual(errors, []);
      if (width === 390) {
        await page.setContent(`<link rel="stylesheet" href="${base}/static/app.css">
          <button id="convSimpleActionsBtn" style="position:fixed;top:8px;left:8px;width:48px;height:44px">Actions</button>
          <div id="convSimpleActionsMenu" class="mobile-actions-menu" hidden><button class="mobile-actions-item">Task details</button></div>`);
        await page.addScriptTag({content: 'function _syncSessionMenuItems() {}\n' + menuFunction + '\n_wireSimpleActionsMenu();'});
        for (const left of [8, 334]) {
          await page.$eval('#convSimpleActionsBtn', (el, left) => el.style.left = left + 'px', left);
          await tap('#convSimpleActionsBtn');
          const rect = await page.$eval('#convSimpleActionsMenu', el => {
            const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, width: r.width};
          });
          assert.ok(rect.width > 0 && rect.left >= 8 && rect.right <= width - 8, JSON.stringify(rect));
          await tap('#convSimpleActionsBtn');
          checked++;
        }
      }
      await context.close();
    }
    console.log(`PASS: ${checked} browser checks at 390/1440 in light/dark; local fixture only.`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
