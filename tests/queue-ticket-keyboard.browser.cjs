// Exercise the production composer and stylesheet without filing real tickets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(process.env.TICKET_APP_JS || 'static/app.js', 'utf8');
const css = fs.readFileSync(process.env.TICKET_APP_CSS || 'static/app.css', 'utf8');
const start = app.indexOf('  function openQueueTicketComposer(opts)');
assert.ok(start > 0);
const composer = app.slice(start, app.indexOf('\n  }\n', start) + 5);

(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', r => r.respond({status:200, contentType:'text/html', body:
      '<meta name="viewport" content="width=device-width,initial-scale=1"><body></body>'}));
    async function setup(width, height, mobile) {
      await page.setViewport({width,height,isMobile:mobile,hasTouch:mobile});
      await page.goto('http://localhost/ticket-fixture');
      await page.addStyleTag({content:css});
      await page.evaluate(() => {
        window._ticketVisibleSessionContext = () => '\n\nOpen sessions:\n- example-session';
        window._rankTicketQueues = names => ({top:[],rest:names});
        window.escapeHtml = s => s;
        window.escapeAttr = s => s;
        window.guardComposerSend = () => true;
        window.isImeKey = ev => ev.isComposing;
        window.fetch = async () => ({json:async () => ({queues:[]})});
      });
      await page.addScriptTag({content:composer + '\nwindow.openQueueTicketComposer = openQueueTicketComposer;'});
      await page.evaluate(() => {
        window.result = null;
        openQueueTicketComposer({queues:['TEST','OTHER'],defaultQueue:'TEST'}).then(r => window.result = r);
      });
      await page.waitForFunction(() => document.activeElement?.id === 'fqTicketNote');
      await page.type('#fqTicketNote', 'Keep this draft');
    }
    for (const [width,height,visible,offset] of [[375,812,380,60],[320,568,260,100],[844,390,210,0]]) {
      await setup(width,height,true);
      // Model Safari's unchanged layout viewport plus the app shell's visual
      // viewport variables. Chromium headless cannot open an iOS keyboard.
      await page.evaluate(({visible,offset}) => {
        document.documentElement.style.setProperty('--app-vh', visible + 'px');
        document.documentElement.style.setProperty('--app-vv-top', offset + 'px');
      }, {visible,offset});
      if (process.env.TICKET_SCREENSHOT_DIR) await page.screenshot({path:
        `${process.env.TICKET_SCREENSHOT_DIR}/ticket-${width}.png`});
      const bounds = await page.evaluate(() => {
        const rect = selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height};
        };
        return {footer:rect('.fq-ticket-actions'),header:rect('.fq-ticket-header'),
          dialog:rect('.fq-ticket-dialog'),body:rect('.fq-ticket-body')};
      });
      console.log(JSON.stringify({width,height,visible,offset,bounds}));
      assert.ok(bounds.footer.bottom <= offset + visible, 'actions stay above keyboard');
      assert.ok(bounds.header.top >= offset, 'header stays visible after Safari pans');
      assert.ok(bounds.dialog.left >= 0 && bounds.dialog.right <= width, 'no horizontal overflow');
      assert.ok(bounds.body.height > 0, 'fields remain scrollable');
      const before = await page.$eval('#fqTicketNote', el => el.value);
      await page.tap('[data-fq-ticket-hide-keyboard]');
      assert.equal(await page.evaluate(() => document.activeElement?.id === 'fqTicketNote'), false);
      assert.equal(await page.$eval('#fqTicketNote', el => el.value), before, 'hide preserves text');
      assert.equal(await page.evaluate(() => result), null, 'hide does not dismiss or submit');
      await page.select('#fqTicketQueue','OTHER');
      await page.evaluate(() => { document.documentElement.style.removeProperty('--app-vh');
        document.documentElement.style.removeProperty('--app-vv-top'); });
      await page.tap('[data-fq-ticket-submit]');
      await page.waitForFunction(() => result !== null);
      assert.equal(await page.evaluate(() => result), before.trim());
      assert.equal(await page.evaluate(() => openQueueTicketComposer.lastQueue), 'OTHER');
      assert.equal(await page.$('.fq-ticket-composer'), null);
    }
    await setup(1280,800,false);
    assert.equal(await page.$eval('.fq-ticket-hide-keyboard', e => getComputedStyle(e).display), 'none');
    const header = await page.$('.fq-ticket-header');
    const box = await header.boundingBox();
    const initial = await page.$eval('.fq-ticket-dialog', el => el.getBoundingClientRect().left);
    await page.mouse.move(box.x + 50,box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 100,box.y + box.height / 2 + 20);
    await page.mouse.up();
    assert.equal(await page.$eval('.fq-ticket-dialog', el => el.getBoundingClientRect().left), initial + 50);
    await page.click('.fq-ticket-close');
    assert.equal(await page.$('.fq-ticket-composer'), null, 'desktop close still works');
    console.log('PASS: keyboard bounds, panning, narrow/landscape, blur without loss, queue, submit, desktop drag/close');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode = 1;});
