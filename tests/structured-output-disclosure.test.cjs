const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');
const source = require('./conversation-content-fixture.cjs').source;

test('structured response stays readable in compact, normal and verbose transcript views', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    for (const mode of ['compact', 'normal', 'verbose']) {
      await page.setContent(`<style>${css}</style><body class="${mode === 'compact' ? 'conv-compact' : ''}"><div class="conv-pane"><div class="conversations-view"><div class="tool-call" id="structured"></div><div class="tool-call" id="ordinary"></div></div></div></body>`);
      await page.evaluate(({source,mode}) => {
        window.requests=[];
        window.verbose = mode === 'verbose';
        Object.defineProperty(window, 'localStorage', {configurable:true,
          value:{getItem:key=>key==='ccc-conv-view'?mode:null}});
        window.convVerboseOn = () => verbose;
        window.toolDisplayName = value => value;
        window.escapeAttr = value => String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        window.fetch = async (url, options) => {
          requests.push({url,options});
          return {ok:true,json:async () => ({ok:true,input:JSON.stringify({verdict:'pass',summary:'Checked all claims <img src=x onerror="window.injected=true">',claims:[{status:'verified'}]},null,2)})};
        };
        (0,eval)(source);
        document.querySelector('#structured').innerHTML = renderToolInputDisclosure({name:'StructuredOutput',has_input:true,id:'output-1'},'session-example',28);
        document.querySelector('#ordinary').innerHTML = renderToolInputDisclosure({name:'Read',has_input:true,id:'read-1'},'session-example',29);
        const details=document.querySelector('#structured details');
        details.addEventListener('toggle', () => { if(details.open) loadToolInputDisclosure(details); });
        if(details.open) loadToolInputDisclosure(details);
      },{source,mode});
      const summary = '#structured details summary';
      assert.equal(await page.$eval(summary, el => el.checkVisibility()),true);
      assert.match(await page.$eval(summary, el => el.textContent), /View structured output/);
      if(mode === 'compact') assert.equal(await page.$eval('#ordinary details', el => el.checkVisibility()),false);
      if(mode !== 'verbose') {
        assert.equal(await page.evaluate(() => requests.length),0,'payload stays lazy');
        await page.click(summary);
      }
      await page.waitForFunction(() => document.querySelector('#structured details').dataset.loaded === '1');
      assert.match(await page.$eval('#structured pre',el=>el.textContent), /"verdict": "pass"/);
      assert.match(await page.$eval('#structured pre',el=>el.textContent), /"claims"/);
      assert.equal(await page.$eval('#structured pre',el=>el.checkVisibility()),true);
      assert.equal(await page.$('#structured pre img'),null,'JSON remains inert text');
      const requests = await page.evaluate(() => window.requests);
      assert.equal(requests.length,1);
      assert.match(requests[0].url,/tool-input\?line=28&tool_use_id=output-1/);
      assert.equal(requests[0].options.cccPriority,'user');
      await page.click(summary);
      await page.click(summary);
      assert.equal(await page.evaluate(() => window.requests.length),1,'reopening uses the loaded payload');
      if(process.env.CCC_STRUCTURED_SCREENSHOT && mode === 'compact') await page.screenshot({path:process.env.CCC_STRUCTURED_SCREENSHOT});
    }
  } finally { await browser.close(); }
});
