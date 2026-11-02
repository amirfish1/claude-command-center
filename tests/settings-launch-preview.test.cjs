'use strict';
// Real launch consumers + real read-only endpoint in a disposable process.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn,spawnSync} = require('node:child_process');
const nativeAvailable=spawnSync('python3',['-c','from watchtower import config,workers,models'],{stdio:'ignore'}).status===0;
const {once} = require('node:events');
const readline = require('node:readline');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'launch-preview-'));
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const queueRefresh=between('  async function refreshSettingsQueues()',"  document.getElementById('settingsUsageRefresh')");
const settingsJSON=between('  async function settingsJSON(', '  async function refreshSettingsUsage()');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><style>
body{display:block;overflow:auto}.settings-pane{box-sizing:border-box;max-width:1000px;margin:auto}.fixture-nav{display:flex;gap:12px;padding:16px}.settings-section{display:block!important}.settings-section[hidden]{display:none!important}
</style></head><body><nav class="fixture-nav"><button id="defaultsTab">Launch defaults</button><button id="queuesTab">Queue overrides</button></nav><main class="settings-pane"><section id="defaults"></section><section id="queues" hidden></section></main><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');
for(const kind of ['defaults','queues']) document.getElementById(kind).append(parsed.querySelector('[data-launch-preview="'+kind+'"]'));
for(const id of ['settingsQueueTable','settingsQueuePreviewBody'])document.getElementById('queues').append(parsed.getElementById(id));
for(const kind of ['defaults','queues'])document.getElementById(kind+'Tab').onclick=()=>{document.getElementById('defaults').hidden=kind!=='defaults';document.getElementById('queues').hidden=kind!=='queues';};
const escapeHtml=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');let settingsQueueBusy=false,settingsQueueSnapshot=null;
${settingsJSON}
${queueRefresh}
</script><script src="/static/settings-launch-preview.js"></script><script>window.cccLaunchPreview.mount();</script></body></html>`;
let child,browser,url,original;
const stateFile=path.join(scratch,'spawn-defaults.json'),queueFile=path.join(scratch,'queues.json');
test.before(async()=>{
 if(!nativeAvailable)return;
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);
 const python=`import json,sys
