const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const poll = app.slice(app.indexOf('  // ── Vercel deploy status ──'), app.indexOf('  function timeAgo(ts)'));
const start = app.indexOf('(function initLocalCiProgress()');
const progress = app.slice(start, app.indexOf('\n})();', start) + 6);
for (const width of [1280, 390]) test('Vercel phases share CI progress at ' + width, async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width, height:900});
    await page.setContent(`<style>${css}</style><div style="width:${Math.min(width, 330)}px;--text:#eee;--surface:#191919;--border:#555;--accent:#8ab4ff;--green:#89dd99;--red:#ff9999;--text-muted:#aaa;background:#191919;padding:12px;box-sizing:border-box">
      <div data-local-ci-host id="localCiProgress"></div><div class="sidebar"><div data-local-ci-host class="local-ci-mobile" id="localCiMobileList"></div></div>
      <div class="main"><div data-local-ci-host class="local-ci-mobile" id="localCiMobileReader"></div></div></div>`);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addScriptTag({content:`
      window.repo = '/example/project';
      window.deployment = {state:'BUILDING', url:'example.vercel.app', project:'example', commit_sha:'abc1234', commit_msg:'Update layout'};
      const activeConvRepoPath = () => window.repo;
      const repoUrl = (url, repo) => url + '?repo_path=' + encodeURIComponent(repo);
      const timeAgo = () => 'now';
      window.backgroundApiFetch = async () => ({ok:true,json:async () => window.deployment});
      window.posts = [];
      window.fetch = async (url, opts={}) => {
        if (opts.method === 'POST') posts.push(url);
        return {ok:true,json:async () => ({jobs:[{id:'ci1',repo:'example/project',name:'Tests',percent:50,current_step:'Run tests',completed_steps:1,total_steps:2}],completed:[]})};
      };
      ${poll}
      ${progress}
      window.checkDeployment = pollVercelDeploy;
      checkDeployment();
    `});
    const host = width === 390 ? '#localCiMobileList' : '#localCiProgress';
    await page.waitForFunction(selector => document.querySelector(selector).textContent.includes('Building'), {}, host);
    assert.match(await page.$eval(host, e => e.textContent), /CI · project · Tests/);
    assert.equal(await page.$eval(host + ' progress', e => e.hasAttribute('value')), false);
    assert.equal(await page.$eval(host + ' a', e => e.href), 'https://example.vercel.app/');
    assert.doesNotMatch(await page.$eval(host + ' a', e => e.title), /undefined|steps complete/);
    assert.equal(await page.$eval(host, e => e.getBoundingClientRect().right <= innerWidth), true);
    await page.screenshot({path:'/tmp/ccc-vercel-progress-' + width + '.png'});
    for (const [state, label, outcome] of [['QUEUED','Queued','active'],['INITIALIZING','Initializing','active'],['READY','Ready','success'],['ERROR','Failed','failure'],['CANCELED','Cancelled','neutral']]) {
      await page.evaluate(async state => {deployment.state=state;await checkDeployment();}, state);
      assert.equal(await page.$eval(host + ' .local-ci-percent', e => e.textContent), label);
      assert.equal(await page.$eval(host + ' .local-ci-entry', e => e.dataset.outcome), outcome);
    }
    await page.click(host + ' .local-ci-dismiss');
    assert.doesNotMatch(await page.$eval(host, e => e.textContent), /Vercel/);
    assert.deepEqual(await page.evaluate(() => posts), []);
    await page.evaluate(async () => {await checkDeployment();});
    assert.doesNotMatch(await page.$eval(host, e => e.textContent), /Vercel/);
    // An older repo's in-flight response cannot paint over the newly selected repo.
    await page.evaluate(async () => {
      let release;
      backgroundApiFetch = () => new Promise(resolve => {release=resolve;});
      const oldRequest=checkDeployment();
      repo='/example/other';
      backgroundApiFetch=async () => ({ok:true,json:async () => ({state:'BUILDING',project:'other',url:'other.vercel.app'})});
      await checkDeployment();
      release({ok:true,json:async () => ({state:'READY',project:'old',url:'old.vercel.app'})});
      await oldRequest;
    });
    assert.match(await page.$eval(host, e => e.textContent), /Vercel · other/);
    assert.doesNotMatch(await page.$eval(host, e => e.textContent), /Vercel · old/);
    await page.evaluate(async () => {backgroundApiFetch=async () => ({ok:true,json:async () => ({disabled:true})});await checkDeployment();});
    assert.doesNotMatch(await page.$eval(host, e => e.textContent), /Vercel/);
    assert.match(await page.$eval(host, e => e.textContent), /CI · project/);
    assert.deepEqual(errors, []);
  } finally {await browser.close();}
});
