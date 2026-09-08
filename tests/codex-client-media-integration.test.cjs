const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
let browser;
test.before(async () => { browser = await puppeteer.launch({headless: true}); });
test.after(async () => { await browser?.close(); });

async function fixture() {
  const page = await browser.newPage();
  await page.setViewport({width:1200,height:950});
  await page.setContent('<div class="conv-pane is-codex-session" style="height:850px;width:1100px"><div class="conv-pane-header"><span class="conv-pane-actions"></span></div><div class="conversations-view"></div></div>');
  await page.addStyleTag({path:path.resolve('static/codex-client.css')});
  await page.evaluate(() => {
    window.__attached = []; window.__mediaEvents = []; window.__disposed = 0;
    window.__mediaActive = false; window.__deferCleanup = false; window.__preferences = 0; window.__eventReads = 0;
    window.CCCCodexClientContext = () => ({threadId:'task',repoPath:'/repo',title:'Task',paneEl:document.querySelector('.conv-pane')});
    window.CCCCodexMedia = {attach(ports) {
      window.__attached.push(ports.context.threadId);
      return {
        isActive: () => window.__mediaActive,
        handleEvents: events => window.__mediaEvents.push(...events),
        dispose() {
          window.__disposed++;
          return window.__deferCleanup ? new Promise(resolve => { window.__resolveCleanup = resolve; }) : Promise.resolve();
        },
      };
    }};
    const methods = ['model/list','account/read'].map(method => ({method,title:method,group:'Account',available:true,read_only:true,params_schema:{type:'object'}}));
    window.fetch = async (url, options={}) => {
      const parsed = new URL(String(url),'http://fixture');
      let value;
      if (parsed.pathname.endsWith('/catalog')) value = {ok:true,methods,experimental_enabled:false};
      else if (parsed.pathname.endsWith('/schema')) value = {ok:true,descriptor:methods.find(m=>m.method===parsed.searchParams.get('method'))};
      else if (parsed.pathname.endsWith('/preferences')) { window.__preferences++; value={ok:true,methods,experimental_enabled:true}; }
      else if (parsed.pathname.endsWith('/operation')) value = {ok:true,generation:'g',result:{data:[]}};
      else if (parsed.pathname.endsWith('/events')) { window.__eventReads++; value={ok:true,generation:'g',cursor:1,connected:true,events:[],requests:[]}; }
      else value = {ok:true,generation:'g',cursor:1,connected:true,thread:{id:parsed.searchParams.get('thread_id')||'task',turns:[]},requests:[],next_cursor:null};
      return {ok:true,json:async()=>value};
    };
  });
  await page.addScriptTag({path:path.resolve('static/codex-client.js')});
  await page.evaluate(()=>window.CCCCodexClient.open());
  return page;
}

test('workspace mounts media and forwards all native event kinds', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      window.CCCCodexClient.handleEvents([{seq:2,method:'process/outputDelta',params:{processHandle:'p',deltaBase64:'aGk='}}]);
      const attached = window.__attached.slice();
      await window.CCCCodexClient.close();
      return {attached,events:window.__mediaEvents.map(e=>e.method),disposed:window.__disposed};
    });
    assert.deepEqual(result,{attached:['task'],events:['process/outputDelta'],disposed:1});
  } finally {await page.close();}
});

test('media activity keeps hidden-page event polling alive', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const state = window.CCCCodexClient.__testing.state;
      clearTimeout(state.pollTimer); state.pollTimer=null;
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
      window.__mediaActive=true;
      const before=window.__eventReads;
      await window.CCCCodexClient.__testing.pollNow();
      const active=window.__eventReads-before;
      window.__mediaActive=false;
      const after=window.__eventReads;
      await window.CCCCodexClient.__testing.pollNow();
      await window.CCCCodexClient.close();
      return {active,inactive:window.__eventReads-after};
    });
    assert.equal(result.active,1); assert.equal(result.inactive,0);
  } finally {await page.close();}
});

test('replacement workspace waits for prior native cleanup', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      window.__deferCleanup=true;
      const opening=window.CCCCodexClient.open({threadId:'next',repoPath:'/repo'});
      await Promise.resolve();
      const before=window.__attached.slice();
      window.__deferCleanup=false;
      if (window.__resolveCleanup) window.__resolveCleanup();
      await opening;
      const after=window.__attached.slice();
      await window.CCCCodexClient.close();
      return {before,after};
    });
    assert.deepEqual(result.before,['task']); assert.deepEqual(result.after,['task','next']);
  } finally {await page.close();}
});

test('preview preference changes wait for native media cleanup', async () => {
  const page = await fixture();
  try {
    await page.evaluate(()=>{window.__mediaActive=true;window.__deferCleanup=true;});
    await page.click('[data-codex-preview]');
    assert.equal(await page.evaluate(()=>window.__preferences),0);
    await page.evaluate(()=>{window.__deferCleanup=false;window.__resolveCleanup?.();});
    await page.waitForFunction(()=>window.__preferences===1&&window.__attached.length===2);
    await page.evaluate(()=>window.CCCCodexClient.close());
  } finally {await page.close();}
});

