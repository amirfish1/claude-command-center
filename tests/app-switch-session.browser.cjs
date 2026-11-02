// CCC_APP_SWITCH_TEST_URL=http://127.0.0.1:8090 node tests/app-switch-session.browser.cjs
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

(async () => {
  const base = process.env.CCC_APP_SWITCH_TEST_URL;
  if (!base) throw new Error('Set CCC_APP_SWITCH_TEST_URL to a running dashboard');
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440, height:1000});
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-engines-first-run-done', '1');
      localStorage.setItem('ccc-ui-mode', 'advanced');
      localStorage.setItem('ccc-sidebar-tab', 'workers');
    });
    const apps = [
      {id:'sessions', label:'Sessions', url:'/'},
      {id:'queues', label:'Queues', url:'/q2.html'},
      {id:'test-app', label:'Test App', url:'/test-app'},
    ];
    // Use the real dashboard and navigation scripts, with a disposable app.
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.method() === 'POST') return request.respond({contentType:'application/json', body:'{}'});
      if (url.pathname === '/api/apps') return request.respond({contentType:'application/json', body:JSON.stringify({apps})});
      if (url.pathname === '/test-app') return request.respond({contentType:'text/html', body:'<!doctype html><title>Test App</title><h1>Test App</h1>'});
      return request.continue();
    });
    await page.goto(base + '/', {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => typeof window.cccOpenApp === 'function' && typeof window.cccSwitchCoreApp === 'function');
    await page.waitForSelector('.ccc-side-nav [data-app-id="test-app"]');
    await page.evaluate(() => {
      window.appSwitchDocument = document;
      window.appSwitchList = document.getElementById('convList');
      window.appSwitchDraft = document.getElementById('convInput');
      window.appSwitchDraft.value = 'Unsent draft kept across app switches';
    });
    let mainNavigations = 0;
    page.on('framenavigated', frame => {if (frame === page.mainFrame()) mainNavigations++;});
    const click = id => page.$eval('.ccc-side-nav [data-app-id="'+id+'"]', el => el.click());
    const assertKept = async () => assert.equal(await page.evaluate(() =>
      document === window.appSwitchDocument && document.getElementById('convList') === window.appSwitchList
      && window.appSwitchDraft.isConnected && window.appSwitchDraft.value === 'Unsent draft kept across app switches'), true);

    await click('test-app');
    await page.waitForFunction(() => document.getElementById('cccMobileAppFrame')?.contentDocument?.querySelector('h1'));
    assert.equal(await page.$eval('.ccc-side-nav [data-app-id="test-app"]', el => el.getAttribute('aria-current')), 'page');
    assert.equal(await page.$eval('#mobileBottomNav', el => getComputedStyle(el).display), 'none');
    const geometry = await page.evaluate(() => {
      const frame = document.getElementById('cccMobileAppFrame').getBoundingClientRect();
      const rail = document.querySelector('.ccc-side-nav').getBoundingClientRect();
      return {left:frame.left, railRight:rail.right, right:frame.right, bottom:frame.bottom, width:innerWidth, height:innerHeight};
    });
    assert.ok(Math.abs(geometry.left - geometry.railRight) < 1);
    assert.equal(geometry.right, geometry.width);
    assert.equal(geometry.bottom, geometry.height);
    await assertKept();
    if (process.env.CCC_APP_SWITCH_SCREENSHOT) {
      await page.screenshot({path:process.env.CCC_APP_SWITCH_SCREENSHOT.replace(/\.png$/, '-app.png')});
    }

    const returnMs = await page.$eval('.ccc-side-nav [data-app-id="sessions"]', el => new Promise(resolve => {
      const started = performance.now();
      el.click();
      requestAnimationFrame(() => resolve(performance.now() - started));
    }));
    assert.equal(await page.$eval('#cccMobileAppFrame', el => el.hidden), true);
    await assertKept();
    console.log('Return to mounted Sessions, click to animation frame: ' + Math.round(returnMs) + 'ms');
    await page.evaluate(() => history.back());
    await page.waitForFunction(() => !document.getElementById('cccMobileAppFrame').hidden);
    await page.evaluate(() => history.forward());
    await page.waitForFunction(() => document.getElementById('cccMobileAppFrame').hidden);
    await assertKept();

    await click('queues');
    await click('test-app');
    await page.evaluate(() => history.back());
    await page.waitForFunction(() => document.getElementById('cccMobileAppFrame').hidden && !document.getElementById('cccCoreAppFrame').hidden);
    assert.equal(new URL(page.url()).pathname, '/q2.html');
    await click('sessions');
    await assertKept();

    await click('test-app');
    await page.setViewport({width:390, height:844});
    await page.waitForFunction(() => document.body.classList.contains('has-mobile-bottom-nav'));
    const phone = await page.evaluate(() => ({frame:document.getElementById('cccMobileAppFrame').getBoundingClientRect().bottom, nav:document.getElementById('mobileBottomNav').getBoundingClientRect().top}));
    assert.ok(phone.frame <= phone.nav + 1);
    await page.$eval('[data-mobile-app="sessions"]', el => el.click());
    await assertKept();
    await page.$eval('[data-mobile-app="test-app"]', el => el.click());
    await page.setViewport({width:1440, height:1000});
    await page.waitForFunction(() => !document.body.classList.contains('has-mobile-bottom-nav'));
    await click('sessions');
    await assertKept();
    // pushState emits same-document navigation events too. The retained
    // document/node identities above distinguish those from a real reload.
    assert.ok(mainNavigations > 0);
    if (process.env.CCC_APP_SWITCH_SCREENSHOT) {
      await page.screenshot({path:process.env.CCC_APP_SWITCH_SCREENSHOT});
    }
    console.log('PASS: desktop/mobile app switches retain document, list and draft; rail, frame bounds, history and resizing work');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
