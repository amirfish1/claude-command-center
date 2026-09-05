const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app=fs.readFileSync('static/app.js','utf8');
const helpers=app.slice(app.indexOf('  function queuedSteerCardCount('),app.indexOf('  // ── Conversation presentation modes',app.indexOf('  function queuedSteerCardCount(')));
const css=fs.readFileSync('static/app.css','utf8');
let browser;
test.before(async()=>{browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();});
async function fixture(width=1100,mode='status-pos-right') {
 const page=await browser.newPage();await page.setViewport({width,height:800});
 await page.setContent('<body class="'+mode+'"><div class="conv-pane has-status-rail is-webui-session" data-pane-id="left" style="height:760px;width:100%"><div class="conv-pane-header">Conversation</div><div class="conversations-view"></div><div class="conv-input-bar visible" style="height:120px"><textarea>Unsent draft</textarea></div><div class="status-rail"></div></div></body>');
 await page.addStyleTag({content:css+'\nbody{margin:0;display:block;} .conversations-view{display:block!important;}'});
 await page.evaluate(helpers=>{
  window.state={conversationId:'session-one'};
  window.paneByPaneId=()=>state;
  window.getConvInputBarForPane=()=>document.querySelector('.conv-input-bar');
  window.getConvViewForPane=window.getConvView=()=>document.querySelector('.conversations-view');
  window.activePaneId=()=> 'left';
  window.conversationsData=[{id:'session-one',session_id:'session-one'}];
  window._normSend=t=>String(t||'').replace(/\s+/g,' ').trim();
  window.scrollConversationToEnd=()=>{};
  window.isPendingSendEchoElement=el=>['pending','send-queued','send-delivered','not-acknowledged'].some(c=>el.classList.contains(c));
  window.removedPending=[];
  window.removePendingSendEcho=p=>{removedPending.push(p);p.element.remove();};
  window.markPendingSendQueued=(p,label,opts)=>{p.element.classList.remove('pending');p.element.classList.add('send-queued');p.entry.queued=true;};
  (0,eval)(helpers);
  window.makeRow=(text,kind='server',time=Date.now())=>{
   const row=document.createElement('div');row.className='event user_text '+(kind==='server'?'pending server-queued':kind==='local'?'pending':'');
   row.dataset.tsEpoch=String(time);
   if(kind==='server') row.dataset.queuedSteerServer='true';
   const msg=document.createElement('div');msg.className='user-msg';msg.dataset.rawText=text;msg.textContent=text;row.append(msg);
   if(kind==='local') row._pendingRef={element:row,entry:{ts:time},sid:'session-one'};
   getConvView().append(row);return row;
  };
  window.sync=()=>syncQueuedSteerTray(getConvView(),'left',false);
 },helpers);
 return page;
}
test('queue occupies its own area above composer and actions never cover long text',async()=>{
 for(const [width,mode] of [[1100,'status-pos-right'],[1100,'status-pos-right status-rail-collapsed'],[390,'status-pos-right'],[760,'']]){
  const page=await fixture(width,mode);try{
   await page.evaluate(()=>{for(let i=0;i<3;i++)makeRow('Message '+i+': '+ 'Long queued steering text with enough room to read. '.repeat(5));sync();});
   const result=await page.evaluate(()=>{
    const tray=document.querySelector('.queued-steer-tray'),input=document.querySelector('.conv-input-bar'),view=getConvView();
    const rect=el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};
    return {sibling:tray.parentNode===input.parentNode,next:tray.nextElementSibling===input, tray:rect(tray),input:rect(input),view:rect(view),draft:input.querySelector('textarea').value,
     rows:[...tray.querySelectorAll('.event')].map(row=>({text:rect(row.querySelector('.user-msg')),actions:row.querySelector('.queued-steer-actions')&&rect(row.querySelector('.queued-steer-actions')),positions:[...row.querySelectorAll('button')].map(b=>getComputedStyle(b).position)}))};
   });
   assert.equal(result.sibling,true);assert.equal(result.next,true);assert.ok(result.tray.bottom<=result.input.top+1);assert.ok(result.tray.top>=result.view.bottom-1);
   assert.equal(result.draft,'Unsent draft');
   for(const row of result.rows){assert.ok(row.actions);assert.ok(row.actions.top>=row.text.bottom-1);assert.ok(row.text.right-row.text.left>=result.tray.right-result.tray.left-48);assert.ok(row.positions.every(p=>p!=='absolute'));}
  }finally{await page.close();}
 }
});
test('local and server queue occurrences merge without discarding pending state or repeated sends',async()=>{
 const page=await fixture();try{
  const out=await page.evaluate(()=>{const first=makeRow('same text','local');const second=makeRow('same text','local');makeRow('same text');makeRow('same text');sync();return {count:queuedSteerCardCount(document.querySelector('.queued-steer-tray')),remaining:getConvView().querySelectorAll('.event').length,preserved:first.isConnected&&second.isConnected,removed:removedPending.length};});
  assert.deepEqual(out,{count:2,remaining:0,preserved:true,removed:0});
 }finally{await page.close();}
});
test('an older identical transcript message is history, not evidence a new queued message drained',async()=>{
 const page=await fixture();try{
  const out=await page.evaluate(()=>{const history=makeRow('again','durable',Date.now()-60000);makeRow('again','server');sync();return {queued:queuedSteerCardCount(document.querySelector('.queued-steer-tray')),history:history.isConnected&&!history.classList.contains('is-queued-steer-duplicate')};});
  assert.deepEqual(out,{queued:1,history:true});
 }finally{await page.close();}
});
test('optimistic steering hides tray actions and failure restores them above the composer',async()=>{
 const page=await fixture();try{
  const out=await page.evaluate(()=>{makeRow('follow up');sync();const row=document.querySelector('.queued-steer-tray .event');const moved=beginOptimisticSteerMove([row],getConvView());const hidden=row.querySelector('.queued-steer-actions')?.hidden;revertOptimisticSteerMove(moved,'left');return {hidden,restored:row.closest('.queued-steer-tray')?.nextElementSibling===getConvInputBarForPane(),shown:row.querySelector('.queued-steer-actions')?.hidden===false};});
  assert.deepEqual(out,{hidden:true,restored:true,shown:true});
 }finally{await page.close();}
});
test('queue sync removes stale Sending feedback but preserves Thinking feedback',async()=>{
 const page=await fixture();try{
  const out=await page.evaluate(()=>{getConvView().insertAdjacentHTML('beforeend','<div class="conv-live-tool-inline optimistic">Sending</div><div class="conv-live-tool-inline optimistic is-thinking">Thinking</div>');makeRow('queued');sync();return {sending:!!getConvView().querySelector('.optimistic:not(.is-thinking)'),thinking:!!getConvView().querySelector('.optimistic.is-thinking')};});
  assert.deepEqual(out,{sending:false,thinking:true});
 }finally{await page.close();}
});
test('a repeated queue acknowledgement still renders only one set of controls per card',async()=>{
 const page=await fixture();try{
  const out=await page.evaluate(()=>{const row=makeRow('queued','local');makeRow('queued');sync();row.insertAdjacentHTML('beforeend','<div class="send-queued-note"><button data-copy-user-message>Copy</button><button data-cancel-queued-message>Cancel</button><button data-steer-queued-message>Steer</button></div>');sync();return {buttons:row.querySelectorAll('button').length,actions:row.querySelector('.queued-steer-actions').querySelectorAll('button').length};});
  assert.deepEqual(out,{buttons:3,actions:3});
 }finally{await page.close();}
});
test('one later durable occurrence retires only one stale queued copy',async()=>{
 const page=await fixture();try{
  const count=await page.evaluate(()=>{const now=Date.now();makeRow('again','server',now-10000);makeRow('again','server',now-5000);makeRow('again','durable',now);sync();return queuedSteerCardCount(document.querySelector('.queued-steer-tray'));});
  assert.equal(count,1);
 }finally{await page.close();}
});
