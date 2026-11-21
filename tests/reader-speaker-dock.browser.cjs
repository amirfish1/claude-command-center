const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const {functions} = require('./conversation-source.cjs');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const index = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
const toolbar = index.slice(index.indexOf('        <div class="conv-presentation-toolbar"'), index.indexOf('        <div class="conversations-view"'));
const header = index.slice(index.indexOf('        <div class="conv-pane-header"'), index.indexOf('        <div class="conv-presentation-toolbar"'));
const source = functions(['attachConversationEndAffordance', '_attachSpeakerTools', '_syncSpeakerTools', '_syncTtsRateUi', 'syncPresentationToolbar']);
let browser;
before(async () => { browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']}); });
after(async () => { await browser?.close(); });
for (const width of [1440, 390]) test(`speaker stays outside transcript at ${width}px with independent pane controls`, async () => {
  const page = await browser.newPage();
  try {
    await page.setViewport({width, height:850, deviceScaleFactor:2});
    await page.setRequestInterception(true);
    page.on('request', req => req.respond({status:200, contentType:'text/html', body:`<style>${css}
      body{margin:0;--surface:#161b22;--bg:#0d1117;--border:#43545a;--text:#c9d1d9}
      main{display:flex;height:800px}.conv-pane{flex:1;min-width:0;display:flex;flex-direction:column}
      .conversations-view{flex:1;min-height:0;overflow:auto}
      </style><main class="conv-split" id="convSplit" data-orientation="${width>700?'vertical':''}">${['p1','p2'].slice(0,width<700?1:2).map(id=>`<section class="conv-pane has-last-user-message" data-pane-id="${id}">${header}${toolbar}<div class="conversations-view"><p>Message text remains unobstructed.</p><button data-read-assistant-message>Read answer</button></div></section>`).join('')}</main>`}));
    await page.goto('http://fixture.test/');
    await page.addScriptTag({content:`
      const sharedState = {_TTS_RATE_MIN:0.5,_TTS_RATE_MAX:2,_ttsRate:1.25,activePaneId:()=> 'p1'};
      const presentationFeatureEnabled=()=>false;
      const _syncSpeakFloatPlayback=btn=>{btn.textContent='◖';};
      const _ttsReadSelectionIfAny=()=>false;
      const _visibleSpeakButton=view=>view.querySelector('[data-read-assistant-message]');
      const _observeConvEventSizes=()=>{};
      const updateConversationEndAffordance=()=>{};
      const _stepTtsRate=delta=>{sharedState._ttsRate+=delta;_syncTtsRateUi();};
      const _ttsJumpToNextSentence=()=>{window.nextSentence=true;};
      const _ttsCycleVoice=()=>({label:'Device voice'});
      ${source}
      document.querySelectorAll('[data-role="pane-title"]').forEach(el=>{el.textContent='Retrieve missing prompt from session';});
      localStorage.setItem('ccc-speak-btn-pos',JSON.stringify({x:0.5,y:0.5}));
      for(const view of document.querySelectorAll('.conversations-view')) {
        attachConversationEndAffordance(view);
        const btn=view._convSpeakButton;btn.classList.add('visible');btn._speakTarget=_visibleSpeakButton(view);
        syncPresentationToolbar(view.closest('.conv-pane'),'off',true);_syncSpeakerTools(btn);
        btn._speakTarget.onclick=()=>{window.readPane=view.closest('.conv-pane').dataset.paneId;};
      }
    `});
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const states = await page.evaluate(() => [...document.querySelectorAll('.conv-reader-audio')].map(dock => {
      const btn=dock.querySelector('button'), tools=btn._speakerTools, view=btn._convTargetView;
      const d=dock.getBoundingClientRect(), v=view.getBoundingClientRect(), b=btn.getBoundingClientRect(), t=tools.getBoundingClientRect();
      return {outside:d.bottom<=v.top,within:d.left>=0&&d.right<=innerWidth,centered:Math.abs(b.left+b.width/2-t.left-t.width/2)<1,
        hit:document.elementFromPoint(b.left+b.width/2,b.top+b.height/2)===btn,
        featureHidden:getComputedStyle(view.closest('.conv-pane').querySelector('.conv-presentation-segment')).display==='none',dragBound:!!btn._dragBound,
        inHeader:!!dock.closest('.conv-pane-header'), toolbarHidden:getComputedStyle(view.closest('.conv-pane').querySelector('.conv-presentation-toolbar')).display==='none'};
    }));
    for (const state of states) assert.deepEqual(state,{outside:true,within:true,centered:true,hit:true,featureHidden:true,dragBound:false,inHeader:width>1200,toolbarHidden:width>1200});
    if (width>1200) {
      // Shrink one desktop split pane without crossing the phone viewport breakpoint.
      const pane = '[data-pane-id="p1"]';
      const title = pane + ' [data-role="pane-title"]';
      await page.$eval(pane, el => {
        const titleEl = el.querySelector('[data-role="pane-title"]');
        titleEl.textContent = 'check-the-background-worker-status Investigate the current worker and report the result';
        titleEl.classList.add('is-expanded');
        el.querySelector('.conv-scroll-speak-btn').classList.add('speaking');
        const ticket = el.querySelector('[data-role="pane-ticket"]');
        ticket.textContent = 'OPS-1234';
        ticket.hidden = false;
        el.querySelector('[data-role="pane-category"]').innerHTML = '<span class="conv-pane-cat-icon">W</span>';
        el.querySelector('[data-role="pane-close"]').style.display = '';
        const sessionId = el.querySelector('[data-role="pane-session-id"]');
        sessionId.textContent = '(1234abcd)';
        sessionId.hidden = false;
        el.style.flex = '0 0 435px';
      });
      await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
      if (process.env.SPEAKER_DOCK_SCREENSHOTS) await page.$(pane).then(el => el.screenshot({path:process.env.SPEAKER_DOCK_SCREENSHOTS+'/narrow-split.png'}));
      const narrow = await page.$eval(title, el => {
        const dock = el.closest('.conv-pane').querySelector('.conv-reader-audio');
        const header = el.closest('.conv-pane-header');
        const view = el.closest('.conv-pane').querySelector('.conversations-view');
        const next = dock.querySelector('.speaker-next-sentence');
        const hit = next.getBoundingClientRect();
        return {
          dockUnderHeader: dock.getBoundingClientRect().top >= header.getBoundingClientRect().bottom,
          dockAboveTranscript: dock.getBoundingClientRect().bottom <= view.getBoundingClientRect().top,
          nextButtonClickable: !next.disabled && document.elementFromPoint(hit.x + hit.width/2, hit.y + hit.height/2) === next,
          titleWidth: el.clientWidth,
          titleOverflows: el.scrollWidth > el.clientWidth,
          titleLines: Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)),
        };
      });
      assert.ok(narrow.dockUnderHeader && narrow.dockAboveTranscript, 'the dock gets its own row above the transcript');
      assert.ok(narrow.nextButtonClickable, 'the dock controls remain reachable');
      assert.ok(narrow.titleWidth >= 160 && !narrow.titleOverflows && narrow.titleLines <= 5, JSON.stringify(narrow));
      await page.click(pane + ' .speaker-next-sentence');
      assert.equal(await page.evaluate(()=>window.nextSentence), true);
      await page.$eval(pane, el => { el.style.flex = ''; });
      await page.waitForSelector(pane + ' .conv-pane-header .conv-reader-audio');
    }
    await page.click('.conv-reader-audio .conv-scroll-speak-btn');
    assert.equal(await page.evaluate(()=>window.readPane),'p1');
    await page.$eval('.speaker-rate-dial',el=>el.focus()); await page.keyboard.press('ArrowRight');
    assert.equal(await page.$eval('.speaker-rate-value',el=>el.textContent),'1.30×');
    await page.click('.speaker-voice-switch');
    assert.equal(await page.$eval('.speaker-voice-name',el=>el.textContent),'Device voice');
    if (width>700) {await page.click('[data-pane-id="p2"] .conv-scroll-speak-btn');assert.equal(await page.evaluate(()=>window.readPane),'p2');}
    await page.evaluate(() => {
      for (const pane of document.querySelectorAll('.conv-pane')) syncPresentationToolbar(pane, 'off', false);
    });
    assert.equal(await page.$$eval('.conv-reader-audio', docks => docks.every(dock => getComputedStyle(dock).display === 'none')), true);
    await page.evaluate(() => {
      for (const pane of document.querySelectorAll('.conv-pane')) syncPresentationToolbar(pane, 'off', true);
    });
    if (process.env.SPEAKER_DOCK_SCREENSHOTS) await page.screenshot({path:process.env.SPEAKER_DOCK_SCREENSHOTS+'/'+width+'-fixture.png'});
  } finally {await page.close();}
});
