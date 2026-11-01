const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const positions = app.slice(app.indexOf('  const VOICE_BUTTON_POSITION_KEY'), app.indexOf('  async function voicePollStatus()'));
const prefs = app.slice(app.indexOf('  const VOICE_MOBILE_BTN_STORAGE_KEY'), app.indexOf('  function applyTheme('));
const wiringStart = app.indexOf('  if ($voiceMobileBtn) {', app.indexOf('  // -- wiring '));
const wiring = app.slice(wiringStart, app.indexOf('  // Settings → Layout toggle for the floating CALL button', wiringStart));

test('Call button hold hides persistently without calling; tap and drag remain distinct', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, hasTouch: true, isMobile: true });
    await page.setRequestInterception(true);
    page.on('request', req => req.respond({ status: 200, contentType: 'text/html', body:
      `<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>
      <button id="voiceMobileBtn" class="voice-mobile-btn">CALL</button>
      <button id="settingsVoiceMobileToggle">Show voice call button</button>` }));
    const boot = async () => {
      await page.goto('http://ccc-voice.test/');
      await page.addScriptTag({ content: `
        const $voiceMobileBtn = document.getElementById('voiceMobileBtn');
        const Voice = {sessionId: null, state: 'idle'};
        window.calls = 0;
        function voiceStart() { window.calls++; }
        function voiceOpenPanel() {}
        function showOpToast() {}
        ${positions}\n${prefs}\n${wiring}
        applyVoiceMobileBtn(getVoiceMobileBtnPref());
        document.getElementById('settingsVoiceMobileToggle').onclick = () => setVoiceMobileBtnPref(true);
      ` });
    };
    const buttonCenter = () => page.$eval('#voiceMobileBtn', el => {
      const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    });
    await boot();
    let c = await buttonCenter();
    await page.touchscreen.tap(c.x, c.y);
    assert.equal(await page.evaluate(() => window.calls), 1, 'ordinary tap calls');
    await page.touchscreen.touchStart(c.x, c.y);
    await page.waitForSelector('#voiceButtonOptions:not([hidden])');
    await page.touchscreen.touchEnd();
    assert.equal(await page.evaluate(() => window.calls), 1, 'holding never calls');
    const rect = await page.$eval('#voiceButtonOptions', el => {
      const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    });
    assert.ok(rect.left >= 0 && rect.right <= 390 && rect.top >= 0 && rect.bottom <= 844);
    if (process.env.VOICE_BUTTON_SCREENSHOT) await page.screenshot({path: process.env.VOICE_BUTTON_SCREENSHOT});
    await page.click('#voiceButtonOptions button');
    assert.equal(await page.$eval('#voiceMobileBtn', el => getComputedStyle(el).display), 'none');
    assert.equal(await page.evaluate(() => localStorage.getItem('ccc-voice-mobile-btn')), '0');
    await boot();
    assert.equal(await page.$eval('#voiceMobileBtn', el => getComputedStyle(el).display), 'none', 'hidden after reload');
    await page.click('#settingsVoiceMobileToggle');
    c = await buttonCenter();
    await page.mouse.move(c.x, c.y); await page.mouse.down();
    await page.mouse.move(c.x - 90, c.y - 100, {steps: 5}); await page.mouse.up();
    assert.equal(await page.evaluate(() => window.calls), 0, 'drag never calls');
    assert.equal(await page.$eval('#voiceButtonOptions', el => el.hidden), true);
    assert.ok(await page.evaluate(() => localStorage.getItem('ccc-voice-button-position')));
    await page.focus('#voiceMobileBtn');
    await page.keyboard.down('Shift'); await page.keyboard.press('F10'); await page.keyboard.up('Shift');
    assert.equal(await page.$eval('#voiceButtonOptions', el => el.hidden), false, 'keyboard opens options');
    await page.keyboard.press('Escape');
    assert.equal(await page.$eval('#voiceButtonOptions', el => el.hidden), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'voiceMobileBtn');
    c = await buttonCenter();
    await page.mouse.click(c.x, c.y, {button: 'right'});
    assert.equal(await page.$eval('#voiceButtonOptions', el => el.hidden), false, 'right click opens options');
    await page.click('#settingsVoiceMobileToggle');
    assert.equal(await page.$eval('#voiceButtonOptions', el => el.hidden), true, 'outside click dismisses');
  } finally { await browser.close(); }
});
