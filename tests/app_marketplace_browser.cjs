/* Run through test_app_marketplace.py against its disposable HTTP fixture. */
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
(async () => {
  const browser = await puppeteer.launch({headless:true, executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1100, height:850});
    const base = process.argv[2];
    await page.goto(base + '/view/session-summary');
    const element = await page.waitForSelector('#app-frame');
    const frame = await element.contentFrame();
    await frame.waitForFunction(() => document.getElementById('status')?.textContent === 'Snapshot loaded.');
    assert.match(await frame.$eval('#sessions', el => el.textContent), /Fixture session/);
    assert.equal(await frame.evaluate(() => window.origin), 'null');
    assert.equal(await frame.evaluate(() => {
      try { return parent.document.body.textContent; } catch (_) { return 'denied'; }
    }), 'denied');
    assert.equal(await frame.evaluate(async base => {
      try { await fetch(base + '/api/sessions/spawn', {method:'POST', body:'{}'}); return 'allowed'; }
      catch (_) { return 'denied'; }
    }, base), 'denied');
    const denied = await frame.evaluate(() => new Promise(resolve => {
      window.addEventListener('message', function receive(event) {
        if (event.data?.requestId !== 'ungranted') return;
        window.removeEventListener('message', receive);
        resolve(event.data);
      });
      parent.postMessage({type:'ccc.app.call', requestId:'ungranted', method:'queues.read'}, '*');
    }));
    assert.equal(denied.ok, false);
    assert.equal(denied.error, 'Permission denied');
    for (const method of ['spawn', '/api/sessions/spawn', 'queues.read']) {
      const result = await frame.evaluate(method => new Promise(resolve => {
        const requestId = 'escalate-' + method;
        window.addEventListener('message', function receive(event) {
          if (event.data?.requestId !== requestId) return;
          window.removeEventListener('message', receive);
          resolve(event.data);
        });
        parent.postMessage({type:'ccc.app.call', requestId, method,
          id:'queue-summary', permissions:['queues.read', 'spawn'],
          generation:'forged', url:'/api/sessions/spawn', body:{prompt:'must never run'}}, '*');
      }), method);
      assert.equal(result.ok, false);
      assert.equal(result.error, 'Permission denied');
    }
    // No direct GET is readable, either; CSP prevents the network request.
    assert.equal(await frame.evaluate(async base => {
      try { await fetch(base + '/api/app-marketplace/index'); return 'allowed'; }
      catch (_) { return 'denied'; }
    }, base), 'denied');

    await page.goto(base + '/view/queue-summary');
    const queue = await (await page.waitForSelector('#app-frame')).contentFrame();
    await queue.waitForFunction(() => document.getElementById('status')?.textContent === 'Snapshot loaded.');
    assert.match(await queue.$eval('#items', el => el.textContent), /Fixture queue item/);
    assert.equal(await queue.evaluate(() => window.origin), 'null');

    await page.goto(base + '/applications');
    await page.click('#browseApps');
    await page.waitForSelector('.marketplace-card button');
    assert.equal(await page.$$eval('.marketplace-card button', buttons =>
      buttons.filter(button => button.textContent === 'Installed' && button.disabled).length), 2);
    assert.equal(await page.$$eval('.marketplace-card a', links => links.length), 2);
    if (process.env.CCC_MARKETPLACE_SCREENSHOT) await page.screenshot({path:process.env.CCC_MARKETPLACE_SCREENSHOT, fullPage:true});
    const dialog = new Promise(resolve => page.once('dialog', async prompt => {
      assert.match(prompt.message(), /Requested permissions:\nNone/);
      await prompt.accept();
      resolve();
    }));
    await page.click('.marketplace-card button');
    await dialog;
    await page.waitForFunction(() => document.querySelector('.marketplace-card button').textContent === 'Installed');
    await page.goto(base + '/view/effects-kit');
    const fx = await (await page.waitForSelector('#app-frame')).contentFrame();
    await fx.waitForFunction(() => !!window.cccFx);
    assert.equal(await fx.evaluate(() => window.origin), 'null');
    await fx.click('#muteBtn');
    assert.equal(await fx.evaluate(() => window.cccFx.muted()), true);
    await fx.click('#countBtn');
    await fx.waitForFunction(() => document.getElementById('countTarget').textContent === '12.40');
    // A direct top-level visit is still sandboxed by the response header.
    await page.goto(base + '/marketplace-content/effects-kit/index.html');
    assert.equal(await page.evaluate(() => window.origin), 'null');
    assert.equal(await page.evaluate(() => {
      try { localStorage.getItem('test'); return 'allowed'; } catch (_) { return 'denied'; }
    }), 'denied');
    await page.goto(base + '/app-static/effects-kit/icon.svg');
    assert.equal(await page.evaluate(() => window.origin), 'null');
    await page.setViewport({width:390, height:844});
    await page.goto(base + '/applications');
    await page.click('#browseApps');
    await page.waitForSelector('.marketplace-card');
    assert.equal(await page.$$eval('.marketplace-card button', buttons =>
      buttons.every(button => button.textContent === 'Installed' && button.disabled)), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.CCC_MARKETPLACE_MOBILE_SCREENSHOT) await page.screenshot({path:process.env.CCC_MARKETPLACE_MOBILE_SCREENSHOT, fullPage:true});
    console.log('Browser isolation, consent, denied bridge, direct SVG and mobile layout passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
