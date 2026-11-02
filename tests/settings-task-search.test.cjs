'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),os=require('node:os');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-task-search-'));
let browser,server,url;
const fixture=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/settings-task-search.css"><style>body{display:block;margin:0;padding:12px;box-sizing:border-box}#settingsPane{max-width:900px;margin:auto}#settingsSearchInput{box-sizing:border-box;width:100%;height:44px}#editor{padding:12px;border:1px solid #888}button:focus-visible{outline:2px solid cyan}</style><input id="settingsSearchInput" aria-label="Search settings"><main id="settingsPane"></main><section id="editor" hidden><button id="nativeControl">Native editor control</button></section><script src="/static/settings-task-search.js"></script><script>
window.calls=[];window.requests=[];window.writes=[];window.fail=false;window.unavailable=false;
const set=Storage.prototype.setItem;Storage.prototype.setItem=function(...args){writes.push(args[0]);return set.apply(this,args)};
const origFetch=window.fetch;window.fetch=(address,options)=>{requests.push([address,options?.method||'GET']);return origFetch(address,options)};
window.cccNotify={policy:()=> 'custom'};
cccSettingsTaskSearch.mount({open:async task=>{calls.push(['open',task.id]);if(unavailable)return null;cccSettingsTaskSearch.search('');document.getElementById('editor').hidden=false;return document.getElementById('editor')},queue:(name,button)=>calls.push(['queue',name,button.dataset.settingsTaskQueue]),returnToSearch:q=>{document.getElementById('editor').hidden=true;document.getElementById('settingsSearchInput').value=q;cccSettingsTaskSearch.search(q)}});
</script>`;
test.before(async()=>{
 server=http.createServer((req,res)=>{
  const address=new URL(req.url,'http://fixture');
  if(address.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(fixture);return;}
  if(/^\/static\/[a-z-]+\.(js|css)$/.test(address.pathname)){res.setHeader('Content-Type',address.pathname.endsWith('.js')?'text/javascript':'text/css');res.end(fs.readFileSync(path.join(root,address.pathname)));return;}
  res.setHeader('Content-Type','application/json');
  if(address.pathname==='/api/settings/queue-engines')res.end(JSON.stringify({queues:[{queue:'EXAMPLE',engine:'codex',model:'example-model',effort:'medium',pins:{engine:'codex'}},{queue:'MANUAL',engine:'claude',model:'default',pins:{}}]}));
  else if(address.pathname==='/api/spawn-defaults'&&address.search==='?policy=1')res.end(JSON.stringify({ok:true,engine:'codex',models:{codex:'example-model'},reasoning_effort:'medium',worker_engine:'claude',worker_inherit_session:false}));
  else {res.statusCode=403;res.end('{}');}
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));url='http://127.0.0.1:'+server.address().port;
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));console.log('Screenshots:',scratch)});
async function open(t,width=1440){const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[];t.after(async()=>{await context.close();assert.deepEqual(errors,[])});page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width,height:900});await page.goto(url);return page;}
async function search(page,q){await page.$eval('#settingsSearchInput',(n,q)=>{n.value=q;n.dispatchEvent(new Event('input',{bubbles:true}))},q);}
test('curated task matching handles intent phrases, ambiguity and unknown input without an action',async t=>{
 const page=await open(t);const matches=await page.evaluate(()=>Object.fromEntries(['stop sounds','use Codex for this queue','make text bigger','login','unknown nonsense','<script>alert(1)</script>'].map(q=>[q,cccSettingsTaskSearch.match(q).map(t=>t.id)])));
 assert.equal(matches['stop sounds'][0],'sounds');assert(matches['use Codex for this queue'].includes('queue'));assert(matches['make text bigger'].includes('reader'));assert(matches.login.includes('engine'));assert.deepEqual(matches['unknown nonsense'],[]);assert.deepEqual(matches['<script>alert(1)</script>'],[]);
 assert.deepEqual(await page.evaluate(()=>({calls,writes,requests})),{calls:[],writes:[],requests:[]});
});
for(const width of [1440,390])test('sound query shows current state and keyboard navigation/back without mutation '+width,async t=>{
 const page=await open(t,width);await search(page,'stop sounds');assert.match(await page.$eval('[data-task-id=sounds]',n=>n.textContent),/sounds On/);
 await page.focus('[data-settings-task=sounds]');await page.keyboard.press('Enter');await page.waitForSelector('.settings-task-back');assert.equal(await page.evaluate(()=>document.activeElement.id),'nativeControl');
 await page.click('.settings-task-back');assert.equal(await page.evaluate(()=>document.activeElement.dataset.settingsTask),'sounds');assert.equal(await page.$eval('#settingsSearchInput',n=>n.value),'stop sounds');assert.deepEqual(await page.evaluate(()=>writes),[]);
 if(width===390)assert.equal(await page.$eval('[data-settings-task=sounds]',n=>n.getBoundingClientRect().height>=44),true);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:path.join(scratch,'sounds-'+width+'.png')});
});
test('queue search reads current queues and requires explicit exact selection without choosing the queried engine',async t=>{
 const page=await open(t,390);await search(page,'use Codex for this queue');await page.waitForSelector('[data-settings-task-queue=EXAMPLE]');
 assert.match(await page.$eval('[data-task-id=queue]',n=>n.textContent),/no engine is selected or staged/);await page.click('[data-settings-task-queue=MANUAL]');assert.deepEqual(await page.evaluate(()=>calls),[['queue','MANUAL','MANUAL']]);
 assert.equal(await page.evaluate(()=>requests.every(([,method])=>method==='GET')),true);assert.deepEqual(await page.evaluate(()=>writes),[]);await page.screenshot({path:path.join(scratch,'queue-390.png')});
});
test('host summary reads the nonmigrating endpoint and opens policy without staging',async t=>{
 const page=await open(t);await search(page,'worker defaults');await page.waitForFunction(()=>document.querySelector('[data-task-id=worker]').textContent.includes('Independent'));
 assert.match(await page.$eval('[data-task-id=defaults]',n=>n.textContent),/example-model/);await page.click('[data-settings-task=worker]');assert.deepEqual(await page.evaluate(()=>calls),[['open','worker']]);assert.deepEqual(await page.evaluate(()=>writes),[]);assert.equal(await page.evaluate(()=>requests.some(([address])=>address==='/api/spawn-defaults?policy=1')),true);
});
test('unavailable reader keeps search and offers a recoverable explanation',async t=>{
 const page=await open(t);await page.evaluate(()=>unavailable=true);await search(page,'text size');await page.click('[data-settings-task=reader]');
 await page.waitForFunction(()=>document.activeElement.dataset.settingsTask==='reader');assert.match(await page.$eval('#settingsTaskResults [role=status]',n=>n.textContent),/select a conversation/);assert.equal(await page.$eval('#settingsTaskResults',n=>n.hidden),false);assert.deepEqual(await page.evaluate(()=>writes),[]);
});
test('failed current-state reads show unavailable, and explicit refresh recovers',async t=>{
 const page=await open(t);await page.setRequestInterception(true);let fail=true;page.on('request',r=>r.url().includes('/api/')&&fail?r.respond({status:503,contentType:'application/json',body:'{}'}):r.continue());
 await search(page,'queue worker');await page.waitForFunction(()=>document.querySelector('[data-task-id=queue]').textContent.includes('unavailable')&&document.querySelector('[data-task-id=worker]').textContent.includes('unavailable'));
 fail=false;await page.click('[data-settings-task-refresh]');await page.waitForSelector('[data-settings-task-queue=EXAMPLE]');assert.deepEqual(await page.evaluate(()=>writes),[]);
});
test('clearing or replacing a query rejects late responses and removes results',async t=>{
 const page=await open(t);await page.setRequestInterception(true);const held=[];page.on('request',r=>r.url().includes('/api/')?held.push(r):r.continue());await search(page,'queue');await page.waitForFunction(()=>requests.length>0);await search(page,'stop sounds');for(const request of held)await request.respond({status:200,contentType:'application/json',body:JSON.stringify({queues:[{queue:'LATE',engine:'codex'}]})}).catch(()=>{});
 assert.equal(await page.$('[data-settings-task-queue=LATE]'),null);await search(page,'');assert.equal(await page.$eval('#settingsTaskResults',n=>n.hidden),true);assert.deepEqual(await page.evaluate(()=>writes),[]);
});


test('navigation skips hidden native focus targets',async t=>{
 const page=await open(t);await page.evaluate(()=>{const input=document.createElement('input');input.hidden=true;input.id='hiddenNative';document.getElementById('editor').prepend(input);});
 await search(page,'stop sounds');await page.click('[data-settings-task=sounds]');await page.waitForSelector('.settings-task-back');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'nativeControl');assert.deepEqual(await page.evaluate(()=>writes),[]);
});
