// Opt-in geometry regression: CCC_MOBILE_READER_TEST_URL=http://127.0.0.1:8091 node tests/mobile-reader-layout.browser.cjs
// API responses are disposable; no session messages or preferences are sent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const base = process.env.CCC_MOBILE_READER_TEST_URL;
if (!base) throw new Error('Set CCC_MOBILE_READER_TEST_URL to a running dashboard');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const fixture = `
  window.__mobileReaderTest = () => {
    const view = getConvView();
    view.closest('.conv-pane').classList.remove('has-new-session-context');
    view.closest('.conv-pane').querySelector('.conv-input-bar').classList.add('visible');
    view.innerHTML = Array.from({length:12}, (_, i) =>
      '<div class="event user_text"><div class="user-msg">Check the mobile conversation layout.</div></div>' +
      '<div class="event assistant"><div class="assistant-text"><p>The conversation should use the full phone width. Navigation stays available through Back, and the controls leave room to read.</p></div></div>').join('');
    document.body.classList.add('mobile-show-main');
    document.getElementById('mobileConvTitle').textContent = 'Check mobile layout';
    document.getElementById('mobileConvTitle').hidden = false;
    const ci = document.getElementById('localCiMobileReader');
    ci.hidden = false;
    ci.innerHTML = '<div class="local-ci-group" data-outcome="success"><details><summary class="local-ci-summary">CI · All checks passed · 100%</summary></details></div>';
    view.scrollTop = 180;
    view._pinnedToBottom = false;
    window.__mobileReaderControls = () => {
      for (const control of [view._convEndButton, view._convNextButton, view._convLastButton, view._convSegmentButton]) {
        control.style.transition = 'none';
        control.classList.add('visible');
      }
      positionConversationEndAffordance(view);
    };
    view._convLastButton.querySelector('span').textContent = 'Previous';
    positionConversationEndAffordance(view);
  };
`;
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.evaluateOnNewDocument(() => {
      if (window.self !== window.top || !/^https?:$/.test(location.protocol)) return;
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-engines-first-run-done', '1');
      localStorage.setItem('ccc-ui-mode', 'advanced');
    });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname === '/static/app.js') return request.respond({contentType:'text/javascript', body:source.replace('  function positionConversationEndAffordance(view) {', fixture + '  function positionConversationEndAffordance(view) {')});
      if (url.pathname === '/api/features') return request.respond({contentType:'application/json', body:JSON.stringify({preview:{idea_tickets:true}})});
      if (url.pathname === '/api/apps') return request.respond({contentType:'application/json', body:JSON.stringify({apps:[{id:'sessions',label:'Sessions',url:'/'},{id:'queues',label:'Queues',url:'/q2.html'},{id:'example',label:'Example app',url:'/example.html'}]})});
      if (url.pathname.startsWith('/api/')) return request.respond({contentType:'application/json', body:JSON.stringify(url.pathname === '/api/sessions' ? [] : url.pathname === '/api/conversations/list' ? {conversations:[], total:0} : {})});
      return request.continue();
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const width of [375, 390, 430, 700, 701, 1440]) {
      await page.setViewport({width, height:900, isMobile:width<=700, hasTouch:width<=700});
      await page.goto(base + '/', {waitUntil:'domcontentloaded'});
      await page.waitForSelector('.conv-scroll-end-btn');
      await page.waitForFunction(() => !!document.querySelector('.ccc-side-nav'));
      await page.evaluate(() => window.__mobileReaderTest());
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.main')).transform === 'matrix(1, 0, 0, 1, 0, 0)' || getComputedStyle(document.querySelector('.main')).transform === 'none');
      const geometry = await page.evaluate(() => {
        window.__mobileReaderControls();
        const rect = el => el.getBoundingClientRect().toJSON();
        return {
          width:innerWidth, main:rect(document.querySelector('.main')),
          railVisible:getComputedStyle(document.querySelector('.ccc-side-nav')).display !== 'none',
          passedVisible:getComputedStyle(document.getElementById('localCiMobileReader')).display !== 'none',
          buttons:[...document.querySelectorAll('.conv-pane > :is(.conv-scroll-end-btn, .conv-scroll-next-btn, .conv-scroll-last-btn, .conv-scroll-segment-btn)')].map(rect),
          back:rect(document.getElementById('mobileBackBtn'))
        };
      });
      if (width <= 700) {
        assert.equal(geometry.main.left, 0, 'reader begins at phone edge');
        assert.equal(geometry.main.right, width, 'reader fills phone');
        assert.equal(geometry.railVisible, false);
        assert.equal(geometry.passedVisible, false, 'passed CI does not consume reader height');
        for (const button of geometry.buttons) {
          assert.equal(button.width, 44);
          assert.equal(button.height, 44);
          assert.ok(button.left >= 0 && button.right <= width);
          assert.ok(Math.abs(button.top - geometry.buttons[0].top) < 1, 'one row');
        }
        const sorted = [...geometry.buttons].sort((a,b) => a.left-b.left);
        for (let i=1; i<sorted.length; i++) assert.ok(sorted[i].left >= sorted[i-1].right, 'controls do not overlap');
        assert.ok(geometry.back.width >= 44);
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-reader-after.png'});
        for (const outcome of ['active', 'failure']) {
          assert.equal(await page.evaluate(outcome => {
            const ci = document.getElementById('localCiMobileReader');
            ci.firstElementChild.dataset.outcome = outcome;
            return getComputedStyle(ci).display !== 'none' && getComputedStyle(ci.firstElementChild).display !== 'none';
          }, outcome), true, outcome + ' CI remains visible');
        }
        await page.click('#mobileBackBtn');
        assert.equal(await page.evaluate(() => document.body.classList.contains('mobile-show-main')), false, 'Back returns to list');
        assert.equal(await page.$eval('.ccc-side-nav', el => getComputedStyle(el).display), 'none', 'home has no desktop rail');
        for (const key of ['coding', 'smallbugs', 'workers']) {
          assert.equal(await page.$eval('#mobileBottomNav > [data-mobile-nav="'+key+'"]', el => getComputedStyle(el).display !== 'none'), true, key + ' bottom button');
        }
        assert.equal(await page.$eval('[data-mobile-nav="coding"] .mobile-nav-label', el => el.textContent), 'Features');
        await page.waitForSelector('[data-mobile-app="example"]');
        await page.click('[data-mobile-nav-menu]');
        assert.equal(await page.$eval('#mobileBottomNav .mobile-nav-menu', el => el.hidden), false, 'hamburger opens');
        assert.equal(await page.$eval('[data-mobile-app="example"]', el => {
          el.scrollIntoView({block:'center'});
          const r = el.getBoundingClientRect();
          return el.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
        }), true, 'apps are reachable in hamburger');
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-home-after.png'});
        await page.click('[data-mobile-nav-menu]');
        await page.evaluate(() => window.cccSetUiMode('simple'));
        assert.equal(await page.$eval('.ccc-side-nav', el => getComputedStyle(el).display), 'none', 'Simple navigation preserved');
      } else {
        assert.equal(geometry.railVisible, width > 1200, 'desktop retains app rail; tablet uses mobile navigation');
        assert.equal(geometry.passedVisible, width <= 1200, 'CI uses the existing mobile/desktop hosts');
      }
      console.log('PASS width ' + width);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