test('selecting another conversation closes only the matching pane workspace', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const paneEl = document.querySelector('.conv-pane');
      window.dispatchEvent(new CustomEvent('ccc:conversation-selected',{detail:{paneEl:document.body,threadId:'other'}}));
      const unrelatedStillOpen = !window.CCCCodexClient.__testing.state.closed;
      window.dispatchEvent(new CustomEvent('ccc:conversation-selected',{detail:{paneEl,threadId:'task'}}));
      const sameStillOpen = !window.CCCCodexClient.__testing.state.closed;
      window.dispatchEvent(new CustomEvent('ccc:conversation-selected',{detail:{paneEl,threadId:'other'}}));
      return {unrelatedStillOpen,sameStillOpen,closed:window.CCCCodexClient.__testing.state.closed,disposed:window.__disposed};
    });
    assert.deepEqual(result,{unrelatedStillOpen:true,sameStillOpen:true,closed:true,disposed:1});
  } finally {await page.close();}
});

test('uncertain native cleanup blocks replacement controllers', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const state = window.CCCCodexClient.__testing.state;
      state.mediaController.dispose = () => Promise.reject(new Error('Native stop unconfirmed'));
      let first, second;
      try { await window.CCCCodexClient.open({threadId:'next',repoPath:'/repo'}); } catch(e) {first=e.message;}
      try { await window.CCCCodexClient.open({threadId:'next',repoPath:'/repo'}); } catch(e) {second=e.message;}
      return {first,second,attached:window.__attached};
    });
    assert.deepEqual(result,{first:'Native stop unconfirmed',second:'Native stop unconfirmed',attached:['task']});
  } finally {await page.close();}
});

test('history revert remounts media after success and failure', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const api=window.CCCCodexClient.__testing;
      await api.executeAction({method:'thread/revert',read_only:false},{threadId:'task'});
      const successful=window.__attached.length;
      const realFetch=window.fetch;
      window.fetch=async (url, options={})=>String(url).endsWith('/operation')
        ? {ok:false,json:async()=>({ok:false,error:'Revert failed'})}:realFetch(url,options);
      try {await api.executeAction({method:'thread/rollback',read_only:false},{threadId:'task'});} catch(_) {}
      const failed=window.__attached.length;
      await window.CCCCodexClient.close();
      return {successful,failed};
    });
    assert.deepEqual(result,{successful:2,failed:3});
  } finally {await page.close();}
});

test('history actions cannot remount over rejected native cleanup', async () => {
  const page=await fixture();
  try {
    const result=await page.evaluate(async()=>{
      const api=window.CCCCodexClient.__testing;
      api.state.mediaController.dispose=()=>Promise.reject(new Error('Stop unconfirmed'));
      try {await api.executeAction({method:'thread/revert',read_only:false},{threadId:'task'});} catch(_) {}
      return {attached:window.__attached.length,controller:!!api.state.mediaController,blocked:api.state.mediaCleanupBlocked};
    });
    assert.deepEqual(result,{attached:1,controller:false,blocked:true});
  } finally {await page.close();}
});

test('retiring media receives events for its captured task until cleanup settles', async()=>{
  const page=await fixture();
  try {
    await page.evaluate(()=>{
      window.__mediaActive=true;
      window.__deferCleanup=true;
      window.__closing=window.CCCCodexClient.close();
    });
    const before=await page.evaluate(()=>window.__eventReads);
    await page.waitForFunction(count=>window.__eventReads>count,{},before);
    await page.evaluate(()=>window.__resolveCleanup());
    await page.evaluate(()=>window.__closing);
    assert.equal(await page.evaluate(()=>window.CCCCodexClient.__testing.state.mediaCleanupBlocked),false);
  } finally {await page.close();}
});

test('late lifecycle results cannot replace a newly selected workspace', async()=>{
  const page=await fixture();
  try {
    const result=await page.evaluate(async()=>{
      const api=window.CCCCodexClient.__testing;
      const original=window.fetch;
      let resolveAction;
      window.fetch=(url,options={})=>String(url).endsWith('/operation')&&JSON.parse(options.body).method==='thread/start'
        ? new Promise(resolve=>{resolveAction=()=>resolve({ok:true,json:async()=>({ok:true,generation:'old',result:{thread:{id:'created',cwd:'/repo'}}})});})
        : original(url,options);
      const action=api.executeAction({method:'thread/start',read_only:false},{cwd:'/repo'});
      await Promise.resolve(); await Promise.resolve();
      await window.CCCCodexClient.open({threadId:'next',repoPath:'/repo'});
      resolveAction(); await action;
      const selected=api.state.context.threadId, generation=api.state.generation;
      await window.CCCCodexClient.close();
      return {selected,generation};
    });
    assert.deepEqual(result,{selected:'next',generation:'g'});
  } finally {await page.close();}
});

test('late mutation completion cannot release a replacement action lock', async()=>{
  const page=await fixture();
  try {
    const result=await page.evaluate(async()=>{
      const api=window.CCCCodexClient.__testing;
      const original=window.fetch; const pending=[];
      window.fetch=(url,options={})=>String(url).endsWith('/operation')&&JSON.parse(options.body).method==='thread/name/set'
        ? new Promise(resolve=>pending.push(()=>resolve({ok:true,json:async()=>({ok:true,generation:'g',result:{}})})))
        : original(url,options);
      const first=api.runOperation('thread/name/set',{threadId:'task',name:'Name'});
      await Promise.resolve(); await Promise.resolve();
      await window.CCCCodexClient.open({threadId:'task',repoPath:'/repo'});
      const second=api.runOperation('thread/name/set',{threadId:'task',name:'Name'});
      await Promise.resolve(); await Promise.resolve();
      pending[0](); await first;
      const third=await api.runOperation('thread/name/set',{threadId:'task',name:'Name'});
      pending[1](); await second;
      await window.CCCCodexClient.close();
      return {third,count:pending.length};
    });
    assert.deepEqual(result,{third:{skipped:true},count:2});
  } finally {await page.close();}
});
