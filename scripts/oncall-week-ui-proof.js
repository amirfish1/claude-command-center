// Local fixture proof of the real On-call settings component. No live API.
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const config = {daily_summary: {enabled:false, time:'09:00'}};
const errors = [];
const server = http.createServer((req, res) => {
  if (req.url === '/api/oncall/status') {
    res.setHeader('Content-Type','application/json');
    return res.end(JSON.stringify({ok:true, config, wt:true, channels:[], sources:[], waiting:[], log:[]}));
  }
  if (req.url === '/api/oncall/config') {
    let body = '';
    req.on('data', b => body += b);
    return req.on('end', () => {
      const patch = JSON.parse(body);
      Object.assign(config.daily_summary, (patch.patch || patch).daily_summary);
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({ok:true, config}));
    });
  }
  if (['/static/oncall.js','/static/oncall.css','/static/app.css'].includes(req.url)) {
    res.setHeader('Content-Type', req.url.endsWith('.js') ? 'text/javascript' : 'text/css');
    return res.end(fs.readFileSync(path.join(root,req.url)));
  }
  res.end('<html><head><link rel="stylesheet" href="/static/app.css"></head><body><div id="settingsPane"></div><script>window.__CCC_FLAGS__={oncall:true};window.cccSettingsDestinations=[];</script><script src="/static/oncall.js"></script></body></html>');
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
    browser = await puppeteer.launch({executablePath:findChromePath(), headless:true, args:['--no-sandbox']});
    const page = await browser.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.setRequestInterception(true);
    page.on('request', req => req.url().startsWith(`http://127.0.0.1:${server.address().port}/`) ? req.continue() : req.abort());
    await page.setViewport({width:1100,height:1600});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const selector = '[role="switch"][aria-label="Daily summary"]';
    await page.waitForSelector(selector);
    assert.equal(await page.$eval(selector, e => e.getAttribute('aria-checked')), 'false');
    await page.click(selector);
    await page.waitForFunction(() => document.querySelector('[aria-label="Daily summary"]').getAttribute('aria-checked') === 'true');
    await page.$eval('[aria-label="Daily summary time"]', e => {e.value='08:30';e.dispatchEvent(new Event('change'));});
    await page.waitForNetworkIdle();
    assert.deepEqual(config.daily_summary,{enabled:true,time:'08:30'});
    assert.deepEqual(errors,[]);
    if (process.argv[2]) await page.screenshot({path:process.argv[2],fullPage:true});
    console.log('PASS: default off; switch and time saved; no browser errors. Fixture API only.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(e => {console.error(e);process.exitCode=1;});
