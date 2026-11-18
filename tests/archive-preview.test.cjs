const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const html = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
const source = html.match(/<script id="cccArchiveTitlePreview">([\s\S]*?)<\/script>/)[1];
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

test('early titles remain safe, bounded, readable on phone and subordinate to the live list', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', r => r.respond({status:200,contentType:'text/html',body:
      '<style>'+css+'</style><div id="cccLoadingOverlay"></div><div id="convList">Loading</div>'}));
    const start = async (options={}) => {
      await page.goto('http://localhost/'+(options.search || ''));
      await page.evaluate(options => {
        localStorage.clear();
        const rows=Array.from({length:20}, (_,i) => ({id:'session-'+i,session_id:'session-'+i,
          source:'codex',session_cwd:'/example/project',display_name:'<img src=x onerror=alert(1)> Saved title '+i}));
        if(options.cache !== false) localStorage.setItem('ccc-sidebar-snapshot-v1:7d',JSON.stringify({
          version:1,window:options.window || '7d',savedAt:Date.now()-(options.age || 0),rows}));
        if(options.disabled) localStorage.setItem('ccc-conv-cache','0');
        if(options.engine) localStorage.setItem('ccc-archive-engine-filter',options.engine);
        if(options.view) localStorage.setItem('ccc-session-view',options.view);
        window.__cccArchiveBootstrap={promise:new Promise(resolve => {window.resolveBoot=resolve;})};
      }, options);
      await page.addScriptTag({content:source});
    };
    await start();
    assert.equal(await page.$$eval('.conv-item',es=>es.length),8);
    assert.equal(await page.$eval('#cccLoadingOverlay',e=>e.classList.contains('gone')),true);
    assert.equal(await page.$eval('[data-boot-preview]',e=>e.querySelectorAll('img,button').length),0);
    const href = await page.$eval('a.conv-item',e=>e.href);
    const url = new URL(href);
    assert.equal(url.origin,'http://localhost');
    assert.equal(url.searchParams.get('conv'),'session-0');
    assert.equal(url.searchParams.get('repo_path'),'/example/project');
    for(const width of [1440,375]) {
      await page.setViewport({width,height:800});
      const visible=await page.$eval('a.conv-item > .wa-row',e=>e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})&&e.getBoundingClientRect().height>0);
      assert.equal(visible,true,'saved title must remain readable at '+width);
    }
    await page.evaluate(() => {
      document.getElementById('convList').innerHTML='<div class="conv-item" data-id="authoritative">Current title</div>';
      resolveBoot(new Response(JSON.stringify({conversations:[{id:'late',display_name:'Old reply'}]})));
    });
    await page.waitForFunction(()=>document.querySelector('[data-id="authoritative"]'));
    assert.equal(await page.$('[data-id="late"]'),null);
    for(const opts of [{age:86400001},{window:'1d'},{disabled:true},{engine:'codex'},
      {view:'board'},{view:'flow'},{search:'?ccc_popout=conversation'},{search:'?ccc_app=queues'}]) {
      await start(opts);
      assert.equal(await page.$('[data-boot-preview]'),null,JSON.stringify(opts));
    }
    await start({cache:false});
    await page.evaluate(()=>{
      window.response=new Response(JSON.stringify({conversations:[{id:'current',display_name:'Current recent title'},
        {id:'deleted',trashed:true},{id:'archived',archived:true}]}));
      resolveBoot(response);
    });
    await page.waitForSelector('[data-id="current"]');
    assert.equal(await page.$$eval('.conv-item',es=>es.length),1);
    assert.equal(await page.evaluate(()=>response.bodyUsed && !!response._cccArchiveParsedBody),true);
    assert.equal(await page.evaluate(()=>window.__cccArchiveProgress),undefined,'preview never claims readiness');
    assert.match(await page.$eval('[data-boot-preview]',e=>e.textContent),/loading full archive/);
    await start({cache:false});
    await page.evaluate(()=>localStorage.setItem('ccc-sidebar-snapshot-v1:7d','{broken'));
    await page.addScriptTag({content:source});
    assert.equal(await page.$('[data-boot-preview]'),null);
    await page.evaluate(()=>Object.defineProperty(window,'localStorage',{get(){throw Error('disabled');},configurable:true}));
    await page.addScriptTag({content:source});
    assert.equal(await page.$('[data-boot-preview]'),null);
  } finally { await browser.close(); }
});