from pathlib import Path
import server
from watchtower import config as wt, workers, models
base=Path(sys.argv[1]);state=base/'spawn-defaults.json';queue=base/'queues.json';policy=base/'policy.json'
server.COMMAND_CENTER_STATE_DIR=base;server.SPAWN_DEFAULTS_FILE=state;server.MODEL_POLICY_FILE=policy
server._WT_CONFIG_AVAILABLE=True;server._wt_config=wt;server._wt_config_path=lambda: queue
wt.CONFIG_FILE=queue;wt.CCC_SPAWN_DEFAULTS_FILE=state;wt.CCC_MODEL_POLICY_FILE=policy
models.entries=lambda engine:[{'id':m,'label':m,'efforts':e} for m,e in {'claude':[('claude-sonnet-4-6',('low','high'))],'codex':[('gpt-5-codex',('low','medium','high')),('gpt-5-blocked',('high',))]}.get(engine,[])]
workers.engine_available=lambda engine:engine=='codex'
policy.write_text(json.dumps({'blocked_models':['gpt-5-blocked']}))
raw=server._factory_spawn_defaults();raw.update(engine='codex',reasoning_effort='high',worker_engine='codex',worker_model='gpt-5-codex',worker_reasoning_effort='high',worker_fallback={'enabled':True,'models':[{'engine':'claude','model':'claude-sonnet-4-6','effort':'high'},{'engine':'codex','model':'gpt-5-codex','effort':'medium'}]})
raw['models'].update(claude='sonnet-4-6',codex='gpt-5-codex');state.write_text(json.dumps(raw))
queue.write_text(json.dumps({'ALPHA':{'auto_drain':True,'engine':'claude','model':'sonnet-4-6','effort':'medium','headroom_dispatch':True},'BETA':{'auto_drain':True,'engine':'codex','model':'gpt-5-blocked','fallback_to_default_worker':False},'MANUAL':{},'OLD':{'archived':True}}))
class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/api/settings/queue-engines':return super().do_GET()
        if self.path=='/':body=(base/'fixture.html').read_bytes();mime='text/html; charset=utf-8'
        elif self.path in ('/static/app.css','/static/settings-launch-preview.js'):
            body=(Path.cwd()/self.path.lstrip('/')).read_bytes();mime='text/css' if self.path.endswith('.css') else 'application/javascript'
        else:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):return self.send_error(405)
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True);httpd.serve_forever()
`;
 child=spawn('python3',['-u','-c',python,scratch],{cwd:root,env:{...process.env,CCC_EPHEMERAL:'1',WATCHTOWER_BLOCKED_MODELS:'',CCC_BLOCKED_MODELS:''}});
 let stderr='';child.stderr.on('data',d=>{stderr+=d;});const lines=readline.createInterface({input:child.stdout});
 const data=await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw new Error(stderr);})]);url=data.url;
 original={defaults:fs.readFileSync(stateFile,'utf8'),queues:fs.readFileSync(queueFile,'utf8')};
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Launch preview screenshots:',scratch);});
async function open(t,width=1440){
 fs.writeFileSync(stateFile,original.defaults);fs.writeFileSync(queueFile,original.queues);
 const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[],requests=[];page.setDefaultTimeout(7000);
 await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',req=>{if(req.url().includes('/api/'))requests.push({method:req.method(),url:req.url()});return req.url().startsWith(url)?req.continue():req.abort();});
 t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);assert(requests.every(r=>r.method==='GET'&&r.url===url+'/api/settings/queue-engines'));});
 await page.goto(url,{waitUntil:'load'});await page.waitForFunction(()=>document.querySelector('#defaults .settings-launch-basis')?.textContent.includes('Resolved by launch consumers'));
 return page;
}
async function choose(page,context,kind='defaults'){await page.select('#settingsLaunchContext-'+kind,context);}
for(const width of [1440,390]){
 test(`all launch contexts expose values, provenance, quota uncertainty and queue inspection at ${width}px`,{skip:!nativeAvailable},async t=>{
  const page=await open(t,width);
  assert.equal(await page.$eval('#settingsLaunchContext-defaults',el=>el.options.length),5);
  assert.match(await page.$eval('#defaults .settings-launch-output',el=>el.textContent),/gpt-5-codex/);
  assert.match(await page.$eval('#defaults .settings-launch-output',el=>el.textContent),/do not route ordinary new sessions/);
  await choose(page,'worker');assert.equal(await page.$eval('#defaults [data-launch-field=engine] strong',el=>el.textContent),'codex');
  assert.match(await page.$eval('#defaults [data-launch-field=model] .settings-source-badge',el=>el.textContent),/Stored worker default/);
  await choose(page,'queue:ALPHA');
  assert.match(await page.$eval('#defaults [data-launch-field=engine] .settings-source-badge',el=>el.textContent),/Queue pin/);
  assert.equal(await page.$eval('#defaults [data-launch-field=model] strong',el=>el.textContent),'claude-sonnet-4-6');
  assert.match(await page.$eval('#defaults [data-launch-field=effort] .settings-source-badge',el=>el.textContent),/Launch safeguard/);
  assert.match(await page.$eval('#defaults .settings-launch-output',el=>el.textContent),/Headroom dispatch is on/);
  assert.match(await page.$eval('#defaults .settings-launch-output',el=>el.textContent),/decision deferred until dispatch/);
  assert.equal(await page.$$eval('#defaults ol li',els=>els.length),2);
  assert.equal(await page.$eval('#defaults .settings-launch-details',el=>el.open),false);
  await page.focus('#defaults .settings-launch-details > summary');await page.keyboard.press('Enter');
  assert.equal(await page.$eval('#defaults .settings-launch-details',el=>el.open),true);
  assert.equal(await page.$eval('#defaults ol',el=>!!el.getClientRects().length),true);
  await page.keyboard.press('Enter');
  await page.screenshot({path:path.join(scratch,`queue-context-${width}.png`),fullPage:true});
  await choose(page,'queue:BETA');assert.equal(await page.$eval('#defaults [data-launch-field=model] strong',el=>el.textContent),'gpt-5-codex');
  assert.match(await page.$eval('#defaults [data-launch-field=model] .settings-source-badge',el=>el.textContent),/Model policy substitution/);
  assert.match(await page.$eval('#defaults h4',el=>el.textContent),/Off.*Queue override/);
  await choose(page,'queue:MANUAL');assert.match(await page.$eval('#defaults .settings-launch-output',el=>el.textContent),/manual launch only/);
  await page.click('#queuesTab');await page.evaluate(()=>refreshSettingsQueues());
  await page.waitForSelector('[data-launch-queue="ALPHA"]');await page.click('[data-launch-queue="ALPHA"]');
  await page.waitForFunction(()=>document.activeElement.id==='settingsLaunchContext-queues' && document.querySelector('#queues .settings-launch-basis'));
  assert.equal(await page.$eval('#settingsLaunchContext-queues',el=>el.value),'queue:ALPHA');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsLaunchContext-queues',JSON.stringify(await page.$eval('#settingsLaunchContext-queues',el=>({disabled:el.disabled,rect:el.getBoundingClientRect().toJSON(),html:el.outerHTML,active:document.activeElement.outerHTML.slice(0,500)}))));
  await page.$eval('[data-launch-queue="BETA"]',el=>el.focus());await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#settingsLaunchContext-queues').value==='queue:BETA' && document.activeElement.id==='settingsLaunchContext-queues');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsLaunchContext-queues');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.equal(fs.readFileSync(stateFile,'utf8'),original.defaults);assert.equal(fs.readFileSync(queueFile,'utf8'),original.queues);
 });
}
test('refresh and saved-default events replace stale values; removed context is not silently reassigned',{skip:!nativeAvailable},async t=>{
 const page=await open(t);await choose(page,'queue:ALPHA');
 const raw=JSON.parse(original.defaults);raw.worker_model='gpt-5-blocked';fs.writeFileSync(stateFile,JSON.stringify(raw));
 // Simulate a completed save notification without writing through the browser.
 await page.evaluate(()=>window.dispatchEvent(new Event('ccc:spawn-defaults-changed')));
 await page.waitForFunction(()=>document.querySelector('#defaults .settings-launch-basis')&& !document.querySelector('#defaults .settings-launch-output').textContent.includes('Reading saved'));
 const cfg=JSON.parse(original.queues);cfg.ALPHA.archived=true;fs.writeFileSync(queueFile,JSON.stringify(cfg));
 await page.click('#defaults .settings-launch-toolbar button');await page.waitForFunction(()=>document.querySelector('#defaults .settings-launch-output').textContent.includes('no longer available'));
 assert.equal(await page.$eval('#settingsLaunchContext-defaults',el=>el.value),'queue:ALPHA');
 await choose(page,'worker');await page.waitForFunction(()=>document.querySelector('#defaults .settings-launch-output').textContent.includes('Model policy substitution'));
});
test('read failure replaces the last preview and retry recovers, with no mutation',{skip:!nativeAvailable},async t=>{
 const page=await open(t,390);const before=fs.readFileSync(stateFile,'utf8');
 page.removeAllListeners('request');page.on('request',req=>req.url().includes('/api/')?req.respond({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:'Configuration changed'})}):req.continue());
 await page.click('#defaults .settings-launch-toolbar button');await page.waitForFunction(()=>document.querySelector('#defaults .settings-launch-output').textContent.includes('Preview unavailable'));
 assert.equal(await page.$eval('#settingsLaunchContext-defaults',el=>el.disabled),true);assert.equal(await page.$('#defaults .settings-launch-basis'),null);
 // A queue-inspection shortcut must retry, never replay cached routing after a failure.
 await page.evaluate(()=>window.cccLaunchPreview.selectQueue('ALPHA'));
 await page.waitForFunction(()=>document.querySelector('#queues .settings-launch-output').textContent.includes('Preview unavailable'));
 assert.equal(await page.$('#queues .settings-launch-basis'),null);
 page.removeAllListeners('request');page.on('request',req=>req.continue());await page.click('#defaults .settings-launch-toolbar button');await page.waitForSelector('#defaults .settings-launch-basis');
 assert.equal(fs.readFileSync(stateFile,'utf8'),before);
});
