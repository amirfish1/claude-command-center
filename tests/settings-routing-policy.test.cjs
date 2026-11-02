'use strict';
// Actual Settings nodes, legacy listeners, staged editor and real disposable API.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'routing-policy-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const between=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);};
const controls=between('  const $spawnDefaultsEngine =','  // Routing chains are staged');
const persist=between('  async function persistSpawnDefaults(','  const $runOnboardingBtn =');
const merge=between('  function mergeSpawnDefaults(','  // setSpawnDefaultModel/setSpawnEngine');
const efforts=between('  const CODEX_REASONING_LEVELS =','  function _normalizeModelId(');
const listeners=between("  if ($spawnDefaultsWorkerInherit) $spawnDefaultsWorkerInherit.addEventListener",'  // Network access modal');
const hubAction=between("        if (act === 'verify') _engHubVerify",'        return;\n      }\n      const openBtn');
const firstRunRender=between('  function renderEnginesFirstRunDefaults()', '  function refreshEnginesFirstRunFoot()');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><style>body{display:block;overflow:auto}.settings-pane{max-width:1000px;margin:auto;box-sizing:border-box}.settings-section{display:block!important}.settings-section[hidden]{display:none!important}</style></head><body><main class="settings-pane" id="fixture"></main><script src="/static/settings-routing-policy.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['defaults','routing'])document.getElementById('fixture').append(parsed.getElementById('settingsSection-'+id));
document.getElementById('settingsSection-routing').hidden=true;
const engines=document.createElement('section');engines.id='settingsSection-engines';engines.hidden=true;engines.append(parsed.getElementById('engFirstRunDefaults'));document.getElementById('fixture').append(engines);
const SPAWN_DEFAULT_ENGINES=['claude','codex','cursor','antigravity','kilo','hermes','kimi','opencode','devin','grok'],WORKER_DEFAULT_ENGINES=SPAWN_DEFAULT_ENGINES.concat(['aider','droid','pi']);
const SPAWN_DEFAULT_OTHER='__other__',normalizeSpawnDefaultEngine=v=>SPAWN_DEFAULT_ENGINES.includes(v)?v:'claude';
let spawnDefaultsState={engine:'claude',models:{},worker_inherit_session:false,disabled_engines:[]},_spawnDefaultsLoaded=false,_defaultModelsByEngine={};
const _spawnEngineChosenByUser=false,_spawnModelChosenByUserForEngine=new Set(),$convInputEngineSelect=null,$kptToolbarEngineSelect=null;
const spawnEngineLabel=v=>({claude:'Claude',codex:'Codex'})[v]||v,syncSpawnEngineDependentUi=()=>{},applyDisabledEnginesToPickers=()=>{},renderEnginesHub=()=>{},showSettingsSavedPulse=()=>{},_modelUnavailableReason=()=>'';
function setActiveSettingsRailSection(which){for(const name of ['defaults','routing','engines'])document.getElementById('settingsSection-'+name).hidden=which!==name;}
document.addEventListener('click',ev=>{const go=ev.target.closest('[data-settings-goto]');if(go)setActiveSettingsRailSection(go.getAttribute('data-settings-goto'));});
let _engHubData={};
${firstRunRender}
const catalog={claude:['claude-sonnet-4-6','claude-opus-4-6'],codex:['gpt-5-codex']};const modelOptionsForSpawnEngine=(engine,current)=>[...new Set([...(catalog[engine]||[]),current].filter(Boolean))].map(id=>({id,label:id}));
${efforts}
${controls}
${merge}
${persist}
${listeners}
window.__hub=async(engine,act)=>{const row={};${hubAction}};
window.__savedCount=0;window.cccRoutingPolicy.mount({models:modelOptionsForSpawnEngine,label:spawnEngineLabel,navigate:()=>setActiveSettingsRailSection('routing'),saved:data=>{mergeSpawnDefaults(data);window.__savedCount++;}});
</script></body></html>`;
const file=path.join(scratch,'spawn-defaults.json'),failFlag=path.join(scratch,'fail-write');
let child,browser,url,original;
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);
 const python=`import json,sys
