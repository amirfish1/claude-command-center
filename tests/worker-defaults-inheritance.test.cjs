'use strict';
// Native Settings markup, CSS, renderers and listeners against the real save
// endpoint. The Python fixture has only a temporary defaults/config directory.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'worker-inheritance-'));
function between(start, end) {
 const a=app.indexOf(start);assert(a>=0,start);const b=app.indexOf(end,a+start.length);assert(b>=0,end);return app.slice(a,b);
}
const controls = between("  const $spawnDefaultsEngine =", '  function renderWorkerFallback()');
const persist = between('  async function persistSpawnDefaults(', '  const $runOnboardingBtn =');
const merge = between('  function mergeSpawnDefaults(', '  // setSpawnDefaultModel/setSpawnEngine');
const efforts = between('  const CODEX_REASONING_LEVELS =', '  function _normalizeModelId(');
const listeners = between("  if ($spawnDefaultsWorkerInherit) $spawnDefaultsWorkerInherit.addEventListener", '  // Network access modal');
const handoff = between('  function _engHubDefaultHandoff(', '  async function _engHubTerminal(');
const hubAction = between("        if (act === 'verify') _engHubVerify", '        return;\n      }\n      const openBtn');
const encoded = JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')).replace(/</g,'\\u003c');
function fixture(){return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><style>
body{display:block;overflow:auto}.settings-pane{box-sizing:border-box;max-width:980px;margin:0 auto}.settings-section{display:block!important}.eng-card{align-items:flex-start}#settingsSection-defaults .eng-defaults{display:block}#settingsSection-defaults .eng-defaults-card{padding:12px 0}.fixture-title{font:20px system-ui;padding:16px}#spawnDefaultsError{display:block;color:#f88}
</style></head><body><div class="fixture-title">Settings · Session defaults</div><main id="fixture" class="settings-pane"></main><div id="spawnDefaultsError" role="alert" hidden></div>
<script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');
document.getElementById('fixture').append(parsed.getElementById('settingsSection-defaults'));document.getElementById('settingsModelUsage').closest('.settings-row').remove();
const SPAWN_DEFAULT_ENGINES=['claude','codex','cursor','antigravity','kilo','hermes','kimi','opencode','devin','grok'],WORKER_DEFAULT_ENGINES=SPAWN_DEFAULT_ENGINES.concat(['aider','droid','pi']),ENGINE_HUB_WORKER_ENGINES=WORKER_DEFAULT_ENGINES;
const SPAWN_DEFAULT_OTHER='__other__',normalizeSpawnDefaultEngine=v=>SPAWN_DEFAULT_ENGINES.includes(v)?v:'claude';
let spawnDefaultsState={engine:'claude',models:{},worker_inherit_session:false,disabled_engines:[]},_spawnDefaultsLoaded=false,_defaultModelsByEngine={};
const _spawnEngineChosenByUser=false,_spawnModelChosenByUserForEngine=new Set(),$convInputEngineSelect=null,$kptToolbarEngineSelect=null;
const spawnEngineLabel=v=>({claude:'Claude',codex:'Codex',kimi:'Kimi'})[v]||v;
const syncSpawnEngineDependentUi=()=>{},applyDisabledEnginesToPickers=()=>{},renderEnginesHub=()=>{},renderWorkerFallback=()=>{},renderModelProfile=()=>{};
const showOpToast=(text)=>{document.body.dataset.toast=text;},showSettingsSavedPulse=row=>{row.dataset.saved='true';};
const _modelUnavailableReason=()=>'';
// Deterministic model catalog; persistence and inheritance use the server.
const modelOptionsForSpawnEngine=(engine,current)=>[...new Set([engine==='claude'?'session-claude':'session-codex',current,'__other__'].filter(Boolean))].map(id=>({id,label:id==='__other__'?'Other…':id}));
${efforts}
${controls}
${merge}
${persist}
${listeners}
const _engHubRows=()=>[{engine:'claude',state:'ready',meta:{label:'Claude'}},{engine:'codex',state:'ready',meta:{label:'Codex'}}];
${handoff}
window.__hubWorker=async engine=>{const act='worker';${hubAction}};
window.__hubDisable=async engine=>{const next=[engine],change=_engHubDefaultHandoff(engine,next);if(change)await _engHubSaveDisabled(next,change.extra);};
window.__reload=async()=>{const response=await fetch('/api/spawn-defaults');mergeSpawnDefaults(await response.json());document.body.dataset.ready='true';};
window.__reload();
</script></body></html>`;}
let child,browser,url,original;
const stateFile=path.join(scratch,'spawn-defaults.json');
function read(){return JSON.parse(fs.readFileSync(stateFile,'utf8'));}
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture());
 const python = `import json, sys, threading
from pathlib import Path
import server
base=Path(sys.argv[1]); server.COMMAND_CENTER_STATE_DIR=base; server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json'
server._model_policy_blocks=lambda model: False
server._model_policy_blocked_models=lambda: set()
server._codex_context_1m_enabled=lambda: False
server._codex_context_config=lambda: {'model_context_window': 1000000}
raw=server._factory_spawn_defaults();raw.update(engine='claude',reasoning_effort='max',auto_compact_k=300,worker_engine='codex',worker_model='independent-worker',worker_reasoning_effort='high',worker_auto_compact_k=600)
raw['models'].update(claude='session-claude',codex='session-codex')
raw['future_policy']={'keep':True}
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))
class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self, path): return False
    def _check_proxy_secret(self): return True
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path=='/api/spawn-defaults': return super().do_GET()
        if self.path=='/static/app.css':
            body=(Path.cwd()/'static/app.css').read_bytes();ctype='text/css'
        elif self.path=='/':
            body=(base/'fixture.html').read_bytes();ctype='text/html; charset=utf-8'
        else: return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/spawn-defaults': return self.send_error(404)
        return super().do_POST()
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True)
httpd.serve_forever()
`;
 child=spawn('python3',['-u','-c',python,scratch],{cwd:root,env:{...process.env,CCC_EPHEMERAL:'1'}});
 let stderr='';child.stderr.on('data',chunk=>{stderr+=chunk;});
 const lines=readline.createInterface({input:child.stdout});
 const started=await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw new Error(stderr);})]);
 url=started.url;original=read();browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Worker inheritance screenshots:',scratch);});
async function open(t,width=1440){
 fs.writeFileSync(stateFile,JSON.stringify(original));
 const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],posts=[];page.setDefaultTimeout(7000);
 await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));page.on('request',req=>{if(req.method()==='POST')posts.push(JSON.parse(req.postData()));});
 await page.setRequestInterception(true);page.on('request',req=>req.url().startsWith(url)?req.continue():req.abort());
 t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});await page.goto(url,{waitUntil:'load'});await page.waitForFunction(()=>document.body.dataset.ready==='true');return {page,posts};
}
async function toggle(page,on){await page.focus('#spawnDefaultsWorkerInherit');await page.keyboard.press('Space');await page.waitForFunction(value=>!document.getElementById('spawnDefaultsWorkerInherit').disabled&&document.getElementById('spawnDefaultsWorkerInherit').checked===value,{},on);}
for(const width of [1440,390]){
 test(`explicit opt-in, synchronized effective fields and restore at ${width}px`,async t=>{
  const {page,posts}=await open(t,width);assert.equal(posts.length,0);assert.equal(read().worker_inherit_session,undefined);
  assert.equal(await page.$eval('#spawnDefaultsWorkerInherit',el=>el.checked),false);
  assert.equal(await page.$eval('#spawnDefaultsWorkerModel',el=>el.value),'independent-worker');
  assert.equal(await page.$eval('#spawnDefaultsWorkerInheritanceRow .settings-row-desc',el=>getComputedStyle(el).display!=='none'),true);
  assert.equal(await page.evaluate(()=>Math.abs(document.getElementById('spawnDefaultsWorkerInherit').getBoundingClientRect().top-document.querySelector('label[for=spawnDefaultsWorkerInherit]').getBoundingClientRect().top)<12),true);
  await page.screenshot({path:path.join(scratch,`independent-${width}.png`),fullPage:true});await toggle(page,true);
  assert.deepEqual(posts.at(-1),{worker_inherit_session:true});
  assert.equal(read().worker_engine,'claude');assert.equal(read().worker_model,'session-claude');assert.equal(read().worker_reasoning_effort,'max');
  assert.equal(read().worker_overrides.worker_model,'independent-worker');
  assert.equal(await page.$eval('#spawnDefaultsWorkerOverrides',el=>el.hidden),true);
  assert.equal(await page.$eval('#spawnDefaultsWorkerEngine',el=>el.disabled),true);
  await page.select('#spawnDefaultsEngine','codex');await page.waitForFunction(()=>document.getElementById('spawnDefaultsWorkerInheritanceStatus').textContent.includes('Codex'));
  assert.equal(read().worker_engine,'codex');assert.equal(read().worker_model,'session-codex');assert.equal(read().worker_reasoning_effort,'');
  assert.equal(Object.keys(posts.at(-1)).some(key=>key.startsWith('worker_')),false);
  await page.select('#spawnDefaultsEffort','high');await page.waitForFunction(()=>document.getElementById('spawnDefaultsWorkerInheritanceStatus').textContent.includes('high'));
  await page.$eval('#spawnDefaultsAutoCompact',el=>{el.value=420;el.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(()=>document.getElementById('spawnDefaultsWorkerInheritanceStatus').textContent.includes('420K'));
  assert.equal(read().worker_auto_compact_k,420);assert.equal(read().worker_reasoning_effort,'high');
  await page.screenshot({path:path.join(scratch,`inherited-${width}.png`),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  await page.reload({waitUntil:'load'});await page.waitForFunction(()=>document.body.dataset.ready==='true');assert.equal(await page.$eval('#spawnDefaultsWorkerInherit',el=>el.checked),true);
  await toggle(page,false);assert.equal(read().worker_model,'independent-worker');assert.equal(read().worker_auto_compact_k,600);
  assert.equal(await page.$eval('#spawnDefaultsWorkerOverrides',el=>el.hidden),false);
  assert.equal(await page.$eval('#spawnDefaultsWorkerModel',el=>el.value),'independent-worker');
 });
}
test('failed opt-in leaves controls and saved worker configuration independent',async t=>{
 const {page}=await open(t,390);await page.setRequestInterception(false);
 await page.setRequestInterception(true);page.removeAllListeners('request');page.on('request',req=>req.method()==='POST'?req.respond({status:400,contentType:'application/json',body:JSON.stringify({ok:false,error:'Fixture rejection'})}):req.continue());
 const before=fs.readFileSync(stateFile,'utf8');await page.focus('#spawnDefaultsWorkerInherit');await page.keyboard.press('Space');await page.waitForFunction(()=>document.getElementById('spawnDefaultsError').textContent.includes('Fixture rejection'));
 assert.equal(await page.$eval('#spawnDefaultsWorkerInherit',el=>el.checked),false);assert.equal(await page.$eval('#spawnDefaultsWorkerOverrides',el=>el.hidden),false);assert.equal(fs.readFileSync(stateFile,'utf8'),before);
 assert.match(await page.$eval('#spawnDefaultsWorkerInheritanceStatus',el=>el.textContent),/Fixture rejection/);
 assert.equal(await page.$eval('#spawnDefaultsWorkerInheritanceStatus',el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),true);
});
test('independent edits stay independent and engine hub handoff follows inheritance',async t=>{
 const {page}=await open(t);
 await page.select('#spawnDefaultsWorkerEngine','claude');await page.waitForFunction(()=>document.getElementById('spawnDefaultsWorkerEngine').closest('.settings-row').dataset.saved==='true');
 assert.equal(read().engine,'claude');assert.equal(read().worker_engine,'claude');assert.equal(read().worker_inherit_session,false);
 await toggle(page,true);await page.evaluate(()=>window.__hubDisable('claude'));
 assert.equal(read().engine,'codex');assert.equal(read().worker_engine,'codex');assert.equal(read().worker_inherit_session,true);
 await page.evaluate(()=>window.__hubWorker('claude'));await page.waitForFunction(()=>!document.getElementById('spawnDefaultsWorkerInherit').checked);
 assert.equal(read().worker_inherit_session,false);assert.equal(read().worker_engine,'claude');assert.equal(read().worker_auto_compact_k,600);assert.deepEqual(read().future_policy,{keep:true});
});
