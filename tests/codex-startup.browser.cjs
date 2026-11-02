// Run against an isolated dashboard: CCC_TEST_URL=http://127.0.0.1:PORT node tests/codex-startup.browser.cjs
// All API writes are intercepted. Engine timings below are deterministic delivery replays.
const fs=require('fs'),assert=require('assert/strict'),path=require('path');
const root=path.resolve(__dirname,'..'),puppeteer=require(root+'/require-puppeteer.js');
const {findChromePath}=require(root+'/puppeteer-browser-config.js');
const before=process.env.CCC_STARTUP_BASELINE==='1';
const out=process.env.CCC_STARTUP_EVIDENCE_DIR||'/tmp/ccc-startup-evidence';
const base=process.env.CCC_TEST_URL;
assert.ok(base,'Set CCC_TEST_URL to an isolated dashboard');fs.mkdirSync(out,{recursive:true});
const source=fs.readFileSync(process.env.CCC_STARTUP_APP_SOURCE||path.join(root,'static/app.js'),'utf8');
const liveSource=fs.readFileSync(process.env.CCC_STARTUP_LIVE_SOURCE||path.join(root,'static/codex-live-source.js'),'utf8');
const sid='00000000-0000-4000-8000-000000000001',pid='codex-app-fixture',cwd='/tmp/startup-fixture';
const prompt='Explain this startup fixture.',partial='The first part',answer='The first part of the answer.';
const injection=`
window.__startup = {
 start(options = {}) {
  _startupApiReadsReleased=true;
  currentSpawnChoice=()=>({engine:'codex',model:'gpt-test',effort:''});
  repoGuessTimer=null;repoGuessInflight=options.guessDelayMs?new Promise(resolve=>setTimeout(resolve,options.guessDelayMs)):null;
  getSpawnCwd=()=>${JSON.stringify(cwd)};
  findSpawnCwdRepo=()=>({path:${JSON.stringify(cwd)}});
  resetRepoGuess=()=>{};assignSpawnedSessionToDefaultObject=()=>{};recordSpawnChoice=()=>{};
  repoGuessAuto=null;repoGuessUserPicked=true;
  currentConversation='__new__';
  const pane=paneByPaneId(activePaneId());pane.conversationId='__new__';
  window.__trial={t0:performance.now(),epoch:Date.now(),marks:[],hidden:[],selected:[]};
  const record=(key,test)=>{
   if(__trial[key]||__trial[key+'Scheduled']||!test())return;
   __trial[key+'Scheduled']=true;
   requestAnimationFrame(()=>requestAnimationFrame(()=>{if(test()&&!document.hidden&&!__trial[key])__trial[key]=performance.now();__trial[key+'Scheduled']=false;}));
  };
  window.__trialObserver=new MutationObserver(()=>{
   const v=getConvView();
   record('prompt',()=>!!v?.querySelector('.user-msg')?.textContent.includes('Explain this startup fixture.'));
   record('assistant',()=>Array.from(v?.querySelectorAll('.assistant-text')||[]).some(el=>el.textContent.includes('The first part')&&el.getBoundingClientRect().height>0));
   record('complete',()=>Array.from(v?.querySelectorAll('.assistant-text')||[]).some(el=>el.textContent.includes('The first part of the answer.')&&el.getBoundingClientRect().height>0));
   record('row',()=>Array.from(document.querySelectorAll('#convList [data-id]')).some(el=>el.dataset.id==='00000000-0000-4000-8000-000000000001'&&el.getBoundingClientRect().height>0));
  });__trialObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
  const sample=()=>{__trial.hidden.push(document.hidden);__trial.selected.push(currentConversation);__trial.timer=setTimeout(sample,50);};sample();
  spawnFromInlineInput(${JSON.stringify(prompt)});
 },
 canonical(){return fetchConversationEvents();},
 state(){return {live:Array.from(window.CCCCodexLiveSource?.__testing.panes.values()||[]).map(e=>({context:e.context,fresh:{threadId:CCCCodexClientContext(e.paneEl).threadId,repoPath:CCCCodexClientContext(e.paneEl).repoPath},generation:e.generation,active:e.active,inFlight:e.inFlight,timer:e.timer,startupUntil:e.startupUntil})),id:currentConversation,session:currentSession,rows:conversationsData.map(r=>({id:r.id,pending:r.pending_spawn})),view:getConvView().innerText,html:getConvView().innerHTML,trial:__trial};}
};
`;
function event(text,live=false){return {type:'assistant',line:live?undefined:3,turn_id:'fixture-turn',msg_id:'fixture-message',live_key:live?'fixture-turn:fixture-message':undefined,provisional:live||undefined,blocks:[{kind:'text',text}]};}
(async()=>{
 const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});let results=[];
 try {
  for(let i=0;i<(before?2:10);i++){
   const page=await browser.newPage();await page.bringToFront();await page.setViewport({width:1440,height:1000});
   await page.evaluateOnNewDocument(()=>{
    localStorage.setItem('ccc-tour-done','1');localStorage.setItem('ccc-ui-mode','advanced');localStorage.setItem('ccc-sidebar-tab','ideas');
    window.EventSource=class {constructor(url){this.url=url;}addEventListener(){}removeEventListener(){}close(){}};
   });
   let start=0,accepted=0,delta=0,livePolls=0,heldLists=0,canonical=false,failOnce=i===4,errors=[];let emptyReconnect=false;
   page.on('pageerror',e=>errors.push(e.message));
   await page.setRequestInterception(true);
   page.on('request',r=>{(async()=>{
    const u=new URL(r.url()),json=data=>r.respond({contentType:'application/json',body:JSON.stringify(data)});
    if(u.pathname==='/static/app.js')return r.respond({contentType:'text/javascript',body:source.replace('  function _watchCodexSpawnRegistration(',injection+'  function _watchCodexSpawnRegistration(')});
    if(u.pathname==='/static/codex-live-source.js')return r.respond({contentType:'text/javascript',body:liveSource});
    if(u.pathname==='/api/spawn/codex'||(r.method()==='POST'&&u.pathname.includes('spawn')&&!u.pathname.includes('timeline'))){
     start=Date.now();await new Promise(x=>setTimeout(x,i%2?650:100));accepted=Date.now();delta=accepted+250;
     return json({ok:true,session_id:sid,spawn_id:pid,log:'fixture.log'});
    }
    if(u.pathname==='/api/session/spawn-timeline/mark'){
     const data=JSON.parse(r.postData());await page.evaluate(data=>__trial.marks.push(data),data);return json({ok:true});
    }
    if(u.pathname==='/api/session/spawn-timeline')return json({ok:true,timeline:{engine:'codex',marks:{spawn_response_sent:accepted-start}}});
    if(u.pathname==='/api/sessions/spawned')return json(start?[{session_id:sid,spawn_id:pid}]:[]);
    if(u.pathname==='/api/codex/client/live-transcript'){
     livePolls++;if(failOnce){failOnce=false;return r.respond({status:503,contentType:'application/json',body:'{}'});}
     if((i===5&&livePolls===3)||(i===6&&livePolls===1)){
      if(i===5){emptyReconnect=true;assert.ok(await page.evaluate(()=>document.querySelector('.assistant-text')?.textContent.includes('The first part')),'partial remains before reconnect');}
      return json({ok:true,generation:'reconnected',cursor:livePolls,turns:[],requests:[]});
     }
     const text=Date.now()>=delta+1400?answer:Date.now()>=delta?partial:'';
     return json({ok:true,generation:livePolls>2?'reconnected':'initial',cursor:livePolls,turns:[{turn_id:'fixture-turn',status:'inProgress',events:text?[event(text,true)]:[]}],requests:[]});
    }
    if(u.pathname==='/api/conversations/'+sid){
     const final=canonical;await new Promise(x=>setTimeout(x,final?0:3400));
     const events=[{type:'user_text',line:2,turn_id:'fixture-turn',text:prompt}];if(final)events.push(event(answer));
     return json({engine:'codex',events,last_line:final?3:2,first_line:1});
    }
    if(u.pathname==='/api/conversations/list'){
     if(start){heldLists++;return;}
     return json({conversations:[],total:0});
    }
    if(u.pathname==='/api/session/landed')return json({landed:true});
    if(u.pathname.startsWith('/api/')){
     if(u.pathname==='/api/sessions')return json([]);
     if(u.pathname==='/api/queue/status')return json({queues:[]});
     if(u.pathname==='/api/repo/known')return json({repos:[]});
     if(u.pathname==='/api/engines/installed')return json({engines:[]});
     return json({});
    }
    return r.continue();
   })().catch(e=>{errors.push('route '+e.message);try{r.abort();}catch{}});});
   await page.goto(base+'/',{waitUntil:'domcontentloaded'});
   await page.waitForFunction(()=>window.__startup,{timeout:30000});
   // Start from a painted dashboard, as a user submitting its composer does.
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await page.evaluate(guessDelayMs=>{__startup.start({guessDelayMs});setTimeout(()=>{fetch('/api/conversations/list?deliberately-held=1',{cccPriority:'user'}).catch(()=>{});},100);},i%2?800:0);
   await page.waitForFunction(()=>window.__trial?.assistant,{timeout:before?8500:8500}).catch(()=>{});
   let s=await page.evaluate(()=>__startup.state());
   if(!before){
    await page.waitForFunction(()=>window.__trial?.complete,{timeout:8500}).catch(async error=>{fs.writeFileSync(out+'/failure.json',JSON.stringify({state:await page.evaluate(()=>__startup.state()),errors,livePolls,start,accepted,delta},null,2));throw error;});
    canonical=true;await page.evaluate(()=>__startup.canonical());await new Promise(x=>setTimeout(x,3700));s=await page.evaluate(()=>__startup.state());
    assert.equal((s.view.match(/The first part of the answer\./g)||[]).length,1,'one canonical answer');
    assert.ok(s.view.includes(prompt),'prompt retained');
    assert.equal((await page.$$eval('.conversations-view .event.user_text',els=>els.length)),1,'one canonical prompt');
    assert.ok(s.view.indexOf(prompt)<s.view.indexOf(answer),'prompt precedes answer');
    assert.ok(heldLists>0,'full-list completion is withheld');
    assert.equal(errors.length,0,'no page or route errors');
    assert.ok(!s.view.includes('Waiting for first visible output'),'headline acknowledges output');
    assert.equal(await page.$eval('.conv-pane.is-active [data-role="pane-session-id"]', el=>el.dataset.copyRowSessionId),sid,'pane header resolves to the accepted UUID');
   }
   const tr=s.trial,ms=t=>t==null?null:Math.round(t-tr.t0);
   const result={trial:i+1,engine:i%2?'delayed acceptance replay + cold folder guess':'warm acceptance replay',reconnect:i===4,prompt_ms:ms(tr.prompt),row_ms:ms(tr.row),assistant_ms:ms(tr.assistant),engine_accept_ms:accepted-start,engine_delta_after_accept_ms:250,accepted_from_submit_ms:accepted-tr.epoch,row_after_accept_ms:tr.row==null?null:Math.round(tr.row-tr.t0-(accepted-tr.epoch)),delta_to_paint_ms:tr.assistant?Math.round((tr.assistant-tr.t0)-(delta-tr.epoch)):null,hidden_samples:tr.hidden.filter(Boolean).length,selection_ids:[...new Set(tr.selected)],livePolls,heldLists,emptyReconnect,errors,marks:tr.marks};
   results.push(result);console.log(JSON.stringify(result));
   if(!before){assert.ok(result.prompt_ms<=250,'optimistic prompt paints within 250ms');assert.ok(result.row_after_accept_ms<=5000,'usable sidebar row paints within 5s of acceptance');assert.ok(result.assistant_ms>=delta-tr.epoch,'paint follows engine delta');}
   await page.screenshot({path:out+'/'+(before?'before':'after')+'-'+(i+1)+'.png'});
   if(i===9){await page.setViewport({width:390,height:844});await page.screenshot({path:out+'/after-phone.png'});}
   await page.close();
  }
  if(!before){const p95=values=>values.sort((a,b)=>a-b)[Math.ceil(values.length*0.95)-1];assert.ok(p95(results.map(r=>r.delta_to_paint_ms))<=2000,'p95 first delta to actual paint <=2s');assert.ok(results.every(r=>r.hidden_samples===0),'all acceptance trials foreground');}
 }finally{fs.writeFileSync(out+'/'+(before?'before':'after')+'-trials.json',JSON.stringify(results,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
