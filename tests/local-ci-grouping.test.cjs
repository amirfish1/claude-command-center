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
    origin:{pusher:{engine:'codex', session_id:'test-session', worker:true}, tickets:['TEST-42','TEST-43','TEST-44']},
    commit_subject:'<img src=x onerror="window.__xss=1">', event:'push', actor:'example-user'};
  let data = {jobs:[{...base, id:'example/project:1:1'},
    {...base, id:'example/project:1:1', name:'another job', percent:90}], completed:[
    {...base, id:'example/project:2:1', name:'install-smoke', conclusion:'success'},
    {...base, id:'example/project:3:1', head_sha:'b'.repeat(40), execution:'self_hosted', conclusion:'failure'}]};
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
      res.end(`<style>${css}</style><div style="width:100%;max-width:330px"><div class="sidebar"><div data-local-ci-host id="localCiMobileList" class="${width <= 1200 ? 'local-ci-mobile' : ''}"></div></div></div><script>${progress}</script>`);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width, height:650});
    await page.goto('http://127.0.0.1:' + server.address().port, {waitUntil:'domcontentloaded'});
    await page.waitForSelector('#localCiMobileList .local-ci-group');
    assert.equal(await page.$$eval('#localCiMobileList .local-ci-group', es => es.length), 2);
    assert.equal(await page.$eval('#localCiMobileList details', el => el.open), false);
    assert.equal(await page.$eval('#localCiMobileList .local-ci-runner', el => el.getAttribute('aria-label')), 'GitHub-hosted runner');
    assert.equal(await page.$eval('#localCiMobileList .local-ci-group:last-child .local-ci-runner', el => el.getAttribute('aria-label')), 'Machine-hosted runner');
    assert.match(await page.$eval('#localCiMobileList summary', el => el.textContent), /TEST-42 \+2.*aaaaaaa.*71%/);

    assert.match(await page.$eval('#localCiMobileList summary .local-ci-elapsed', el => el.textContent), /^5:\d{2}$/);
    assert.match(await page.$eval('#localCiMobileList summary', el => el.title), /2 running · 1 passed/);
    assert.equal(await page.$eval('#localCiMobileList summary', el => el.getBoundingClientRect().height <= 44), true);
    assert.equal(await page.$eval('#localCiMobileList', el => el.scrollWidth <= el.clientWidth), true);
    assert.equal(await page.$eval('#localCiMobileList .local-ci-group:last-child', el => el.dataset.outcome), 'failure');
    if (width <= 1200) {
      assert.equal(await page.$eval('#localCiMobileList .local-ci-mobile-toggle', el => el.textContent), 'CI: 1 running, 1 failed');
      assert.equal(await page.$eval('#localCiMobileList', el => el.getBoundingClientRect().height), 45, 'one 44px disclosure plus border');
      assert.equal(await page.$eval('#localCiMobileList .local-ci-group', el => getComputedStyle(el).display), 'none');
      await page.screenshot({path:'/tmp/ccc-ci-mobile-line.png'});
      await page.focus('.local-ci-mobile-toggle');
      await page.keyboard.press('Enter');
      assert.equal(await page.$eval('.local-ci-mobile-toggle', el => el.getAttribute('aria-expanded')), 'true');
    }
    assert.equal(await page.$eval('#localCiMobileList .local-ci-summary-name', el => el.scrollWidth <= el.clientWidth), true);
    await page.screenshot({path:'/tmp/ccc-ci-grouped-' + width + '.png'});
    await page.focus('#localCiMobileList summary');
    await page.keyboard.press('Enter');
    await page.waitForSelector('#localCiMobileList details[open] .local-ci-context', {visible:true});
    assert.match(await page.$eval('#localCiMobileList .local-ci-context', el => el.textContent), /test-session/);
    assert.equal(await page.$$eval('#localCiMobileList img', es => es.length), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForNetworkIdle({idleTime:100});
    assert.equal(await page.$eval('#localCiMobileList details', el => el.open), true);
    assert.equal(await page.evaluate(() => document.activeElement.matches('#localCiMobileList summary')), true);
    await page.keyboard.press('Space');
    await page.waitForFunction(() => !document.querySelector('#localCiMobileList details').open);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#localCiMobileList details').open);
    data = {jobs:[], completed:[...data.completed, {...base, id:'example/project:1:1', conclusion:'success'}]};
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForFunction(() => document.querySelector('#localCiMobileList summary').textContent.includes('100%'));
    await page.click('#localCiMobileList .local-ci-group > .local-ci-dismiss');
    await page.waitForFunction(() => document.querySelectorAll('#localCiMobileList .local-ci-group').length === 1);
    assert.deepEqual(new Set(dismissed), new Set(['example/project:1:1', 'example/project:2:1']));
    assert.match(await page.$eval('#localCiMobileList summary', el => el.textContent), /1 failed/);
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
