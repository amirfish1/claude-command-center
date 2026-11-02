// Opt-in integration check: runs ten real, short Codex turns (Linux only).
// Launch a disposable worker with CCC_STATE_DIR and CCC_WORKER_SOCKET, then
// an ephemeral dashboard with the same environment. Pass its loopback URL as
// CCC_TEST_URL and its state directory as CCC_STARTUP_WORKER_STATE. Set
// CCC_STARTUP_REAL=1 and CCC_STARTUP_EVIDENCE_DIR explicitly. The harness
// restarts only that worker, at idle trial boundaries, to test cold ownership.
// Full-list reads are withheld in alternating trials; engine, canonical and
// timeline endpoints use real responses. Unrelated startup APIs are stubbed.
// Evidence includes session data and belongs outside the public repository.
const fs=require('fs'),assert=require('assert/strict'),cp=require('child_process');
const path=require('path');
const root=path.resolve(__dirname,'..'),puppeteer=require(root+'/require-puppeteer.js');
assert.equal(process.env.CCC_STARTUP_REAL,'1','Set CCC_STARTUP_REAL=1 to authorize real Codex trials');
const {findChromePath}=require(root+'/puppeteer-browser-config.js');
const out=process.env.CCC_STARTUP_EVIDENCE_DIR;
assert.ok(out,'Set CCC_STARTUP_EVIDENCE_DIR (artifacts contain local session data)');
const stateDir=process.env.CCC_STARTUP_WORKER_STATE;
assert.ok(stateDir,'Set CCC_STARTUP_WORKER_STATE to a disposable verification worker state directory');
assert.notEqual(path.resolve(stateDir),path.resolve(process.env.HOME,'.claude','command-center'));
assert.notEqual(path.resolve(stateDir),path.resolve(process.env.HOME,'.ccc'));
const base=process.env.CCC_TEST_URL;
assert.ok(base,'Set CCC_TEST_URL to an isolated dashboard using that worker');
assert.equal(new URL(base).hostname,'127.0.0.1');
const discovery=path.join(process.env.HOME,'.claude','command-center','port.txt');
if(fs.existsSync(discovery))assert.notEqual(base.replace(/\/$/,''),fs.readFileSync(discovery,'utf8').trim().replace(/\/$/,''),'Do not run against the managed dashboard');
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
const model=process.env.CCC_STARTUP_MODEL||'gpt-6.1-sol';
const prompt='This is a UI latency check. Reply with exactly: Startup check complete. Do not call any tools.';
const injection=`window.__smoke={start(){_startupApiReadsReleased=true;
 currentSpawnChoice=()=>({engine:'codex',model:${JSON.stringify(model)},effort:'low'});
 getSpawnCwd=()=>${JSON.stringify(root)};findSpawnCwdRepo=()=>({path:${JSON.stringify(root)}});
 settleRepoGuessBeforeSend=async()=>{};resetRepoGuess=()=>{};assignSpawnedSessionToDefaultObject=()=>{};recordSpawnChoice=()=>{};
 repoGuessAuto=null;repoGuessUserPicked=true;
 currentConversation='__new__';paneByPaneId(activePaneId()).conversationId='__new__';
 __smoke.trial={epoch:Date.now(),t0:performance.now(),hidden:[],selected:[],marks:[]};
 const record=(name,test)=>{if(__smoke.trial[name]||__smoke.trial[name+'Scheduled']||!test())return;
  __smoke.trial[name+'Scheduled']=true;requestAnimationFrame(()=>requestAnimationFrame(()=>{
   if(test()&&!document.hidden&&!__smoke.trial[name])__smoke.trial[name]=performance.now();__smoke.trial[name+'Scheduled']=false;
  }));};
 new MutationObserver(()=>{const view=getConvView();if(!view)return;
  record('prompt',()=>!!view.querySelector('.user-msg')?.textContent.includes('This is a UI latency check.'));
  record('assistant',()=>Array.from(view.querySelectorAll('.assistant-text')).some(el=>el.textContent.trim()&&el.getBoundingClientRect().height>0));
  record('row',()=>Array.from(document.querySelectorAll('#convList [data-id]')).some(el=>el.dataset.id===currentSession.id&&el.getBoundingClientRect().height>0));
 }).observe(document.body,{subtree:true,childList:true,characterData:true});
 const sample=()=>{__smoke.trial.hidden.push(document.hidden);__smoke.trial.selected.push(currentConversation);setTimeout(sample,50);};sample();
 spawnFromInlineInput(${JSON.stringify(prompt)});
 setTimeout(()=>{fetch('/api/conversations/list?window=1d&startup-measurement=1',{cccPriority:'user'}).catch(()=>{});},100);
 },canonical(){return fetchConversationEvents();},state(){return {id:currentConversation,session:currentSession,view:getConvView().innerText,answers:Array.from(getConvView().querySelectorAll('.assistant-text')).filter(el=>el.getBoundingClientRect().height>0).map(el=>el.innerText),prompts:Array.from(getConvView().querySelectorAll('.user-msg')).filter(el=>el.getBoundingClientRect().height>0).map(el=>el.innerText),trial:__smoke.trial};}};\n`;
