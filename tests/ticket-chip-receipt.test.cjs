const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const puppeteer=require('../require-puppeteer.js');
const {findChromePath}=require('../puppeteer-browser-config.js');
const app=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
const sid='11111111-2222-4333-8444-555555555555';
const marker='\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed=app.replace(marker, `
  window.receiptTest={selectConversation,activePaneId,fetchConversationEvents,seed(){
    const row={id:'${sid}',session_id:'${sid}',source:'interactive',engine:'claude',display_name:'Review repair',first_message:'Review the proposed repair.'};
    conversationsData=[row];archiveData=[row];archiveLoaded=true;renderArchiveList=()=>{};
  }};
`+marker);

function bridge(result, activeSid=sid) {
  const vm=require('node:vm'), calls=[];
  const start=app.indexOf('  window.cccSendQuestionAnswer =');
  const end=app.indexOf('  window.cccRenderMarkdown =',start);
  const context={window:{},paneByPaneId:()=>({id:'p1',conversationId:activeSid}),activePaneId:()=> 'p1',
    appendPendingSendEcho:(...args)=>{calls.push(['echo',...args]);return {entry:{}};},
    postInjectInput:async(...args)=>{calls.push(['inject',...args]);if(result instanceof Error)throw result;return result;},
    removePendingSendEcho:pending=>calls.push(['remove',!!pending]),
    markPendingSendQueued:()=>calls.push(['queued']),markPendingSendDelivered:()=>calls.push(['delivered']),
    bindPendingSendDeliveryId:()=>{},beginWtReceiptTracking:()=>calls.push(['wt']),
    scheduleFireAndWatchRefresh:()=>{},markSessionSending:()=>{},refreshConversationList:()=>{}};
  vm.runInNewContext(app.slice(start,end),context);
  return {send:context.window.cccSendQuestionAnswer,calls};
}

test('receipt echoes reflect queue, delivery and failure without stealing another conversation',async()=>{
  for(const [result,label] of [[{ok:true,queued:true},'queued'],[{ok:true},'delivered'],[{ok:true,via:'wt-send'},'wt']]){
    const h=bridge(result);await h.send(sid,'Decision receipt',{echo:true});
    assert.equal(h.calls[0][0],'echo');
    assert.equal(h.calls.at(-1)[0],label);
  }
  for(const result of [{ok:false,error:'Offline'},{ok:true,submitted:false},{ok:true,soft_block:true},new Error('Timeout')]){
    const h=bridge(result);
    const sent=await h.send(sid,'Decision receipt',{echo:true}).catch(e=>({ok:false}));
    assert.equal(sent.ok,false);
    assert.deepEqual(h.calls.at(-1),['remove',true]);
    assert.equal(h.calls.some(call=>['queued','delivered','wt'].includes(call[0])),false);
  }
  const other=bridge({ok:true},'other-session');
  await other.send(sid,'Decision receipt',{echo:true});
  assert.equal(other.calls.some(call=>call[0]==='echo'),false);
  assert.equal(other.calls[0][1],sid,'selection changes cannot redirect the receipt');
});

test('a footer answer records the ticket and leaves a durable visible receipt in its originating conversation', {timeout:30000}, async()=>{
  const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  const posts=[];
  let receipt='';
  const footer='DID: The repair is ready.\nNEXT_STEP_USER: Choose for [Q9:repair]: approve the repair or leave it alone.\nNEXT_ACTION: '
    +JSON.stringify([{label:'Approve repair',reply:'[Q9:repair] Yes, approve only the repair.'}]);
  try {
    const page=await browser.newPage();
    await page.setViewport({width:1440,height:900});
    await page.evaluateOnNewDocument(()=>{localStorage.setItem('ccc-conv-list-collapsed','0');localStorage.setItem('ccc-theme','light');});
    await page.setRequestInterception(true);
    page.on('request',r=>{
      const url=new URL(r.url());
      if(r.isNavigationRequest())return r.respond({status:200,contentType:'text/html',body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
      if(url.pathname==='/static/app.js')return r.respond({status:200,contentType:'application/javascript',body:exposed});
      if(url.pathname.startsWith('/static/')&&(url.pathname.endsWith('.css')||url.pathname==='/static/question-tags.js')){
        const file=path.join(__dirname,'..',url.pathname);
        if(fs.existsSync(file))return r.respond({status:200,contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(file,'utf8')});
      }
      let data=[];
      if(url.pathname==='/api/question-tags')data={ok:true,questions:[{n:9,tag:'[Q9:repair]',slug:'repair',answered:false,mentions:[],
        first:{sid,ts:'2026-01-01T00:00:00Z',uuid:'ask',text:'Approve [Q9:repair]?'},ticket:{ref:'OPS-7',open:true,session_id:'worker-session'}}]};
      if(url.pathname==='/api/conversations/'+sid){
        const events=[{type:'user_text',line:1,text:'Review the proposed repair.'},
          {type:'assistant',line:2,blocks:[{kind:'text',text:'<session-state>\n'+footer+'\n</session-state>'}]}];
        if(receipt)events.push({type:'user_text',line:3,text:receipt});
        data={engine:'claude',events,last_line:receipt?3:2,first_line:1,truncated_before:false};
      }
      if(r.method()==='POST'&&['/api/ux-fixes/answer','/api/inject-input'].includes(url.pathname)){
        const payload=JSON.parse(r.postData());posts.push({url:url.pathname,payload});
        data={ok:true};
        if(url.pathname==='/api/inject-input'){
          receipt=payload.text;
          data.delivery_id='receipt-test';
        }
      }
      return r.respond({status:200,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('http://ccc-receipt.test/',{waitUntil:'load'});
    await page.waitForFunction(()=>!!window.receiptTest&&!!window.cccQuestionTags);
    await page.evaluate(async sid=>{receiptTest.seed();await receiptTest.selectConversation(sid);},sid);
    await page.waitForSelector('.session-state-block [data-qtalk]');
    await page.$eval('.conv-input-bar textarea',el=>{el.value='Keep my unfinished draft';el.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.click('.ssb-act-say');
    await page.waitForFunction(()=>[...document.querySelectorAll('.user-msg')].some(el=>el.textContent.startsWith('Decision recorded on OPS-7:')));
    assert.deepEqual(posts.map(p=>p.url),['/api/ux-fixes/answer','/api/inject-input']);
    assert.deepEqual(posts[0].payload,{ref:'OPS-7',text:'[Q9:repair] Yes, approve only the repair.'});
    assert.equal(posts[1].payload.session_id,sid);
    assert.equal(posts[1].payload.source,'composer');
    assert.equal(posts[1].payload.composer_form,'ccc-composer-v1');
    assert.match(posts[1].payload.text,/This answer is already recorded on the ticket/);
    assert.equal(await page.$eval('.conv-input-bar textarea',el=>el.value),'Keep my unfinished draft');
    assert.equal(await page.$eval('.ssb-act-say',el=>el.disabled),true);
    await page.evaluate(()=>document.querySelector('.ssb-act-say').click());
    assert.equal(posts.length,2,'the disabled chip must not repeat either delivery');
    await page.evaluate(()=>receiptTest.fetchConversationEvents(receiptTest.activePaneId()));
    await page.waitForFunction(()=>!!document.querySelector('.event.user_text:not(.pending):not(.send-delivered) .user-msg[data-raw-text^="Decision recorded on OPS-7:"]'));
    assert.equal(await page.$$eval('.user-msg',els=>els.filter(el=>el.textContent.startsWith('Decision recorded on OPS-7:')).length),1,'durable transcript replaces its optimistic echo');
    const visible=await page.$('.user-msg[data-raw-text^="Decision recorded on OPS-7:"]');
    await visible.scrollIntoView();
    assert.equal(await visible.evaluate(el=>{const r=el.getBoundingClientRect();return el.checkVisibility()&&r.top>=0&&r.bottom<=innerHeight;}),true);
    if(process.env.CCC_RECEIPT_SCREENSHOT)await page.screenshot({path:process.env.CCC_RECEIPT_SCREENSHOT});
  } finally {await browser.close();}
});
