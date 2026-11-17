// Canonical child pages share their first response while keeping frame semantics.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const root = path.resolve(__dirname, '..');
const pages = JSON.parse(execFileSync('python3', ['-c', `
import json,tempfile
from pathlib import Path
import server
from ccc_server import app_routines
with tempfile.TemporaryDirectory() as tmp:
    server.USER_APPS_DIR=Path(tmp)
    app=Path(tmp)/'document-test';app.mkdir()
    (app/'app.json').write_text('{}')
    (app/app_routines.STATE_NAME).write_text(json.dumps({'agent':{'session_id':'session-test'}}))
    (app/'index.html').write_text('<!doctype html><html><head></head><body><main data-ccc-content><h1>Canonical app</h1><textarea id="appDraft"></textarea></main><script src="/static/app-kit.js" data-app="document-test" defer></script></body></html>')
    current={'events':[{'type':'user','text':'A real fixture request','line':1},{'type':'assistant','text':'A rendered fixture response','line':2}],'last_line':2}
    server._conversation_open_response=lambda *_args,**_kwargs: {'body':json.dumps(current).encode(),'encoding':None,'cache':'miss'}
    selected={'id':'document-test','nav':'/view/document-test','label':'Test app'}
    pages={'shell':server._load_app_shell_html(selected),'app':server._load_application_html('document-test'),'conversation':server._load_index_html(app_embed=True)}
    server._conversation_open_response=lambda *_args,**_kwargs: None
    pages['failed_shell']=server._load_app_shell_html(selected)
    print(json.dumps(pages))
`], {cwd:root,maxBuffer:8*1024*1024,encoding:'utf8'}));
const bundles = JSON.parse(execFileSync('python3', ['-c',
  'from pathlib import Path; from ccc_server.conversation_assets import BUNDLES, ConversationScript; import json; print(json.dumps({name:ConversationScript(Path("static"),name).read_bytes().decode() for name in BUNDLES}))'], {cwd:root,maxBuffer:8*1024*1024}));

