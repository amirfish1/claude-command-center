const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(process.env.CCC_CI_SOURCE || require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('(function initLocalCiProgress()');
const progress = app.slice(start, app.indexOf('\n})();', start) + 6);
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const key = 'ccc-ci-progress-v1';
const active = {id:'example/project:123:1', repo:'example/project', name:'Tests',
  percent:50, current_step:'Run tests', completed_steps:1, total_steps:2,
  execution:'github_hosted', event:'push', actor:'example-user', head_sha:'abcdef1234567890',
  commit_subject:'Fix test progress', origin:{sessions:[], tickets:['TEST-42'],
    pusher:{session_id:'test-session', engine:'codex', worker:true}},
  url:'https://github.com/example/project/actions/runs/123'};

for (const width of [1280, 390]) test('CI survives refresh before live status returns at ' + width, async () => {
  let hold = false;
  let fail = false;
  let data = {jobs:[active], completed:[]};
  const pending = [];
  const reply = res => {
    res.writeHead(fail ? 503 : 200, {'Content-Type':'application/json'});
    res.end(JSON.stringify(data));
  };
  const server = http.createServer((req, res) => {
    if (req.url === '/api/ci/local') {
      if (hold) pending.push(res);
      else reply(res);
    } else if (req.url === '/api/ci/dismiss') {
      data = {jobs:[], completed:[]};
      reply(res);
    } else {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(`<style>${css}</style><div style="width:${Math.min(width, 330)}px;padding:12px;box-sizing:border-box">
        <div data-local-ci-host id="localCiProgress" hidden></div>
        <div class="sidebar"><div data-local-ci-host class="local-ci-mobile" id="localCiMobileList" hidden></div></div>
        </div><script>
          const nativeFetch = window.fetch.bind(window);
          window.ciReadPriorities = [];
          window.fetch = (input, init) => {
            if (input === '/api/ci/local') ciReadPriorities.push(init?.cccPriority || 'background');
            return nativeFetch(input, init);
          };
          ${progress}</script>`);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewport({width, height:600});
    const host = width === 390 ? '#localCiMobileList' : '#localCiProgress';
    await page.goto(`http://127.0.0.1:${server.address().port}`, {waitUntil:'domcontentloaded'});
    await page.waitForSelector(host + ' progress[value="50"]');
    const context = await page.$eval(host + ' .local-ci-context', el => el.textContent);
    assert.match(context, /GitHub-hosted runner/);
    assert.match(context, /push · example-user · abcdef1/);
    assert.match(context, /Pushed by codex · test-session · control-plane worker/);
    assert.match(context, /TEST-42/);
    assert.equal(await page.$eval(host, el => el.scrollWidth <= el.clientWidth), true);
    assert.equal(await page.evaluate(() => ciReadPriorities[0]), 'user', 'cold CI read bypasses startup background queue');
    hold = true;
    await page.reload({waitUntil:'domcontentloaded'});
    // No waitForSelector: saved progress must already exist at DOM ready,
    // even though every live CI response is deliberately held open.
    assert.equal(await page.$eval(host, el => el.hidden), false);
    assert.match(await page.$eval(host, el => el.textContent), /Saved · Run tests/);
    assert.equal(await page.$eval(host + ' progress', el => el.value), 50);
    await page.screenshot({path:'/tmp/ccc-ci-refresh-' + width + '.png'});

    // A failed live update must retain the saved running job.
    fail = true;
    hold = false;
    pending.splice(0).forEach(reply);
    await page.waitForNetworkIdle({idleTime:100});
    assert.match(await page.$eval(host, el => el.textContent), /Saved · Run tests/);

    fail = false;
    data = {jobs:[], completed:[{...active, percent:100, conclusion:'success', completed_at:'2026-01-01T12:00:00Z'}]};
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForFunction(selector => document.querySelector(selector).textContent.includes('Passed'), {}, host);
    assert.doesNotMatch(await page.$eval(host, el => el.textContent), /Saved/);
    await page.click(host + ' .local-ci-dismiss');
    await page.waitForFunction(selector => document.querySelector(selector).hidden, {}, host);
    hold = true;
    await page.reload({waitUntil:'domcontentloaded'});
    assert.equal(await page.$eval(host, el => el.hidden), true, 'dismissed runs stay dismissed on refresh');

    for (const value of ['invalid json', JSON.stringify({at:Date.now() - 6 * 60000, data:{jobs:[active], completed:[]}})]) {
      await page.evaluate((key, value) => sessionStorage.setItem(key, value), key, value);
      await page.reload({waitUntil:'domcontentloaded'});
      assert.equal(await page.$eval(host, el => el.hidden), true, 'invalid or expired snapshots stay hidden');
    }
    // Storage errors must not prevent a live response from rendering.
    await page.evaluateOnNewDocument(() => {
      Storage.prototype.getItem = () => { throw new Error('Storage disabled'); };
      Storage.prototype.setItem = () => { throw new Error('Storage disabled'); };
    });
    hold = false;
    data = {jobs:[active], completed:[]};
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForSelector(host + ' progress[value="50"]');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