fs.mkdirSync(out,{recursive:true});
async function coldOwner(trial){
 const oldPid=Number(fs.readFileSync(path.join(stateDir,'worker.pid'),'utf8').trim());
 const cmd=fs.readFileSync('/proc/'+oldPid+'/cmdline','utf8').split('\0');assert.ok(cmd.some(arg=>path.basename(arg)==='ccc_worker.py'));
 const env=fs.readFileSync('/proc/'+oldPid+'/environ','utf8').split('\0');
 assert.ok(env.includes('CCC_STATE_DIR='+stateDir),'Worker must use the explicitly selected verification state');
 assert.equal(fs.readlinkSync('/proc/'+oldPid+'/cwd'),root);
 const health=JSON.parse(cp.execFileSync('python3',['ccc_worker.py','--health'],{cwd:root,env:{...process.env,CCC_STATE_DIR:stateDir,CCC_WORKER_SOCKET:path.join(stateDir,'worker.sock')},encoding:'utf8'}));
 assert.equal(health.active,0,'Only restart an idle verification worker');
 assert.equal(health.queued,0);assert.equal(health.uncertain,0);
 process.kill(oldPid,'SIGTERM');
 for(let n=0;n<50;n++){try{process.kill(oldPid,0);}catch{break;}await new Promise(r=>setTimeout(r,100));}
 const fd=fs.openSync(out+'/owner-'+trial+'.log','a');
 const child=cp.spawn('python3',['ccc_worker.py'],{cwd:root,env:{...process.env,CCC_STATE_DIR:stateDir,CCC_WORKER_SOCKET:path.join(stateDir,'worker.sock')},stdio:['ignore',fd,fd],detached:true});fs.closeSync(fd);child.unref();
 for(let n=0;n<100;n++){
  try{if(Number(fs.readFileSync(path.join(stateDir,'worker.pid'),'utf8').trim())===child.pid&&fs.existsSync(path.join(stateDir,'worker.sock')))return {oldPid,pid:child.pid,at:Date.now()};}catch{}
  await new Promise(r=>setTimeout(r,100));
 }
 throw Error('Isolated owner did not become ready');
}
(async()=>{
 const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});let results=[];
 try{for(let i=0;i<10;i++){
  const p=await browser.newPage();await p.bringToFront();await p.setViewport({width:1440,height:1000});
  await p.evaluateOnNewDocument(()=>{localStorage.setItem('ccc-tour-done','1');localStorage.setItem('ccc-ui-mode','advanced');localStorage.setItem('ccc-sidebar-tab','ideas');window.EventSource=class{addEventListener(){}removeEventListener(){}close(){}};});
  let active=false,spawn=null,held=[],listReads=0,canonical=0,errors=[];
  const heldMode=i%2===0;
  p.on('pageerror',e=>errors.push(e.message));
  p.on('response',async r=>{if(new URL(r.url()).pathname==='/api/sessions/spawn-codex'){spawn=await r.json();console.log('spawn',i+1,JSON.stringify(spawn));}});
  await p.setRequestInterception(true);p.on('request',r=>{(async()=>{
   const u=new URL(r.url()),json=x=>r.respond({contentType:'application/json',body:JSON.stringify(x)});
   if(u.pathname==='/static/app.js')return r.respond({contentType:'text/javascript',body:source.replace('  function _watchCodexSpawnRegistration(',injection+'  function _watchCodexSpawnRegistration(')});
   if(['/api/sessions/spawn-codex','/api/control-plane/graph','/api/codex/client/live-transcript','/api/session/spawn-timeline/mark','/api/session/spawn-timeline'].includes(u.pathname))return r.continue();
   if(/^\/api\/conversations\/[0-9a-f-]{36}$/.test(u.pathname)){canonical++;return r.continue();}
   if(u.pathname==='/api/conversations/list'){
    if(active){listReads++;if(heldMode){held.push({r,at:Date.now()});return;}return r.continue();}
    return json({conversations:[],total:0});
   }
   if(u.pathname.startsWith('/api/')){if(u.pathname==='/api/sessions')return json([]);if(u.pathname==='/api/queue/status')return json({queues:[]});return json({});}
   return r.continue();
  })().catch(e=>{errors.push(e.message);try{r.abort();}catch{}});});
  await p.goto(base+'/',{waitUntil:'domcontentloaded'});await p.waitForFunction(()=>window.__smoke);
  const owner=(i===0||i===5)?await coldOwner(i+1):null;
  // Intentionally no dashboard-paint readiness wait: retain boot contention.
  active=true;await p.evaluate(()=>__smoke.start());
  await p.waitForFunction(()=>window.__smoke.trial?.assistant,{timeout:60000}).catch(e=>errors.push(e.message));
  await new Promise(r=>setTimeout(r,1100));
  const heldAtPaint=held.length;
  let state=await p.evaluate(()=>__smoke.state());
  const responsesReleasedAt=Date.now();
  for(const h of held)await h.r.continue();held=[];
  await p.evaluate(()=>__smoke.canonical());await new Promise(r=>setTimeout(r,500));
  state=await p.evaluate(()=>__smoke.state());
  const timeline=spawn?.session_id?await(await fetch(base+'/api/session/spawn-timeline?session_id='+spawn.session_id)).json():null;
  const m=timeline?.timeline?.marks||{},tr=state.trial;
  const observedPaintMs=tr.assistant-tr.t0;
  const rowPaintMs=tr.row-tr.t0;
  const view=state.view;
  const result={trial:i+1,mode:heldMode?'real full-list response withheld through assistant paint':'real normal full-list',ownerReconnect:owner,spawn,state,timeline,canonical,listReads,heldAtPaint,responsesReleasedAt,errors,
   metrics:{prompt_ms:tr.prompt-tr.t0,codex_first_text_delta:m.codex_first_text_delta,client_first_visible_output:m.client_first_visible_output,client_row_resolved:m.client_row_resolved,spawn_response_sent:m.spawn_response_sent,
    delta_to_paint_ms:m.client_first_visible_output-m.codex_first_text_delta,observed_delta_to_paint_ms:observedPaintMs-m.codex_first_text_delta,observed_row_after_response_ms:rowPaintMs-m.spawn_response_sent,row_after_response_ms:m.client_row_resolved-m.spawn_response_sent,engine_accept_ms:m.turn_accepted,engine_response_ms:m.spawn_response_sent,engine_first_text_ms:m.codex_first_text_delta,hidden_ms:m.client_first_visible_output_hidden_ms,unselected_ms:m.client_first_visible_output_unselected_ms,
    hidden_samples:tr.hidden.filter(Boolean).length,selection_ids:[...new Set(tr.selected)]}};
  results.push(result);fs.writeFileSync(out+'/trials.json',JSON.stringify(results,null,2));console.log(JSON.stringify({trial:i+1,mode:result.mode,owner:owner,metrics:result.metrics,errors}));
  await p.screenshot({path:out+'/trial-'+(i+1)+'.png'});await p.close();
  assert.equal(state.answers.filter(text=>text.includes('Startup check complete.')).length,1,'canonical handoff keeps one answer');
  assert.equal(state.prompts.filter(text=>text.includes('This is a UI latency check.')).length,1,'canonical handoff keeps one prompt');
  assert.ok(!view.includes('Waiting for first visible output'),'headline acknowledges actual output');
  assert.ok(spawn?.session_id,'real accepted session');assert.equal(errors.length,0);
  assert.ok(Number.isFinite(result.metrics.delta_to_paint_ms),'real engine delta and client paint marks exist');
  assert.equal(result.metrics.hidden_samples,0);assert.equal(result.metrics.hidden_ms,0);assert.equal(result.metrics.unselected_ms,0);
  assert.ok(result.metrics.selection_ids.every(id=>id==='__new__'||id.startsWith('spawning-')||id===spawn.session_id));
  if(heldMode)assert.ok(heldAtPaint>0,'actual full-list request remained unresolved through paint');
 }
 const p95=key=>results.map(r=>r.metrics[key]).sort((a,b)=>a-b)[Math.ceil(results.length*0.95)-1];
 const aggregate={n:results.length,prompt_p95_ms:p95('prompt_ms'),delta_to_paint_p95_ms:p95('delta_to_paint_ms'),row_after_response_p95_ms:p95('row_after_response_ms'),observed_delta_to_paint_p95_ms:p95('observed_delta_to_paint_ms'),observed_row_after_response_p95_ms:p95('observed_row_after_response_ms'),all_prompts_le250:results.every(r=>r.metrics.prompt_ms<=250)};
 fs.writeFileSync(out+'/aggregate.json',JSON.stringify(aggregate,null,2));console.log(JSON.stringify(aggregate));
 assert.ok(aggregate.all_prompts_le250,'every real prompt paints <=250ms');assert.ok(aggregate.delta_to_paint_p95_ms<=2000);assert.ok(aggregate.row_after_response_p95_ms<=5000);assert.ok(aggregate.observed_delta_to_paint_p95_ms<=2000);assert.ok(aggregate.observed_row_after_response_p95_ms<=5000);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