test('first response boots both canonical documents with URLs, controls and reload drafts intact', async () => {
  const requests=[],errors=[];
  let failRead=false, streamEnabled=false;
  const server=http.createServer((request,response)=>{
    const url=new URL(request.url,'http://localhost');requests.push(url.pathname+url.search);
    response.setHeader('Content-Type','application/json');
    if(url.pathname==='/') {
      response.setHeader('Content-Type','text/html');response.end(url.searchParams.has('ccc_app')?(failRead?pages.failed_shell:pages.shell):pages.conversation);
    } else if(url.pathname==='/view/document-test') {
      response.setHeader('Content-Type','text/html');response.end(pages.app);
    } else if(bundles[url.pathname.slice('/static/'.length)]) {
      response.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':'application/javascript');
      response.end(bundles[url.pathname.slice('/static/'.length)]);
    } else if(url.pathname.startsWith('/static/')) {
      const file=path.join(root,url.pathname);
      if(!file.startsWith(root+'/static/')||!fs.existsSync(file)){response.writeHead(404).end();return;}
      response.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':'text/css');response.end(fs.readFileSync(file));
    } else if(streamEnabled&&url.pathname==='/api/conversations/session-test/stream') {
      response.setHeader('Content-Type','text/event-stream');
      response.end('data: '+JSON.stringify({events:[{type:'assistant',text:'Live event after initial read',line:3}],last_line:3})+'\n\n');
    } else if(failRead&&url.pathname==='/api/conversations/session-test') {
      response.writeHead(500).end(JSON.stringify({error:'Fixture read failed'}));
    } else response.end(JSON.stringify(url.pathname==='/api/conversations/session-test'
      ? {events:[{type:'user',text:'A real fixture request',line:1},{type:'assistant',text:'A rendered fixture response',line:2}],last_line:2}
      : url.pathname==='/api/apps/routines'?{ok:true,agent:{session_id:'session-test'},routines:[]}
      : url.pathname==='/api/apps'?{apps:[{id:'document-test',nav:'/view/document-test',label:'Test app'}]}:{}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
    const page=await browser.newPage();await page.setViewport({width:1600,height:1000});
    page.on('pageerror',error=>errors.push(error.message));
    const base='http://127.0.0.1:'+server.address().port;
    await page.setRequestInterception(true);
    page.on('request',request=>new URL(request.url()).origin===base?request.continue():request.respond({status:200,body:'{}'}));
    await page.goto(base+'/?ccc_app=document-test',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.getElementById('cccMobileAppFrame')?.contentWindow?.cccAppReady,{timeout:20000});
    const app=page.frames().find(frame=>frame.url().includes('/view/document-test'));
    const agent=page.frames().find(frame=>frame.url().includes('embed=app'));
    assert.ok(!requests.some(url=>/\/static\/app-(?:shell|embed)-boot\.js/.test(url)),
      'both canonical startup bridges arrive with their owning document');
    assert.ok(app&&agent,'both canonical frame URLs remain observable');
    assert.equal(page.url(),base+'/?ccc_app=document-test');
    assert.equal(requests.filter(url=>url.startsWith('/view/')||url.includes('embed=app')).length,0,'no sequential child HTML requests');
    assert.equal(requests.filter(url=>url.startsWith('/api/conversations/session-test?tail=')).length,0,'initial canonical tail arrives with the page');
    assert.equal(await agent.$$eval('#conversationsView .event',elements=>elements.length),2);
    assert.equal(await agent.$eval('#convInput',input=>input.disabled),false);
    assert.equal(await agent.evaluate(()=>window.cccConversation.paneByPaneId('p1').lastLine),2,'live stream starts after the supplied read cursor');
    streamEnabled=true;
    await agent.evaluate(()=>window.cccConversation.startConvStream('p1'));
    await agent.waitForFunction(()=>document.getElementById('conversationsView').textContent.includes('Live event after initial read'),{timeout:5000});
    assert.ok(requests.some(url=>url==='/api/conversations/session-test/stream?after=2'));
    assert.equal(await agent.evaluate(()=>window.cccConversation.paneByPaneId('p1').lastLine),3);
    streamEnabled=false;
    await agent.evaluate(()=>window.cccConversation.stopConvStream('p1'));
    assert.equal(await agent.evaluate(()=>location.origin),base);
    await agent.click('#convActionsBtn');
    assert.equal(await agent.$eval('#convActionsBtn',button=>button.getAttribute('aria-expanded')),'true');
    await agent.click('#convActionsBtn');
    await agent.type('#convInput','A draft across document delivery');
    await agent.evaluate(()=>{const c=window.cccConversation;c.rememberComposerDraftForPane(c.activePaneId());});
    await app.type('#appDraft','App draft');
    assert.equal(await app.evaluate(()=>window.cccAppReady.shell_lead_ms>0),true,'the whole host wait is retained');
    if(process.env.CCC_APP_DOCUMENT_SCREENSHOT)await page.screenshot({path:process.env.CCC_APP_DOCUMENT_SCREENSHOT});
    await Promise.all([agent.waitForNavigation({waitUntil:'domcontentloaded'}),agent.evaluate(()=>location.reload())]);
    await agent.waitForFunction(()=>window.cccConversation&&document.querySelectorAll('#conversationsView .event').length===2);
    assert.equal(await agent.$eval('#convInput',input=>input.value),'A draft across document delivery');
    assert.equal(await app.$eval('#appDraft',input=>input.value),'App draft','reloading agent preserves app browsing context');
    assert.ok(requests.some(url=>url.includes('embed=app')),'reload uses the original canonical route');
    assert.equal(page.url(),base+'/?ccc_app=document-test');
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.getElementById('cccMobileAppFrame')?.contentWindow?.cccAppReady,{timeout:20000});
    const again=page.frames().find(frame=>frame.url().includes('embed=app'));
    assert.equal(await again.$eval('#convInput',input=>input.value),'A draft across document delivery');
    // No saved transcript in this context: a failed read must never release
    // the app-ready promise just because the document itself was supplied.
    failRead=true;
    const context=await browser.createBrowserContext();
    const failed=await context.newPage();
    await failed.setRequestInterception(true);
    failed.on('request',request=>new URL(request.url()).origin===base?request.continue():request.respond({status:200,body:'{}'}));
    await failed.goto(base+'/?ccc_app=document-test',{waitUntil:'domcontentloaded'});
    await failed.waitForFunction(()=>{
      const app=document.getElementById('cccMobileAppFrame')?.contentWindow;
      return app?.document.querySelector('iframe')?.contentDocument?.getElementById('conversationsView')?.textContent.includes('Failed to load conversation');
    },{timeout:20000});
    await new Promise(resolve=>setTimeout(resolve,350));
    assert.equal(await failed.evaluate(()=>!!document.getElementById('cccMobileAppFrame').contentWindow.cccAppReady),false);
    await context.close();
    assert.deepEqual(errors,[]);
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
});
