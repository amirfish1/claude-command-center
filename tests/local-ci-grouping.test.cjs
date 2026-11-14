const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('(function initLocalCiProgress()');
const progress = app.slice(start, app.indexOf('\n})();', start) + 6);
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

for (const width of [1280, 390]) test('one expandable CI row per commit at ' + width, async () => {
  const base = {repo:'example/project', head_sha:'a'.repeat(40), execution:'github_hosted',
    name:'unittest', percent:42, workflow_percent:42, current_step:'Run tests', completed_steps:3, total_steps:7,
    workflow_started_at:new Date(Date.now() - 312000).toISOString(),
    origin:{pusher:{engine:'codex', session_id:'test-session', worker:true}, tickets:['TEST-42']},
    commit_subject:'<img src=x onerror="window.__xss=1">', event:'push', actor:'example-user'};
  let data = {jobs:[{...base, id:'example/project:1:1'},
    {...base, id:'example/project:1:1', name:'another job', percent:90}], completed:[
    {...base, id:'example/project:2:1', name:'install-smoke', conclusion:'success'},
    {...base, id:'example/project:3:1', head_sha:'b'.repeat(40), conclusion:'failure'}]};
  const dismissed = [];
  const server = http.createServer(async (req, res) => {
    if (req.url.startsWith('/api/ci/')) {
      if (req.url === '/api/ci/dismiss') {
        let body = '';
        for await (const chunk of req) body += chunk;
        const {id} = JSON.parse(body);
        dismissed.push(id);
        data.completed = data.completed.filter(row => row.id !== id);
      }
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(data));
    } else {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(`<style>${css}</style><div style="width:100%;max-width:330px"><div class="sidebar"><div data-local-ci-host id="checks"></div></div></div><script>${progress}</script>`);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width, height:650});
    await page.goto('http://127.0.0.1:' + server.address().port, {waitUntil:'domcontentloaded'});
    await page.waitForSelector('#checks .local-ci-group');
    assert.equal(await page.$$eval('#checks .local-ci-group', es => es.length), 2);
    assert.equal(await page.$eval('#checks details', el => el.open), false);
    assert.match(await page.$eval('#checks summary', el => el.textContent), /TEST-42.*aaaaaaa.*71%/);
    assert.match(await page.$eval('#checks summary .local-ci-elapsed', el => el.textContent), /^5:\d{2}$/);
    assert.match(await page.$eval('#checks summary', el => el.title), /2 running · 1 passed/);
    assert.equal(await page.$eval('#checks summary', el => el.getBoundingClientRect().height <= 44), true);
    assert.equal(await page.$eval('#checks', el => el.scrollWidth <= el.clientWidth), true);
    assert.equal(await page.$eval('#checks .local-ci-group:last-child', el => el.dataset.outcome), 'failure');
    await page.screenshot({path:'/tmp/ccc-ci-grouped-' + width + '.png'});
    await page.focus('#checks summary');
    await page.keyboard.press('Enter');
    await page.waitForSelector('#checks details[open] .local-ci-context', {visible:true});
    assert.match(await page.$eval('#checks .local-ci-context', el => el.textContent), /test-session/);
    assert.equal(await page.$$eval('#checks img', es => es.length), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForNetworkIdle({idleTime:100});
    assert.equal(await page.$eval('#checks details', el => el.open), true);
    assert.equal(await page.evaluate(() => document.activeElement.matches('#checks summary')), true);
    await page.keyboard.press('Space');
    await page.waitForFunction(() => !document.querySelector('#checks details').open);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#checks details').open);
    data = {jobs:[], completed:[...data.completed, {...base, id:'example/project:1:1', conclusion:'success'}]};
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForFunction(() => document.querySelector('#checks summary').textContent.includes('100%'));
    await page.click('#checks .local-ci-group > .local-ci-dismiss');
    await page.waitForFunction(() => document.querySelectorAll('#checks .local-ci-group').length === 1);
    assert.deepEqual(new Set(dismissed), new Set(['example/project:1:1', 'example/project:2:1']));
    assert.match(await page.$eval('#checks summary', el => el.textContent), /1 failed/);
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
