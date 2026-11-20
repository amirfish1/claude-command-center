const respondConversationAsset = require('./conversation-assets.cjs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed=app.replace(marker, `
 window.compactTest={seed(){
 window.CCCNavigation.setTab('sessions');
 currentConversation='compact-test';
 currentSession={id:'compact-test',source:'antigravity',cwd:'/workspace/example',cwdExists:true};
 conversationsData=[{id:'compact-test',session_id:'compact-test',source:'antigravity',is_live:false}];
 updateInputBar();
 }, stage(){return _compactRun&&_compactRun.stage;}, boundary(){renderConversationEvents([{type:'system',subtype:'compact_boundary',line:100,compact:{trigger:'Antigravity'}}],activePaneId());}};
`+marker);
async function verifyCompact(entryPoint) {
 const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
 try{
 const page=await browser.newPage(); await page.setViewport({width:1440,height:1000});
 await page.evaluateOnNewDocument(()=>localStorage.setItem('ccc-ui-mode','advanced'));
 let sent;
 await page.setRequestInterception(true);
 page.on('request',async r=>{
 const url=new URL(r.url());
 if(r.isNavigationRequest())return r.respond({status:200,contentType:'text/html',body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
 if(respondConversationAsset(r))return;
 if(url.pathname==='/static/app.js')return r.respond({status:200,contentType:'application/javascript',body:exposed});
 // A peer-owned Antigravity session can be unknown to the local server.
 if(url.pathname==='/api/session/compact-test/slash-commands')return r.respond({status:200,contentType:'application/json',body:JSON.stringify({engine:'claude',commands:[{name:'/clear'}]})});
 if(url.pathname==='/api/spawn-defaults')return r.respond({status:200,contentType:'application/json',body:JSON.stringify({
   ok:true, engine:'antigravity', worker_engine:'claude', auto_compact_k:200, worker_auto_compact_k:250,
   models:{}, supported_engines:['antigravity', 'claude'],
 })});
 if(url.pathname==='/api/session/compact'){sent=JSON.parse(r.postData() || await r.fetchPostData());return r.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,compact:true,engine:'antigravity',via:'antigravity-stream'})});}
 if(url.pathname.startsWith('/static/')){const file=path.join(__dirname,'..',url.pathname);if(fs.existsSync(file))return r.respond({status:200,contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(file,'utf8')});}
 return r.respond({status:200,contentType:'application/json',body:'[]'});
 });
 await page.goto('http://ccc-compact.test/',{waitUntil:'load'});await page.waitForFunction(()=>!!window.compactTest);await page.waitForNetworkIdle({idleTime:200});
 await page.evaluate(()=>compactTest.seed());
 assert.equal(await page.$eval('#convCompactBtn',el=>el.checkVisibility()&&!el.disabled),true);
 if (entryPoint !== 'button') {
   await page.type('#convInput', '/');
   const command = '.slash-command-item';
   await page.waitForSelector(command, {visible:true});
   assert.deepEqual(await page.$$eval(command, items => items.map(item => item.querySelector('.slash-command-name').textContent)), ['/autocompact', '/compact']);
   if (process.env.CCC_SLASH_SCREENSHOT) await page.screenshot({path:process.env.CCC_SLASH_SCREENSHOT});
   if (entryPoint === 'auto-compact settings') {
     await page.keyboard.press('Enter');
     assert.equal(await page.$eval('#convInput', el => el.value), '/autocompact ');
     await page.keyboard.press('Enter');
     await page.waitForFunction(() => document.activeElement?.id === 'spawnDefaultsAutoCompact');
     assert.equal(await page.$eval('#spawnDefaultsAutoCompact', el => el.checkVisibility()), true);
     assert.equal(await page.$eval('#spawnDefaultsAutoCompact', el => el.value), '200');
     assert.equal(sent, undefined);
     return;
   }
   await page.keyboard.type('comp');
   await page.waitForFunction(() => document.querySelector('.slash-command-item.selected .slash-command-name')?.textContent === '/compact');
   await page.keyboard.press('Enter');
   assert.equal(await page.$eval('#convInput', el => el.value), '/compact ');
   assert.equal(sent, undefined);
   await page.keyboard.press('Enter');
 } else {
   await page.click('#convCompactBtn');
 }
 await page.waitForFunction(()=>compactTest.stage()==='working');
 assert.equal(sent.session_id,'compact-test');
 await page.evaluate(()=>compactTest.boundary());
 await page.waitForFunction(()=>compactTest.stage()==='done');
 if(process.env.CCC_COMPACT_SCREENSHOT)await page.screenshot({path:process.env.CCC_COMPACT_SCREENSHOT});
 }finally{await browser.close();}
}

test('Antigravity Compact button sends native compact and waits for checkpoint', () => verifyCompact('button'));
test('Antigravity slash menu sends native compact and waits for checkpoint', () => verifyCompact('slash menu'));
test('Antigravity /autocompact opens the saved threshold setting', () => verifyCompact('auto-compact settings'));
