// Explicit live-browser regression check: CCC_SETTINGS_TEST_URL=http://127.0.0.1:8196 node tests/settings-navigation.browser.cjs
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  const base = process.env.CCC_SETTINGS_TEST_URL;
  if (!base) throw new Error('Set CCC_SETTINGS_TEST_URL to a running verification dashboard');
  const browser = await puppeteer.launch({executablePath:findChromePath(), headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:1000});
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done','1');
      localStorage.setItem('ccc-engines-first-run-done','1');
    });
    await page.goto(base+'/?ccc_settings=defaults',{waitUntil:'domcontentloaded'});
    await page.waitForSelector('#settingsModal.open');
    const visibleTabs = () => page.evaluate(() => [...document.querySelectorAll('#settingsRail .settings-rail-item')].filter(el=>el.offsetParent).map(el=>el.textContent.trim()));
    assert.deepEqual(await visibleTabs(),['Agents','Workspace','Notifications & voice','Connections','Usage & budgets','System & help']);
    assert.equal(await page.$eval('.settings-usage-disclosure',el=>el.open),false);
    for (const id of ['appearance','notifications','vault','sessions','maintenance','defaults']) {
      await page.click('[data-settings-destination="'+id+'"]');
      assert.equal(await page.$eval('[data-section-id="'+id+'"]',el=>el.classList.contains('is-active-section')),true);
    }
    await page.type('#settingsSearchInput','routing');
    assert.equal(await page.$eval('#settingsSection-routing > details',el=>el.open),true);
    await page.$eval('#settingsSearchInput',el=>{el.value='';el.dispatchEvent(new Event('input',{bubbles:true}))});
    assert.equal(await page.$eval('#settingsSection-routing > details',el=>el.open),false);
    // Closing while filtered must not make nonmatching controls permanently hidden.
    await page.type('#settingsSearchInput','routing');
    await page.click('#settingsModalClose');
    await page.$eval('#settingsBtn',el=>el.click());
    await page.click('[data-settings-destination="defaults"]');
    assert.equal(await page.$eval('#spawnDefaultsEngine',el=>!!el.offsetParent),true);
    // Multi-word matching need not be a contiguous phrase.
    await page.type('#settingsSearchInput','session engine');
    assert.equal(await page.$eval('#spawnDefaultsEngine',el=>!!el.offsetParent),true);
    await page.click('[data-settings-destination="appearance"]');
    await page.focus('[data-settings-destination="appearance"]');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.settingsDestination),'notifications');
    // Existing adapters still open their correct disclosure.
    for (const id of ['engines','queues','routing']) {
      await page.$eval('#settingsRailTab-'+id,el=>el.click());
      assert.equal(await page.$eval('#settingsSection-'+id+' > details',el=>el.open),true);
    }
    await page.setViewport({width:390,height:844});
    await page.click('[data-settings-destination="vault"]');
    assert.equal(await page.$eval('.settings-modal',el=>el.scrollWidth<=el.clientWidth+1),true);
    assert.equal((await visibleTabs()).length,6);
    console.log('PASS: six destinations, switching, collapsed reports, search restoration, multi-word search, keyboard, legacy adapters, mobile width');
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
