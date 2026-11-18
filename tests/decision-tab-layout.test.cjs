const assert = require('node:assert/strict');
const { test } = require('node:test');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

test('inactive Decisions views avoid scroll geometry and mounted frames still follow their host', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', request => request.respond({status:200, contentType:'text/html',
      body:'<div id="convList"><div class="ordinary-row">Session</div></div>'}));
    await page.goto('http://ccc.test/');
    await page.evaluate(() => {
      window.scrollReads = 0;
      const scroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollLeft');
      Object.defineProperty(document.getElementById('convList'), 'scrollLeft', {
        get() { scrollReads++; return scroll.get.call(this); },
        set(value) { scroll.set.call(this, value); },
      });
    });
    await page.addScriptTag({path:require.resolve('../static/decision-tab.js')});
    await page.evaluate(() => {
      CCCDecisionTab.mount();
      window.dispatchEvent(new Event('resize'));
      document.querySelector('.ordinary-row').textContent = 'Updated session';
    });
    assert.equal(await page.evaluate(() => scrollReads), 0);
    await page.evaluate(() => {
      const host = document.createElement('div');
      host.id = 'sidebarDecisionGuideHost';
      host.style.cssText = 'width:320px;height:600px';
      document.getElementById('convList').append(host);
      CCCDecisionTab.mount();
    });
    assert.deepEqual(await page.evaluate(() => {
      const host = document.getElementById('sidebarDecisionGuideHost').getBoundingClientRect();
      const frame = document.getElementById('cccDecisionGuideFrame');
      const rect = frame.getBoundingClientRect();
      return [frame.hidden, rect.left === host.left, rect.top === host.top,
        rect.width === host.width, rect.height === host.height, scrollReads > 0];
    }), [false, true, true, true, true, true]);
    await page.evaluate(() => {
      document.getElementById('sidebarDecisionGuideHost').remove();
      CCCDecisionTab.mount();
      scrollReads = 0;
      window.dispatchEvent(new Event('resize'));
    });
    assert.deepEqual(await page.evaluate(() => [
      document.getElementById('cccDecisionGuideFrame').hidden, scrollReads,
    ]), [true, 0]);
  } finally { await browser.close(); }
});