from pathlib import Path
import server
base=Path(sys.argv[1]);server.COMMAND_CENTER_STATE_DIR=base;server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json'
server._model_policy_blocks=lambda model:False;server._model_policy_blocked_models=lambda:set()
server._model_catalog_allows_model=lambda engine,model:True
server._worker_fallback_catalog_models=lambda engine:[{'id':name} for name in {'claude':['claude-sonnet-4-6','claude-opus-4-6'],'codex':['gpt-5-codex']}.get(engine,[])]
server._codex_context_1m_enabled=lambda:False;server._codex_context_config=lambda:{'model_context_window':1000000}
raw=server._factory_spawn_defaults();raw.update(engine='claude',reasoning_effort='max',auto_compact_k=300,worker_engine='codex',worker_model='gpt-5-codex',worker_reasoning_effort='high',worker_auto_compact_k=600,future_policy={'keep':True})
raw['models'].update(claude='claude-sonnet-4-6',codex='gpt-5-codex');raw['worker_fallback']={'enabled':True,'future_guard':{'keep':True},'models':[{'engine':'claude','model':'claude-sonnet-4-6','effort':'high','future_row':'first'},{'engine':'codex','model':'gpt-5-codex','effort':'medium','future_row':'second'}]}
raw['model_profiles']={'standard':{'models':[{'engine':'claude','model':'claude-sonnet-4-6','effort':'medium','future_profile_row':True}],'future_profile':True},'future_capability':{'keep':[1,2]}}
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))
write=server._write_spawn_defaults_file
def guarded_write(value):
    if (base/'fail-write').exists():raise OSError('simulated save failure')
    write(value)
