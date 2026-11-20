// Local browser check. All requests are blocked; this never visits the live site.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  const out = path.resolve(__dirname, '../.claude/screenshots/launch-captures');
  fs.mkdirSync(out, {recursive:true});
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage();
      await page.setViewport({width,height:width === 390 ? 844 : 1000,deviceScaleFactor:width === 390 ? 3 : 2});
      await page.setRequestInterception(true);
      let actionRequests = 0;
      let testing = false;
      page.on('request', req => {
        if (testing) actionRequests++;
        if (req.url().startsWith('file:')) req.continue(); else req.abort();
      });
      await page.goto(pathToFileURL(path.resolve(__dirname, '../docs/index.html')).href);
      await page.$eval('#try-oncall', el => el.scrollIntoView());
      const overflow = await page.$eval('#try-oncall', el => el.scrollWidth > el.clientWidth);
      assert.equal(overflow, false, 'section overflow');
      await page.screenshot({path:path.join(out, `try-it-${width}.png`)});
      testing = true;
      await page.focus('#try-reply');
      await page.keyboard.type('Keep it <script>example</script>');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelector('#try-status').textContent.includes('next turn'));
      assert.match(await page.$eval('#try-status', el => el.textContent), /<script>example<\/script>/);
      assert.equal(await page.$eval('#try-status', el => el.children.length), 0);
      await page.waitForFunction(() => !document.querySelector('#try-notice').hidden);
      assert.match(await page.$eval('#try-status', el => el.textContent), /not been told/);
      await page.click('#try-no');
      assert.match(await page.$eval('#try-status', el => el.textContent), /receives nothing/);
      await page.click('#try-reset');
      await page.click('#try-suggest');
      await page.click('#try-reset');
      await page.emulateMediaFeatures([{name:'prefers-reduced-motion', value:'reduce'}]);
      await page.click('#try-suggest');
      await page.waitForFunction(() => !document.querySelector('#try-notice').hidden);
      await page.click('#try-yes');
      assert.match(await page.$eval('#try-status', el => el.textContent), /Example notice:/);
      assert.equal(actionRequests, 0, 'reply must not make requests');
      await page.close();
      console.log(`PASS ${width}: keyboard, literal text, notice approval, reset, reduced motion, no reply requests`);
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
