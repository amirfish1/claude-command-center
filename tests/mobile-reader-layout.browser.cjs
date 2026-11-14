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
    ci.classList.add('is-expanded');
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
      localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      localStorage.setItem('ccc-engines-first-run-done', '1');
      localStorage.setItem('ccc-ui-mode', 'advanced');
      const viewport = new EventTarget();
      Object.assign(viewport, {height:900, width:innerWidth, offsetTop:0, offsetLeft:0, scale:1});
      Object.defineProperty(window, 'visualViewport', {value:viewport, configurable:true});
    });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname === '/static/app.js') return request.respond({contentType:'text/javascript', body:source.replace('  function positionConversationEndAffordance(view) {', fixture + '  function positionConversationEndAffordance(view) {')});
      if (url.pathname === '/api/headroom') return request.respond({contentType:'application/json', body:JSON.stringify({ok:true, rows:[
        {id:'claude',engine:'claude',available:true,percent_left:72},
        {id:'codex',engine:'codex',available:true,percent_left:61},
        {id:'devin',engine:'devin',available:true,percent_left:83}]})});
      if (url.pathname === '/api/ci/local') return request.respond({contentType:'application/json',body:JSON.stringify({jobs:[
        {id:'fixture:1',repo:'example/project',head_sha:'a'.repeat(40),name:'Tests',execution:'github_hosted',percent:98,workflow_percent:98,workflow_started_at:new Date(Date.now()-670000).toISOString()},
        {id:'fixture:2',repo:'example/project',head_sha:'b'.repeat(40),name:'Tests',execution:'self_hosted',percent:84,workflow_percent:84,workflow_started_at:new Date(Date.now()-353000).toISOString()}],completed:[]})});
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
        assert.equal(await page.$eval('.sidebar-header', el => getComputedStyle(el).visibility), 'hidden', 'reader hides home branding');
        assert.equal(geometry.main.top, 0, 'reader uses the whole viewport');
        assert.equal(await page.$eval('#convSteerBtn', el => getComputedStyle(el).display), 'none', 'mobile reserves space for Submit');
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
          }, outcome), false, outcome + ' CI stays on Home, not in a session');
        }
        // Simulate a phone keyboard leaving less than half the layout viewport.
        await page.evaluate(() => {
          const input = document.getElementById('convInput');
          input.focus(); input.value = Array.from({length:12}, (_, i) => 'Draft line ' + i).join('\n');
          input.dispatchEvent(new Event('input', {bubbles:true}));
          input.setSelectionRange(input.value.length, input.value.length);
          Object.assign(visualViewport, {height:360, offsetTop:24});
          visualViewport.dispatchEvent(new Event('resize'));
        });
        await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--app-vh') === '304px');
        const keyboard = await page.evaluate(() => {
          const input = document.getElementById('convInput'), bar = document.getElementById('convInputBar');
          const rect = el => el.getBoundingClientRect().toJSON();
          return {main:rect(document.querySelector('.main')),
            input:rect(input), bar:rect(bar), send:rect(document.getElementById('convSendBtn')),
            scrolling:input.scrollHeight > input.clientHeight};
        });
        assert.equal(keyboard.main.top, 24);
        assert.ok(keyboard.input.height > 20, 'draft keeps usable height');
        assert.ok(keyboard.input.bottom <= 328, 'draft above keyboard accessory bar');
        assert.ok(keyboard.send.bottom <= 328, 'Submit above keyboard');
        assert.ok(keyboard.scrolling, 'long drafts scroll inside the composer');
        const controlsBottom = await page.evaluate(() => Math.max(...[...document.querySelectorAll('.conv-pane > :is(.conv-scroll-end-btn,.conv-scroll-next-btn,.conv-scroll-last-btn,.conv-scroll-segment-btn)')].map(el => el.getBoundingClientRect().bottom)));
        assert.ok(controlsBottom <= keyboard.bar.top, 'jump controls stay above the growing composer');
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-keyboard-after.png'});
        await page.evaluate(() => {
          document.activeElement.blur();
          Object.assign(visualViewport, {height:900, offsetTop:0});
          visualViewport.dispatchEvent(new Event('resize'));
        });
        await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--app-vh') === '900px');
        await page.click('#mobileBackBtn');
        assert.equal(await page.evaluate(() => document.body.classList.contains('mobile-show-main')), false, 'Back returns to list');
        assert.equal(await page.$eval('.ccc-side-nav', el => getComputedStyle(el).display), 'none', 'home has no desktop rail');
        for (const key of ['coding', 'smallbugs', 'workers']) {
          assert.equal(await page.$eval('#mobileBottomNav > [data-mobile-nav="'+key+'"]', el => getComputedStyle(el).display !== 'none'), true, key + ' bottom button');
        }
        assert.equal(await page.$eval('[data-mobile-nav="coding"] .mobile-nav-label', el => el.textContent), 'Features');
        await page.waitForSelector('.mobile-home-status .hb-chip');
        await page.waitForSelector('.mobile-home-status .local-ci-mobile-toggle');
        await page.evaluate(() => {
          const grid = document.querySelector('.mobile-home-status .hb-grid');
          if (!grid.querySelector('.ccc-sav-ticker')) {
            const money = document.createElement('button'); money.className = 'ccc-sav-ticker'; money.textContent = '$12.34'; grid.append(money);
          }
        });
        const home = await page.evaluate(() => {
          const line = document.querySelector('.mobile-home-status');
          const rect = el => el.getBoundingClientRect().toJSON();
          return {line:rect(line), brand:rect(document.querySelector('.sidebar-header-brand')),
            gradient:getComputedStyle(document.querySelector('.sh-title-main')).backgroundImage,
            logo:rect(document.querySelector('.sh-app-icon')),
            entries:[...line.querySelectorAll('.local-ci-mobile-toggle,.hb-chip,.ccc-sav-ticker:not([hidden])')].map(rect),
            text:line.textContent};
        });
        assert.ok(home.brand.top < home.line.top, 'brand comes before status');
        assert.ok(home.logo.width >= 24 && home.logo.height >= 24, 'CCC logo preserved');
        assert.match(home.gradient, /linear-gradient/, 'styled brand preserved');
        assert.ok(home.line.height <= 46, 'CI, all engines and money occupy one row');
        assert.ok(home.entries.length >= 5, 'CI, three engines and money');
        for (const entry of home.entries) assert.ok(entry.top >= home.line.top && entry.bottom <= home.line.bottom, 'all status entries share the row');
        assert.match(home.text, /GH 98%.*Self 84%/);
        await page.click('.mobile-home-status .local-ci-mobile-toggle');
        assert.equal(await page.$eval('.mobile-home-status', el => el.getBoundingClientRect().height <= 46), true, 'CI details float without growing the status line');
        await page.keyboard.press('Escape');
        await page.evaluate(() => {
          document.activeElement.blur();
          const line = document.querySelector('.mobile-home-status'); line.scrollLeft = line.scrollWidth;
        });
        assert.ok(await page.$eval('.mobile-home-status .ccc-sav-ticker:not([hidden])', el => {const r=el.getBoundingClientRect();return r.width > 0 && el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}), 'money is reachable on the same horizontal line');
        await page.mouse.move(width - 2, 400);
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-home-usage-after.png'});
        await page.evaluate(() => { document.querySelector('.mobile-home-status').scrollLeft = 0; });
        // Long home lists collapse their chrome and restore it at the top.
        await page.evaluate(() => {
          const list = document.getElementById('convList');
          const rows = document.createElement('div'); rows.id = 'scrollFixture';
          rows.innerHTML = Array.from({length:30}, (_,i) => '<div class="conv-item" style="height:80px">Session '+i+'</div>').join('');
          list.append(rows); list.scrollTop = 180;
          list.dispatchEvent(new Event('scroll'));
        });
        await page.waitForFunction(() => document.body.classList.contains('mobile-home-collapsed'));
        assert.equal(await page.$eval('.mobile-home-status', el => getComputedStyle(el).display), 'none');
        assert.equal(await page.$eval('.new-session-panel', el => getComputedStyle(el).display), 'none');
        assert.equal(await page.$eval('.mobile-home-new', el => el.getBoundingClientRect().width), 44);
        assert.ok(await page.$eval('.sidebar-header-brand', el => el.getBoundingClientRect().height > 0 && getComputedStyle(el).opacity !== '0'));
        assert.ok(await page.$eval('.mobile-home-new', el => {const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}), 'compact New is visible and clickable');
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-home-collapsed.png'});
        await page.evaluate(() => {
          const list = document.getElementById('convList'); list.scrollTop = 0; list.dispatchEvent(new Event('scroll'));
          document.getElementById('scrollFixture').remove();
        });
        await page.waitForFunction(() => !document.body.classList.contains('mobile-home-collapsed'));
        await page.waitForSelector('[data-mobile-app="example"]');
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-home-status-after.png'});
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
        assert.ok(await page.$eval('#mobileSimpleHeader .msh-brand', el => el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility === 'visible'), 'Simple Home preserves branding');
        assert.equal(await page.$eval('#mobileSimpleHeader .msh-wordmark', el => el.textContent), 'Claude Command Center');
        assert.equal(await page.$eval('#mobileSimpleHeader .mobile-home-status', el => el.getBoundingClientRect().height <= 46), true, 'Simple Home shares the one-line status');
        if (width === 390) await page.screenshot({path:'/tmp/ccc-mobile-simple-home-after.png'});
      } else {
        assert.equal(geometry.railVisible, width > 1200, 'desktop retains app rail; tablet uses mobile navigation');
        assert.equal(geometry.passedVisible, false, 'reader has no CI strip');
      }
      console.log('PASS width ' + width);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
