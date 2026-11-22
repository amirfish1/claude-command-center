// Deterministic microphone lifecycle checks; actual speech services require a device.
const assert = require('node:assert/strict');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
    await page.setRequestInterception(true);
    page.on('request', request => request.respond({status:200,contentType:'text/html',body:'<body class="has-mobile-bottom-nav"><button id="sidebarNewTicketBtn">Ticket</button><button id="settingsTicketRecordToggle" role="switch" aria-checked="false">Voice ticket button</button></body>'}));
    async function setup(native = false, enabled = true) {
      await page.goto('http://localhost/fixture');
      await page.evaluate(native => {
        window.starts = 0; window.stops = 0; window.inputs = 0;
        Object.defineProperty(navigator, 'mediaDevices', {value:undefined, configurable:true});
        document.getElementById('sidebarNewTicketBtn').onclick = () => {
          const input = document.createElement('textarea'); input.id = 'fqTicketNote';
          input.value = '\n\nOpen sessions:\n- test';
          input.oninput = () => window.inputs++;
          document.body.append(input);
        };
        if (native) {
          Object.defineProperty(navigator, 'userAgent', {value:'CCC-iOS'});
          window.Capacitor = {Plugins:{SpeechRecognition:{
            requestPermissions:() => new Promise(resolve => { window.grant = resolve; }),
            addListener:async () => ({remove:async () => {}}),
            start:async () => { window.starts++; }, stop:async () => { window.stops++; }
          }}};
        } else {
          window.SpeechRecognition = class {
            constructor() { window.recognition = this; }
            start() { window.starts++; }
            stop() { window.stops++; }
            abort() { this.onend(); }
          };
        }
        // Synthetic pointer events don't own real browser pointer capture.
        Element.prototype.setPointerCapture = () => {};
      }, native);
      await page.evaluate(enabled => {
        if (enabled === null) return;
        if (enabled) localStorage.setItem('ccc-mobile-ticket-record-enabled', '1');
        else localStorage.removeItem('ccc-mobile-ticket-record-enabled');
      }, enabled);
      await page.addScriptTag({path:path.resolve(__dirname,'../static/mobile-ticket-record.js')});
    }
    async function pointer(type) {
      await page.evaluate(type => document.getElementById('mobileTicketRecord').dispatchEvent(new PointerEvent(type, {pointerId:1,button:0})), type);
    }
    const visible = () => page.$eval('#mobileTicketRecord', e => e.checkVisibility());
    const background = () => page.$eval('#mobileTicketRecord', e => getComputedStyle(e).backgroundColor);
    await setup(false, false);
    assert.equal(await visible(), false, 'fresh browser hides Hold');
    await page.evaluate(() => document.getElementById('mobileTicketRecord').click());
    assert.equal(await page.evaluate(() => starts), 0, 'disabled shortcut cannot start speech');
    await page.click('#settingsTicketRecordToggle');
    assert.equal(await visible(), true);
    assert.equal(await page.$eval('#settingsTicketRecordToggle', e => e.getAttribute('aria-checked')), 'true');
    await setup(false, null);
    assert.equal(await visible(), true, 'opt-in survives reload');
    await pointer('pointerdown');
    await page.evaluate(() => recognition.onresult({results:[[{transcript:'Discard when disabled'}]]}));
    await page.click('#settingsTicketRecordToggle');
    await page.evaluate(() => recognition.onend());
    assert.equal(await visible(), false);
    assert.equal(await page.$('#fqTicketNote'), null, 'disabling cancels the recording');
    await setup(false, null);
    assert.equal(await visible(), false, 'opt-out survives reload');
    await page.evaluate(() => {
      localStorage.setItem('ccc-mobile-ticket-record-enabled', '1');
      dispatchEvent(new StorageEvent('storage', {key:'ccc-mobile-ticket-record-enabled'}));
    });
    assert.equal(await visible(), true, 'other-tab preference updates visibility');
    await page.evaluate(() => document.body.classList.add('has-upd-overlay-open'));
    assert.equal(await visible(), false, 'overlay still hides an enabled shortcut');
    await setup();
    assert.equal(await background(), 'rgb(140, 219, 156)', 'idle button is green');
    await pointer('pointerdown');
    assert.equal(await page.evaluate(() => starts), 1);
    await page.evaluate(() => { recognition.onstart(); recognition.onresult({results:[[{transcript:'Fix the mobile queue'}]]}); });
    assert.equal(await background(), 'rgb(255, 133, 133)', 'recording button is red on mobile');
    await pointer('pointerup');
    assert.equal(await background(), 'rgb(140, 219, 156)', 'button resets when recording ends');
    assert.equal(await page.evaluate(() => stops), 1);
    assert.equal(await page.$('#fqTicketNote'), null, 'waits for final recognition');
    await page.evaluate(() => { recognition.onresult({results:[[{transcript:'Fix the mobile queue button'}]]}); recognition.onend(); });
    await page.waitForSelector('#fqTicketNote');
    assert.match(await page.$eval('#fqTicketNote', e => e.value), /^Fix the mobile queue button\n\nOpen sessions:/);
    assert.equal(await page.evaluate(() => inputs), 1, 'triggers existing routing');
    await setup();
    await pointer('pointerdown');
    await page.evaluate(() => { recognition.onstart(); recognition.onresult({results:[[{transcript:'Discard this'}]]}); });
    await pointer('pointercancel');
    await page.evaluate(() => recognition.onend());
    assert.equal(await page.$('#fqTicketNote'), null, 'cancel does not create draft');
    await setup();
    await pointer('pointerdown');
    await pointer('pointerup');
    await page.evaluate(() => { recognition.onstart(); recognition.onend(); });
    assert.equal(await page.$('#fqTicketNote'), null);
    assert.equal(await page.$eval('#mobileTicketRecord', e => e.getAttribute('aria-pressed')), 'false');
    await setup();
    await pointer('pointerdown');
    await page.evaluate(() => { recognition.onerror({error:'not-allowed'}); recognition.onend(); });
    assert.match(await page.$eval('#mobileTicketRecordStatus', e => e.textContent), /Speech input was not allowed/);
    await setup();
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'userAgent', {value:'iPhone'});
      Object.defineProperty(navigator, 'standalone', {value:true});
    });
    await pointer('pointerdown');
    await page.evaluate(() => { recognition.onerror({error:'not-allowed'}); recognition.onend(); });
    assert.match(await page.$eval('#mobileTicketRecordStatus', e => e.textContent), /HTTPS address.*direct microphone recording/);
    await setup();
    await pointer('pointerdown');
    await page.evaluate(() => {
      recognition.onresult({results:[[{transcript:'Do not submit while dragging'}]]});
      document.getElementById('mobileTicketRecord').dispatchEvent(new PointerEvent('pointermove', {pointerId:1, clientX:-10000, clientY:-10000}));
    });
    await pointer('pointerup');
    await page.evaluate(() => recognition.onend());
    assert.equal(await page.$('#fqTicketNote'), null, 'drag cancels the recording');
    const moved = await page.$eval('#mobileTicketRecord', e => ({left:e.getBoundingClientRect().left, top:e.getBoundingClientRect().top}));
    assert.equal(moved.left,8); assert.equal(moved.top,8);
    await setup(true);
    await pointer('pointerdown');
    await pointer('pointerup');
    await page.evaluate(() => grant({speechRecognition:'granted'}));
    await page.waitForFunction(() => stops === 1);
    assert.equal(await page.evaluate(() => starts), 0, 'release during permission cannot start native microphone');
    console.log('PASS: final transcript, existing input routing, cancel, early release, denied access, native permission race');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