server._write_spawn_defaults_file=guarded_write
class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/api/spawn-defaults'):return super().do_GET()
        if self.path=='/':body=(base/'fixture.html').read_bytes();mime='text/html; charset=utf-8'
        elif self.path in ('/static/app.css','/static/settings-routing-policy.js'):
            body=(Path.cwd()/self.path.lstrip('/')).read_bytes();mime='text/css' if self.path.endswith('css') else 'application/javascript'
        else:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path=='/api/spawn-defaults':return super().do_POST()
        self.send_error(405)
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True);httpd.serve_forever()
`;
 child=spawn('python3',['-u','-c',python,scratch],{cwd:root,env:{...process.env,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);
 const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw new Error(stderr);})])).url;
 original=fs.readFileSync(file,'utf8');browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Routing policy screenshots:',scratch);});
async function open(t,width=1440){
 fs.writeFileSync(file,original);fs.rmSync(failFlag,{force:true});const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[],posts=[];page.setDefaultTimeout(7000);
 await page.setViewport({width,height:1000});page.on('pageerror',err=>errors.push(err.message));await page.setRequestInterception(true);
 page.on('request',req=>{if(req.method()==='POST')posts.push({url:req.url(),body:JSON.parse(req.postData())});return req.url().startsWith(url)?req.continue():req.abort();});
 t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);assert(posts.every(row=>row.url===url+'/api/spawn-defaults'&&['preview','apply'].includes(row.body.policy_action)));});
 await page.goto(url,{waitUntil:'load'});assert.equal(await page.$('#settingsSection-defaults #spawnDefaultsEngine'),null);assert(!/id="efd(Engine|Model|Effort)"/.test(html));
 await page.focus('[data-policy-open=defaults]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.includes('Saved policy loaded'));
 return {page,posts};
}
const read=()=>JSON.parse(fs.readFileSync(file,'utf8'));
async function choose(page,value){await page.select('#routingPolicyContext',value);}
async function preview(page){await page.click('#routingPolicyPreview');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Validated'));}
async function apply(page){await preview(page);await page.click('#routingPolicyApply');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Launch policy saved'));}
for(const width of [1440,390])test(`one staged draft across defaults, fallback and profiles at ${width}px`,async t=>{
 const {page,posts}=await open(t,width);const before=fs.readFileSync(file,'utf8');assert.equal(posts.length,0);
 await page.select('#spawnDefaultsEngine','codex');await page.select('#spawnDefaultsEffort','high');
 assert.equal(fs.readFileSync(file,'utf8'),before);assert.equal(await page.evaluate(()=>spawnDefaultsState.engine),'claude');
 await choose(page,'worker_fallback');await page.$eval('#routingPolicyChain fieldset button',button=>button.nextElementSibling.click());
 await choose(page,'standard');const model=await page.$('#routingPolicyChain fieldset label:nth-of-type(2) select');await model.select('claude-opus-4-6');
 await preview(page);assert.equal(fs.readFileSync(file,'utf8'),before);assert.equal(posts.length,1);assert.equal(posts[0].body.policy_action,'preview');
 assert.match(await page.$eval('#routingPolicyDiff',node=>node.textContent),/Session engine/);assert.match(await page.$eval('#routingPolicyDiff',node=>node.textContent),/Capability profiles/);
 await page.screenshot({path:path.join(scratch,`policy-review-${width}.png`),fullPage:true});
 await page.click('#routingPolicyApply');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Launch policy saved'));
 const saved=read();assert.equal(saved.engine,'codex');assert.equal(saved.reasoning_effort,'high');assert.deepEqual(saved.future_policy,{keep:true});
 assert.deepEqual(saved.worker_fallback.models.map(row=>row.future_row),['second','first']);assert.equal(saved.worker_fallback.future_guard.keep,true);
 assert.equal(saved.model_profiles.standard.models[0].future_profile_row,true);assert.equal(saved.model_profiles.standard.models[0].model,'claude-opus-4-6');assert.equal(saved.model_profiles.standard.future_profile,true);assert.deepEqual(saved.model_profiles.future_capability,{keep:[1,2]});
 assert.equal(await page.evaluate(()=>window.__savedCount),1);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await choose(page,'defaults');await page.screenshot({path:path.join(scratch,`policy-defaults-${width}.png`),fullPage:true});
 await page.$eval('#spawnDefaultsWorkerInheritanceRow',node=>node.scrollIntoView({block:'start'}));
 assert.equal(await page.$eval('#spawnDefaultsWorkerInheritanceRow .settings-row-main',node=>node.scrollWidth<=node.clientWidth+1),true);
 await page.screenshot({path:path.join(scratch,`policy-workers-${width}.png`),fullPage:true});
});
test('inheritance opts in atomically, follows staged session tuple and restores exact independent defaults',async t=>{
 const {page}=await open(t,390);const before=read();await page.focus('#spawnDefaultsWorkerInherit');await page.keyboard.press('Space');
 assert.equal(read().worker_inherit_session,undefined);assert.equal(await page.$eval('#spawnDefaultsWorkerOverrides',node=>node.hidden),true);
 await page.select('#spawnDefaultsEngine','codex');await page.select('#spawnDefaultsEffort','medium');await apply(page);
 let saved=read();assert.equal(saved.worker_engine,'codex');assert.equal(saved.worker_reasoning_effort,'medium');assert.equal(saved.worker_auto_compact_k,300);assert.equal(saved.worker_overrides.worker_auto_compact_k,600);
 saved.worker_overrides.engine='metadata-only';saved.worker_overrides.future={keep:true};fs.writeFileSync(file,JSON.stringify(saved));
 await page.click('#routingPolicyReload');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Saved policy loaded'));
 await page.focus('#spawnDefaultsWorkerInherit');await page.keyboard.press('Space');await apply(page);saved=read();
 assert.equal(saved.engine,'codex');assert.equal(saved.worker_overrides.engine,'metadata-only');assert.deepEqual(saved.worker_overrides.future,{keep:true});
 for(const key of ['worker_engine','worker_model','worker_reasoning_effort','worker_auto_compact_k'])assert.deepEqual(saved[key],before[key]);
});
test('invalid shared draft rejects all changes; capability engines remain Claude-only',async t=>{
 const {page}=await open(t);const before=fs.readFileSync(file,'utf8');await page.select('#spawnDefaultsEffort','high');await choose(page,'standard');
 assert.deepEqual(await page.$$eval('#routingPolicyChain fieldset label:first-of-type select option',nodes=>nodes.map(node=>node.value)),['claude']);
 await page.select('#routingPolicyChain fieldset label:nth-of-type(2) select','');await page.click('#routingPolicyPreview');
 await page.waitForFunction(()=>!document.querySelector('#routingPolicyError').hidden);assert.match(await page.$eval('#routingPolicyError',node=>node.textContent),/explicit model/);
 assert.equal(fs.readFileSync(file,'utf8'),before);assert.equal(await page.$eval('#routingPolicyApply',node=>node.disabled),true);
 await page.click('#routingPolicyDiscard');assert.equal(fs.readFileSync(file,'utf8'),before);
});
test('stale revision and failed atomic save retain draft without partial writes or applied event',async t=>{
 const {page}=await open(t,390);await page.select('#spawnDefaultsEffort','high');await preview(page);
 const newer=read();newer.future_policy={newer:true};fs.writeFileSync(file,JSON.stringify(newer));const before=fs.readFileSync(file,'utf8');
 await page.click('#routingPolicyApply');await page.waitForFunction(()=>document.querySelector('#routingPolicyError').textContent.includes('changed'));
 assert.equal(fs.readFileSync(file,'utf8'),before);assert.equal(await page.$eval('#routingPolicyApply',node=>node.disabled),true);assert.equal(await page.evaluate(()=>window.__savedCount),0);
 assert.equal(await page.$eval('#routingPolicyError',node=>{const r=node.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),true);
 await page.click('#routingPolicyReload');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Saved policy loaded'));
 await page.select('#spawnDefaultsEffort','high');await preview(page);fs.writeFileSync(failFlag,'fail');await page.click('#routingPolicyApply');await page.waitForFunction(()=>document.querySelector('#routingPolicyError').textContent.includes('Could not save'));
 assert.equal(fs.readFileSync(file,'utf8'),before);assert.equal(await page.$eval('#spawnDefaultsEffort',node=>node.value),'high');assert.equal(await page.evaluate(()=>window.__savedCount),0);
 fs.rmSync(failFlag);await apply(page);assert.equal(read().reasoning_effort,'high');
});
test('engine default shortcuts stage in the shared editor; old anchors and keyboard navigation resolve',async t=>{
 const {page,posts}=await open(t);const before=fs.readFileSync(file,'utf8');await page.evaluate(()=>window.__hub('codex','default'));
 assert.equal(await page.$eval('#spawnDefaultsEngine',node=>node.value),'codex');assert.equal(posts.length,0);assert.equal(fs.readFileSync(file,'utf8'),before);
 await page.evaluate(()=>{location.hash='workerFallbackSettings';});await page.waitForFunction(()=>document.querySelector('#routingPolicyContext').value==='worker_fallback');
 await page.focus('#routingPolicyContext');await page.keyboard.press('s');await page.keyboard.press('Tab');assert.notEqual(await page.evaluate(()=>document.activeElement.tagName),'BODY');
 await page.evaluate(()=>{location.hash='modelProfilesSettings';});await page.waitForFunction(()=>document.querySelector('#routingPolicyContext').value==='standard');
 await page.evaluate(()=>window.__hub('claude','worker'));assert.equal(await page.$eval('#spawnDefaultsWorkerEngine',node=>node.value),'claude');assert.equal(posts.length,0);
});
test('route add, engine edit, reorder and removal retain keyboard focus',async t=>{
 const {page}=await open(t,390);await choose(page,'worker_fallback');
 await page.focus('#routingPolicyChain fieldset:first-of-type button:nth-child(2)');await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement.tagName),'SELECT');
 await choose(page,'fast');await page.focus('#routingPolicyChain > button');await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement.closest('label').firstChild.textContent),'Model');
 await page.focus('#routingPolicyChain fieldset button:last-child');await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Add route');
 assert.equal(fs.readFileSync(file,'utf8'),original);
});

test('first-run setup uses the same visible editor and can return without saving',async t=>{
 const {page,posts}=await open(t,390);const before=fs.readFileSync(file,'utf8');
 await page.evaluate(()=>{document.getElementById('fixture').classList.add('settings-modal','is-first-run');window.__cccEnginesFirstRun=true;renderEnginesFirstRunDefaults();setActiveSettingsRailSection('engines');});
 await page.focus('#engFirstRunDefaults [data-policy-open]');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>!document.getElementById('settingsSection-routing').hidden);
 assert.equal(await page.$eval('#routingPolicyEditor .eng-defaults',node=>getComputedStyle(node).display!=='none'),true);
 await page.focus('.routing-policy-first-run');await page.keyboard.press('Enter');
 assert.equal(await page.$eval('#settingsSection-engines',node=>node.hidden),false);
 assert.equal(posts.length,0);assert.equal(fs.readFileSync(file,'utf8'),before);
});
