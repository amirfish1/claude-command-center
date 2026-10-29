const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

test('question controls show original scope and send typed answers without navigation', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    for (const width of [1440,390]) {
      const page = await browser.newPage();
      await page.setViewport({width,height:850});
      await page.setContent('<style>'+fs.readFileSync('static/question-tags.css','utf8')+'</style><div class="conv-item" data-session-id="current-continuation"><span class="'+(width===390?'wa-preview':'conv-outcome-step')+'">Answer [Q17:publish] before posting.</span></div>');
      await page.evaluate(() => {
        window.sent=[];window.drafts=[];window.navigations=0;window.fail=false;
        window.fetch=async()=>({json:async()=>({ok:true,questions:[{n:17,tag:'[Q17:publish]',slug:'publish',answered:false,mentions:[],first:{sid:'original-session',ts:'2026-01-01T00:00:00Z',uuid:'ask',text:'[Q17:publish] Approve the preview and publish from your account? This includes the full publication scope.'}}]})});
        window.cccAppendToComposer=async(sid,text)=>{drafts.push({sid,text});return true};
        window.cccOpenSession=()=>navigations++;
        window.cccSendQuestionAnswer=async(sid,text)=>{
          sent.push({sid,text});
          if(fail) return {ok:false,error:'Offline'};
          return new Promise(resolve=>window.releaseSend=()=>resolve({ok:true,queued:true}));
        };
      });
      await page.addScriptTag({path:'static/question-tags.js'});
      await page.waitForSelector('.qtag-chip');
      assert.equal(await page.$eval('.qtag-chip',b=>b.textContent),'Q17');
      await page.click('.qtag-chip');
      await page.waitForSelector('.qtag-sheet:not([hidden]) .qtag-text');
      assert.match(await page.$eval('.qtag-text',x=>x.textContent),/full publication scope/);
      await page.click('[data-qsend]');
      assert.equal(await page.evaluate(()=>sent.length),0,'empty answer never sends');
      await page.type('#qtagAns','No, hold publication.');
      await page.click('[data-qadd]');
      await page.waitForFunction(()=>drafts.length===1);
      assert.deepEqual(await page.evaluate(()=>drafts[0]),{sid:'current-continuation',text:'[Q17] No, hold publication.'});
      assert.equal(await page.evaluate(()=>sent.length),0,'drafting never sends');
      await page.type('#qtagAns','Yes, the preview only.');
      await page.click('[data-qsend]');
      await page.waitForFunction(()=>sent.length===1);
      await page.evaluate(()=>document.querySelector('[data-qsend]').click());
      assert.equal(await page.evaluate(()=>sent.length),1,'pending send cannot duplicate');
      assert.deepEqual(await page.evaluate(()=>sent[0]),{sid:'current-continuation',text:'[Q17] Yes, the preview only.'});
      await page.evaluate(()=>releaseSend());
      await page.waitForFunction(()=>document.querySelector('.qtag-added').textContent.includes('Queued'));
      assert.equal(await page.$eval('#qtagAns',x=>x.value),'');
      await page.evaluate(()=>window.fail=true);
      await page.type('#qtagAns','No, do not publish.');
      await page.click('[data-qsend]');
      await page.waitForFunction(()=>document.querySelector('.qtag-added').textContent.includes('Offline'));
      assert.equal(await page.$eval('#qtagAns',x=>x.value),'No, do not publish.');
      assert.equal(await page.$eval('[data-qsend]',x=>x.disabled),false);
      assert.equal(await page.evaluate(()=>navigations),0);
      await page.evaluate(() => {
        window.pendingLoads=[];
        window.fetch=()=>new Promise(resolve=>pendingLoads.push(resolve));
        window.cccSendQuestionAnswer=async(sid,text)=>{sent.push({sid,text});return {ok:true}};
        cccQuestionTags.open(17,'old-continuation');
        cccQuestionTags.open(18,'new-continuation');
        const question=n=>({n,tag:'[Q'+n+':publish]',slug:'publish',answered:false,mentions:[],first:{sid:'original',ts:'2026-01-01',uuid:'q'+n,text:'Question '+n+' full scope.'}});
        pendingLoads[1]({json:async()=>({ok:true,questions:[question(17),question(18)]})});
      });
      await page.waitForFunction(()=>document.querySelector('.qtag-text').textContent.includes('Question 18'));
      await page.evaluate(()=>pendingLoads[0]({json:async()=>({ok:true,questions:[]})}));
      await new Promise(resolve=>setTimeout(resolve,40));
      await page.type('#qtagAns','Hold the newer action.');
      await page.click('[data-qsend="18"]');
      await page.waitForFunction(()=>sent.length===3);
      assert.deepEqual(await page.evaluate(()=>sent[2]),{sid:'new-continuation',text:'[Q18] Hold the newer action.'});
      await page.close();
    }
  } finally { await browser.close(); }
});
