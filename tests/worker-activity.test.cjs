const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

test('worker header counts receipts, groups repos, labels plan coverage and ages out stale data', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent('<div class="sidebar"><div id="mobileSimpleHeader"></div><div class="sidebar-header"><div class="sidebar-header-brand">Command Center</div><div class="mobile-home-status">CI: Running</div></div></div>');
    await page.addStyleTag({content:fs.readFileSync('static/app.css','utf8')});
    await page.evaluate(() => {
      window.testNow = 1800000000000;
      Date.now = () => window.testNow;
      window.ticks = [];
      window.setInterval = fn => { window.ticks.push(fn); };
      window.data = {sampled_at:Date.now()/1000,pending:false,workers:[
        {worker_id:'one',repo_path:'/example/app',last_token_at:Date.now()/1000-10,plan:{done:1,total:2}},
        {worker_id:'two',repo_path:'/example/app',last_token_at:Date.now()/1000-90},
        {worker_id:'three',repo_path:'/example/tools',last_token_at:Date.now()/1000-299},
        {worker_id:'old',repo_path:'/example/app',last_token_at:Date.now()/1000-301},
        {worker_id:'unknown',repo_path:'/example/app',last_token_at:null},
        {worker_id:'future',repo_path:'/example/app',last_token_at:Date.now()/1000+100}
      ]};
      window.fetch = async () => ({ok:true,json:async()=>window.data});
    });
    await page.addScriptTag({content:fs.readFileSync('static/worker-activity.js','utf8')});
    await page.waitForSelector('.worker-activity-now');
    let result = await page.evaluate(() => ({text:document.querySelector('.worker-activity-line').textContent,
      groups:[...document.querySelectorAll('.worker-activity-repo')].map(e=>({text:e.textContent,title:e.title})),
      title:document.querySelector('.worker-activity-line').title}));
    assert.match(result.text,/1 now.*2 recent/);
    assert.deepEqual(result.groups.map(g=>g.text),['app 2 · plan 50%','tools 1']);
    assert.match(result.groups[0].title,/1\/2 workers report a plan/);
    assert.match(result.groups[1].title,/progress unavailable/);
    assert.match(result.title,/1 live workers have no measurable usage/);
    await page.setViewport({width:390,height:800});
    await page.evaluate(() => document.body.classList.add('ccc-simple-mode'));
    await page.waitForFunction(() => document.querySelector('.worker-activity-line').parentElement.id === 'mobileSimpleHeader');
    assert.equal(await page.evaluate(() => document.querySelector('.worker-activity-line').clientWidth <= innerWidth),true);
    await page.evaluate(() => document.body.classList.add('mobile-home-collapsed'));
    assert.equal(await page.evaluate(() => document.querySelector('.worker-activity-line').checkVisibility()),false);
    await page.evaluate(() => {document.body.classList.remove('mobile-home-collapsed');window.testNow += 46000;window.ticks[1]();});
    assert.match(await page.$eval('.worker-activity-line',e=>e.textContent),/Activity unavailable/);
    assert.equal(await page.$('.worker-activity-now'),null);
    await page.evaluate(async () => {window.data={sampled_at:Date.now()/1000,pending:false,workers:[]};await window.ticks[0]();});
    assert.match(await page.$eval('.worker-activity-line',e=>e.textContent),/0 now.*0 recent.*No token usage in 5m/);
    await page.evaluate(async () => {
      window.data={sampled_at:Date.now()/1000,pending:false,workers:[{repo_path:'/example/<img src=x>',last_token_at:Date.now()/1000}]};
      await window.ticks[0]();
    });
    assert.equal(await page.$('.worker-activity-line img'),null,'repository labels remain text');
    assert.match(await page.$eval('.worker-activity-repo',e=>e.textContent),/<img src=x>/);
  } finally { await browser.close(); }
});
