const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const fake = 'obvious-test-password-XXXX';

async function open(t, width = 1280) {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.setViewport({width, height:900});
  const posts = [];
  let status = 200;
  let requests = ['first', 'second'].map(id => ({id, name:`Example ${id}`, reason:'Authorize the local example tool.', kind:'password', expires_at:Date.now()/1000+300}));
  await page.setRequestInterception(true);
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.isNavigationRequest()) return request.respond({status:200, contentType:'text/html', body:'<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Credential prompt</title><link rel="stylesheet" href="/static/credential-broker.css"><body><script src="/static/credential-broker.js"></script></body></html>'});
    if (url.pathname.startsWith('/static/')) return request.respond({status:200, contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript', body:fs.readFileSync(path.join(__dirname,'..',url.pathname),'utf8')});
    if (url.pathname === '/api/secrets/resolve') {
      const body = JSON.parse(request.postData());
      posts.push(body);
      if (status === 200) requests = requests.filter(row => row.id !== body.id);
      return request.respond({status, contentType:'application/json', body:'{}'});
    }
    return request.respond({status:200, contentType:'application/json', body:JSON.stringify({requests})});
  });
  await page.goto('http://ccc-credential.test/', {waitUntil:'load'});
  await page.waitForSelector('#credential-first');
  return {page, posts, fail:() => {status = 500;}, remove:() => {requests = [];}};
}

for (const width of [1280, 390]) test(`secret visibility is explicit, reversible and local to one request at ${width}px`, async t => {
  const {page, posts} = await open(t, width);
  if (process.env.CCC_CREDENTIAL_BEFORE && width === 1280) await page.screenshot({path:process.env.CCC_CREDENTIAL_BEFORE});
  const value = '#credential-first';
  const toggle = '[data-request-id="first"] .credential-visibility input';
  assert.equal(await page.$eval(value, el => el.type), 'password');
  await page.type(value, fake);
  await page.click(toggle);
  assert.equal(await page.$eval(value, el => el.type), 'text');
  assert.equal(await page.$eval(value, el => el.value), fake);
  assert.equal(await page.$eval('#credential-second', el => el.type), 'password');
  assert.equal(await page.$eval('[data-request-id="first"] .credential-remember input', el => el.checked), false);
  const shown = await page.$eval(value, el => {const r=el.getBoundingClientRect();return {width:r.width, height:r.height, right:r.right};});
  assert.ok(shown.height >= 44 && shown.right <= width && shown.width > 200, 'revealing keeps the full-size responsive field');
  assert.equal(posts.length, 0, 'a visibility toggle cannot submit or save');
  await page.focus(toggle);
  await page.keyboard.press('Space');
  assert.equal(await page.$eval(value, el => el.type), 'password');
  assert.equal(await page.$eval(value, el => el.value), fake);
  if (process.env.CCC_CREDENTIAL_SCREENSHOTS) {
    await page.click(toggle);
    await page.screenshot({path:path.join(process.env.CCC_CREDENTIAL_SCREENSHOTS, `visible-${width}.png`)});
  }
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
});

test('revealed values clear and re-mask on hide, failed submission, removal and page exit', async t => {
  const {page, posts, fail, remove} = await open(t);
  const value = '#credential-first';
  const toggle = '[data-request-id="first"] .credential-visibility input';
  async function reveal() {await page.type(value, fake); await page.click(toggle);}
  async function cleared() {
    assert.deepEqual(await page.$eval(value, el => ({value:el.value, type:el.type})), {value:'', type:'password'});
    assert.equal(await page.$eval(toggle, el => el.checked), false);
  }
  await reveal();
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {value:true, configurable:true}); document.dispatchEvent(new Event('visibilitychange'));});
  await cleared();
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {value:false, configurable:true}); document.dispatchEvent(new Event('visibilitychange'));});
  await reveal();
  fail();
  await page.click('[data-request-id="first"] button[type="submit"]');
  await page.waitForFunction(() => document.querySelector('.credential-error').textContent.includes('Could not submit'));
  await cleared();
  assert.deepEqual(posts, [{id:'first', value:fake, remember:false}]);
  await reveal();
  await page.evaluate(() => {window.detachedCredential=document.getElementById('credential-first');});
  remove();
  await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
  await page.waitForFunction(() => !document.getElementById('credential-first'));
  assert.deepEqual(await page.evaluate(() => ({value:detachedCredential.value, type:detachedCredential.type})), {value:'', type:'password'});
});

test('denying a revealed request never sends its value and clears the detached field', async t => {
  const {page, posts} = await open(t);
  await page.type('#credential-first', fake);
  await page.click('[data-request-id="first"] .credential-visibility input');
  await page.evaluate(() => {window.detachedCredential=document.getElementById('credential-first');});
  await page.click('[data-request-id="first"] button[type="button"]');
  await page.waitForFunction(() => !document.getElementById('credential-first'));
  assert.deepEqual(posts, [{id:'first', deny:true}]);
  assert.equal(await page.evaluate(() => detachedCredential.value), '');
  await page.type('#credential-second', fake);
  await page.click('[data-request-id="second"] .credential-visibility input');
  await page.evaluate(() => {window.detachedCredential=document.getElementById('credential-second');window.dispatchEvent(new Event('pagehide'));});
  assert.equal(await page.evaluate(() => detachedCredential.value), '');
  assert.equal(await page.$eval('#credentialBroker', el => el.querySelectorAll('.credential-card').length), 0);
});
