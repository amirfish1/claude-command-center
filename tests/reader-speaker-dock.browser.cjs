const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const {functions} = require('./conversation-source.cjs');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const index = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
const toolbar = index.slice(index.indexOf('        <div class="conv-presentation-toolbar"'), index.indexOf('        <div class="conversations-view"'));
const source = functions(['attachConversationEndAffordance', '_attachSpeakerTools', '_syncSpeakerTools', '_syncTtsRateUi', 'syncPresentationToolbar']);
let browser;
before(async () => { browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']}); });
after(async () => { await browser?.close(); });
for (const width of [1440, 390]) test(`speaker stays outside transcript at ${width}px with independent pane controls`, async () => {
  const page = await browser.newPage();
  try {
    await page.setViewport({width, height:850});
    await page.setRequestInterception(true);
    page.on('request', req => req.respond({status:200, contentType:'text/html', body:`<style>${css}
      body{margin:0;--surface:#161b22;--bg:#0d1117;--border:#43545a;--text:#c9d1d9}
      main{display:flex;height:800px}.conv-pane{flex:1;min-width:0;display:flex;flex-direction:column}
      .conversations-view{flex:1;min-height:0;overflow:auto}
      </style><main>${['p1','p2'].slice(0,width<700?1:2).map(id=>`<section class="conv-pane" data-pane-id="${id}">${toolbar}<div class="conversations-view"><p>Message text remains unobstructed.</p><button data-read-assistant-message>Read answer</button></div></section>`).join('')}</main>`}));
    await page.goto('http://fixture.test/');
    await page.addScriptTag({content:`
      const sharedState = {_TTS_RATE_MIN:0.5,_TTS_RATE_MAX:2,_ttsRate:1.25,activePaneId:()=> 'p1'};
      const presentationFeatureEnabled=()=>false;
      const _syncSpeakFloatPlayback=btn=>{btn.textContent='Read';};
      const _ttsReadSelectionIfAny=()=>false;
      const _visibleSpeakButton=view=>view.querySelector('[data-read-assistant-message]');
      const _observeConvEventSizes=()=>{};
      const updateConversationEndAffordance=()=>{};
      const _stepTtsRate=delta=>{sharedState._ttsRate+=delta;_syncTtsRateUi();};
      const _ttsJumpToNextSentence=()=>{window.nextSentence=true;};
      const _ttsCycleVoice=()=>({label:'Device voice'});
      ${source}
      localStorage.setItem('ccc-speak-btn-pos',JSON.stringify({x:0.5,y:0.5}));
      for(const view of document.querySelectorAll('.conversations-view')) {
        attachConversationEndAffordance(view);
        const btn=view._convSpeakButton;btn.classList.add('visible');btn._speakTarget=_visibleSpeakButton(view);
        syncPresentationToolbar(view.closest('.conv-pane'),'off',true);_syncSpeakerTools(btn);
        btn._speakTarget.onclick=()=>{window.readPane=view.closest('.conv-pane').dataset.paneId;};
      }
    `});
    const states = await page.evaluate(() => [...document.querySelectorAll('.conv-reader-audio')].map(dock => {
      const btn=dock.querySelector('button'), tools=btn._speakerTools, view=btn._convTargetView;
      const d=dock.getBoundingClientRect(), v=view.getBoundingClientRect(), b=btn.getBoundingClientRect(), t=tools.getBoundingClientRect();
      return {outside:d.bottom<=v.top,within:d.left>=0&&d.right<=innerWidth,centered:Math.abs(b.left+b.width/2-t.left-t.width/2)<1,
        hit:document.elementFromPoint(b.left+b.width/2,b.top+b.height/2)===btn,
        featureHidden:getComputedStyle(dock.parentElement.querySelector('.conv-presentation-segment')).display==='none',dragBound:!!btn._dragBound};
    }));
    for (const state of states) assert.deepEqual(state,{outside:true,within:true,centered:true,hit:true,featureHidden:true,dragBound:false});
    await page.click('.conv-reader-audio .conv-scroll-speak-btn');
    assert.equal(await page.evaluate(()=>window.readPane),'p1');
    await page.$eval('.speaker-rate-dial',el=>el.focus()); await page.keyboard.press('ArrowRight');
    assert.equal(await page.$eval('.speaker-rate-value',el=>el.textContent),'1.30×');
    await page.click('.speaker-voice-switch');
    assert.equal(await page.$eval('.speaker-voice-name',el=>el.textContent),'Device voice');
    if (width>700) {await page.click('[data-pane-id="p2"] .conv-scroll-speak-btn');assert.equal(await page.evaluate(()=>window.readPane),'p2');}
    if (process.env.SPEAKER_DOCK_SCREENSHOTS) await page.screenshot({path:process.env.SPEAKER_DOCK_SCREENSHOTS+'/'+width+'-fixture.png'});
  } finally {await page.close();}
});
