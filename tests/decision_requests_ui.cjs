// Disposable, fully intercepted browser fixtures. No connection reaches a backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/decision-requests.js'), 'utf8');
const pageHtml = fs.readFileSync(path.join(root, 'static/decision-inbox.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
const id = '00000000-0000-4000-8000-000000000001';
const answerId = '00000000-0000-4000-8000-000000000002';
const base = {request_id:id, revision:4, adapter:'watchtower-block', origin_id:'TASK-1', title:'Choose region', body:'Which region?',
  aliases:['[Q1:region]'], kind:'question', status:'open', needs_answer:true, delivery_state:'none', answer_state:'none',
  observed_at:'2026-01-02T12:00:00Z', episode_provenance:true, can_answer:true, answer:null};
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), headless:true, args:['--no-sandbox']});
  let checks = 0;
  async function fixture(run, {origin='https://decisions.test', noRandomUUID=false} = {}) {
    const page = await browser.newPage();
    if (noRandomUUID) await page.evaluateOnNewDocument(() => Object.defineProperty(crypto, 'randomUUID', {value:undefined, configurable:true}));
    const model = {ok:true, source_current:true, answers_enabled:true, requests:[{...base}], coverage_issues:[], needs_answer_count:1, checked_at:base.observed_at};
    const posts = []; let handle = async req => req.abort('failed'), handleRead = null;
    await page.setRequestInterception(true);
    page.on('request', async req => {
      const url = new URL(req.url());
      if (req.method() === 'POST') { posts.push({url:url.pathname, body:JSON.parse(req.postData())}); return handle(req); }
      if (url.pathname === '/') return req.respond({status:200, contentType:'text/html', body:pageHtml + '<script src="/owner.js"></script>'});
      if (url.pathname === '/owner.js') return req.respond({status:200, contentType:'application/javascript', body:'window.ownerTimers=[]; window.setInterval=fn=>window.ownerTimers.push(fn);\n' + source});
      if (url.pathname === '/api/decision-inbox/requests') {
        const response = {status:200, contentType:'application/json', body:JSON.stringify(model)};
        return handleRead ? handleRead(req, response) : req.respond(response);
      }
      return req.abort('blockedbyclient');
    });
    const ready = () => page.waitForFunction(() => document.querySelector('#requestCount').textContent.includes('need an answer'));
    const load = async () => { await page.goto(origin + '/'); await ready(); };
    const refresh = async () => { await page.click('#requestRefresh'); await page.waitForFunction(() => !document.querySelector('#requestRefresh').disabled); };
    const click = label => page.evaluate(label => [...document.querySelectorAll('#requestRows button')].find(b => b.textContent === label).click(), label);
    const waitText = value => page.waitForFunction(value => document.querySelector('#requestRows').textContent.includes(value), {}, value);
    const reply = (req, state='pending', text='  Region A\nExact.  ') => req.respond({status:200, contentType:'application/json', body:JSON.stringify({ok:true, request_id:id, answer_id:answerId, state, answer:{request_id:id, expected_revision:4, text, actor:'owner'}, work_completed:false})});
    try { await load(); await run({page, model, posts, load, refresh, click, waitText, reply, setHandle:fn => {handle=fn;}, setReadHandle:fn => {handleRead=fn;}}); checks++; }
    finally { await page.close(); }
  }
  try {
    await fixture(async ({page,model,refresh,posts}) => {
      for (const changes of [{answers_enabled:false}, {answers_enabled:'1'}, {requests:[{...base, can_answer:false}]}, {source_current:false},
        {requests:[{...base, adapter:'session-question'}]}, {requests:[{...base, delivery_owner:'routine'}]}, {requests:[{...base, kind:'approval'}]}]) {
        Object.assign(model, {answers_enabled:true,source_current:true,requests:[{...base}]}, changes); await refresh();
        assert.equal(await page.$('#requestRows textarea'),null); assert.equal(await page.$('#requestRows button'),null);
      }
      await page.evaluate(() => window.ownerTimers.forEach(fn=>fn()));
      await page.waitForFunction(() => !document.querySelector('#requestRefresh').disabled);
      assert.equal(posts.length,0);
      assert.match(await page.$eval('#requestRows',el=>el.textContent), /Episode initialized/);
      assert.match(await page.$eval('#requestRows time',el=>el.textContent), /PST|PDT/);
    });
    await fixture(async ({page,model,refresh,posts}) => {
      model.answers_enabled=false;
      model.requests=[{...base,origin_id:'TASK SPACE/1',aliases:['[Q1:region]','D-12']}]; await refresh();
      assert.deepEqual(await page.$eval('.request-origin',el=>[el.textContent,el.getAttribute('href'),el.target,el.title,el.parentElement.firstElementChild===el]),
        ['TASK SPACE/1','/?ticket=TASK%20SPACE%2F1','_top','Open ticket TASK SPACE/1',true]);
      assert.match(await page.$eval('#requestRows',el=>el.textContent), /\[Q1:region\] · D-12/);
      assert.equal(await page.$('#requestRows textarea'),null);
      // Observe the handler without allowing navigation in this fixture.
      assert.equal(await page.$eval('.request-origin',el=>{
        let prevented; el.addEventListener('click',event=>{prevented=event.defaultPrevented;event.preventDefault();},{once:true});
        el.dispatchEvent(new MouseEvent('click',{button:0,bubbles:true,cancelable:true}));return prevented;
      }),false);
      model.requests=[{...base,adapter:'session-question'}]; await refresh(); assert.equal(await page.$('.request-origin'),null);
      assert.equal(posts.length,0);
    });
    await fixture(async ({page,posts}) => {
      await page.evaluate(() => {
        window.ticketMessages=[];
        window.addEventListener('message',event=>{if(event.data?.type==='ccc:open-ticket') window.ticketMessages.push({data:event.data,origin:event.origin});});
        const frame=document.createElement('iframe');frame.src='/';document.body.append(frame);
      });
      await page.waitForFunction(() => document.querySelector('iframe').contentDocument.querySelector('.request-origin'));
      const frame=page.frames().find(frame=>frame!==page.mainFrame());
      await frame.click('.request-origin');
      await page.waitForFunction(() => window.ticketMessages.length===1);
      assert.deepEqual(await page.evaluate(()=>window.ticketMessages),[{data:{type:'ccc:open-ticket',ref:'TASK-1'},origin:'https://decisions.test'}]);
      for (const modifier of ['ctrlKey','metaKey','shiftKey','altKey']) {
        assert.equal(await frame.$eval('.request-origin',(el,modifier)=>{
          let prevented;el.addEventListener('click',event=>{prevented=event.defaultPrevented;event.preventDefault();},{once:true});
          el.dispatchEvent(new MouseEvent('click',{button:0,bubbles:true,cancelable:true,[modifier]:true}));return prevented;
        },modifier),false);
      }
      assert.equal(await page.evaluate(()=>window.ticketMessages.length),1); assert.equal(posts.length,0);
      await page.screenshot({path:'/tmp/ccc-owner-origin-fixture.png',fullPage:true});
    });
    await fixture(async ({page,refresh}) => {
      await page.type('textarea','draft unchanged');
      await page.$eval('textarea',el => {el.focus();el.setSelectionRange(3,7);});
      // Simulate periodic refresh without moving focus to the refresh button.
      await page.evaluate(() => document.querySelector('#requestRefresh').click());
      await page.waitForFunction(() => !document.querySelector('#requestRefresh').disabled);
      assert.deepEqual(await page.$eval('textarea',el=>[el.value,document.activeElement===el,el.selectionStart,el.selectionEnd]), ['draft unchanged',true,3,7]);
    });
    await fixture(async ({page,posts,load,click,waitText,setHandle,reply,model,refresh}) => {
      const exact='  Region A\nExact.  ';
      await page.type('textarea',exact); await click('Save answer'); await waitText('Save not confirmed');
      assert.equal(posts.length,1); assert.equal(posts[0].body.text,exact);
      assert.deepEqual(Object.keys(posts[0].body).sort(), ['expected_revision','idempotency_key','request_id','text']);
      await load(); assert.equal(await page.$('textarea'),null); await waitText('Retry saving same answer');
      setHandle(req => reply(req)); await click('Retry saving same answer'); await waitText('Send saved answer');
      assert.deepEqual(posts[1],posts[0]); assert.equal(posts.length,2);
      await page.screenshot({path:'/tmp/ccc-owner-answer-fixture.png',fullPage:true});
      assert.equal(await page.$eval('.request-frozen',el=>el.textContent),exact);
      // Save remains visible in default Needs answer, even after source refresh.
      model.requests=[{...base, needs_answer:false, answer:{answer_id:answerId,state:'pending',text:exact,expected_revision:4}}];
      await refresh(); await waitText('Send saved answer');
      await page.evaluate(() => window.ownerTimers.forEach(fn=>fn()));
      await page.waitForFunction(() => !document.querySelector('#requestRefresh').disabled);
      assert.equal(posts.length,2);
      setHandle(req => req.abort('failed')); await click('Send saved answer'); await waitText('Check delivery');
      await load(); await waitText('Check delivery'); assert.equal(posts.length,3);
      setHandle(req => reply(req,'accepted')); await click('Check delivery'); await waitText('Acceptance does not mean the work is complete.');
      assert.equal(posts[3].url,'/api/decision-inbox/answers/reconcile');
      assert.deepEqual(posts[3].body,{answer_id:answerId});
    });
    await fixture(async ({page,posts,load,click,waitText,model}) => {
      await page.type('textarea','recovered'); await click('Save answer'); await waitText('Save not confirmed');
      model.requests=[{...base,needs_answer:false,answer:{answer_id:answerId,state:'pending',text:'recovered',expected_revision:4}}];
      await load(); await waitText('Send saved answer'); assert.equal(posts.length,1);
      assert.equal(await page.$('textarea'),null);
    });
    await fixture(async ({page,posts,click,waitText,setHandle,reply}) => {
      let release; setHandle(req => new Promise(resolve => {release=()=>reply(req,'rejected','kept').then(resolve);}));
      await page.type('textarea','kept');
      await page.evaluate(() => {const form=document.querySelector('.request-answer');form.requestSubmit();form.requestSubmit();});
      await page.waitForFunction(() => !!document.querySelector('#requestRows button:disabled'));
      assert.equal(posts.length,1); await release(); await waitText('Answer definitively rejected');
      assert.equal(await page.$eval('.request-frozen',el=>el.textContent),'kept'); assert.equal(await page.$('textarea'),null);
    });
    await fixture(async ({page,posts,click,waitText,setHandle,load}) => {
      setHandle(req => req.respond({status:409,contentType:'application/json',body:'{"ok":false,"error":"answer_conflict"}'}));
      await page.type('textarea','conflicted exact text'); await click('Save answer'); await waitText('answer_conflict');
      await load(); await waitText('source conflict'); assert.equal(await page.$eval('.request-frozen',el=>el.textContent),'conflicted exact text');
      await click('Retry saving same answer'); await waitText('answer_conflict'); assert.deepEqual(posts[0],posts[1]);
    });
    await fixture(async ({page,model,refresh,posts,click,waitText,setHandle,reply}) => {
      model.source_current=false; model.requests=[{...base,can_answer:false,needs_answer:false,answer:{answer_id:answerId,state:'unknown',text:'saved',expected_revision:4}}];
      await refresh(); await page.select('#requestFilter','delivery'); await waitText('Check delivery');
      setHandle(req=>reply(req,'accepted','saved')); await click('Check delivery');
      await page.select('#requestFilter','all'); await waitText('Acceptance does not mean'); assert.equal(posts.length,1);
    });
    await fixture(async ({page,model,refresh,posts,click,waitText,setHandle,reply}) => {
      model.source_current=false; model.requests=[{...base,can_answer:false,needs_answer:false,answer:{answer_id:answerId,state:'pending',text:'saved',expected_revision:4}}];
      await refresh(); await page.select('#requestFilter','delivery'); await waitText('Send saved answer');
      setHandle(req=>reply(req,'rejected','saved')); await click('Send saved answer');
      await page.select('#requestFilter','all'); await waitText('Answer definitively rejected');
      assert.deepEqual(posts[0],{url:'/api/decision-inbox/answers/send',body:{answer_id:answerId}});
    });
    await fixture(async ({page,posts,load}) => {
      await page.evaluate(() => sessionStorage.setItem('ccc:decision-answer:v1:bad','broken json'));
      await load(); assert.equal(await page.$eval('.request-answer button',el=>el.disabled),true); assert.equal(posts.length,0);
    });
    await fixture(async ({page,model,posts,refresh,load,click,waitText,setHandle,reply}) => {
      await page.type('textarea','old exact rejection');
      setHandle(req=>reply(req,'pending','old exact rejection')); await click('Save answer'); await waitText('Send saved answer');
      setHandle(req=>reply(req,'rejected','old exact rejection')); await click('Send saved answer'); await waitText('Answer definitively rejected');
      assert.equal(await page.$('textarea'),null);
      assert.equal(await page.$eval('.request-frozen',el=>el.textContent),'old exact rejection');
      const oldKey=posts[0].body.idempotency_key;
      const rejected={answer_id:answerId,state:'rejected',text:'old exact rejection',expected_revision:4};
      model.requests=[{...base,revision:5,answer:rejected}]; model.source_current=false;
      await refresh();
      assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#requestRows button')].some(el=>el.textContent==='Start new answer')),false);
      model.source_current=true; model.requests[0].can_answer=false; await refresh();
      assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#requestRows button')].some(el=>el.textContent==='Start new answer')),false);
      model.requests[0].can_answer=true; await refresh();
      await page.evaluate(()=>sessionStorage.setItem('unrelated-sentinel','keep'));
      await click('Start new answer'); await page.waitForSelector('textarea');
      assert.equal(await page.$eval('textarea',el=>el.value),'');
      assert.equal(await page.$eval('.request-rejected-history',el=>el.textContent),'old exact rejection');
      assert.equal(await page.evaluate(()=>sessionStorage.getItem('ccc:decision-answer:v1:00000000-0000-4000-8000-000000000001')),null);
      assert.equal(await page.evaluate(()=>sessionStorage.getItem('unrelated-sentinel')),'keep');
      await refresh(); await page.waitForSelector('textarea');
      setHandle(req=>req.abort('failed')); await page.type('textarea','new exact answer'); await click('Save answer'); await waitText('Save not confirmed');
      assert.notEqual(posts[2].body.idempotency_key,oldKey); assert.equal(posts[2].body.expected_revision,5);
      assert.equal(posts[2].body.text,'new exact answer');
      await load(); await waitText('Retry saving same answer');
      assert.equal(await page.$eval('.request-rejected-history',el=>el.textContent),'old exact rejection');
      await click('Retry saving same answer'); await waitText('Save not confirmed'); assert.deepEqual(posts[2],posts[3]);
    });
    await fixture(async ({page,posts,click,waitText}) => {
      assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
      assert.equal(await page.evaluate(()=>window.isSecureContext),false);
      await page.type('textarea','remote HTTP answer'); await click('Save answer'); await waitText('Save not confirmed');
      assert.match(posts[0].body.idempotency_key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      await click('Retry saving same answer'); await waitText('Save not confirmed'); assert.deepEqual(posts[0],posts[1]);
    }, {origin:'http://decisions.test',noRandomUUID:true});
    for (const terminal of ['accepted','rejected']) {
      await fixture(async ({page,model,posts,refresh,load,click,waitText,setHandle,setReadHandle,reply}) => {
        await page.type('textarea','terminal exact answer');
        setHandle(req=>reply(req,'pending','terminal exact answer')); await click('Save answer'); await waitText('Send saved answer');
        model.requests=[{...base,needs_answer:false,answer:{answer_id:answerId,state:'pending',text:'terminal exact answer',expected_revision:4}}];
        let release; const readStarted=new Promise(resolve=>setReadHandle((req,response)=>{
          release=()=>req.respond(response); resolve();
        }));
        // Start the stale read before sending, then release it after the receipt.
        await page.evaluate(()=>document.querySelector('#requestRefresh').click()); await readStarted;
        setHandle(req=>reply(req,terminal,'terminal exact answer')); await click('Send saved answer');
        await waitText(terminal==='accepted'?'Answer accepted by source':'Answer definitively rejected');
        await release(); await page.waitForFunction(()=>!document.querySelector('#requestRefresh').disabled);
        const assertTerminal=async()=>{
          assert.equal(await page.$eval('.request-state',el=>el.textContent),terminal==='accepted'?'Answer accepted by source':'Answer rejected by source');
          assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#requestRows button')].some(el=>el.textContent==='Send saved answer')),false);
        };
        await assertTerminal(); setReadHandle(null); await refresh(); await assertTerminal();
        await load(); await assertTerminal();
        model.requests[0].answer.answer_id='00000000-0000-4000-8000-000000000003';
        model.requests[0].answer.state='accepted'; model.requests[0].can_answer=true;
        await refresh(); await assertTerminal(); await load(); await assertTerminal();
        assert.equal(posts.length,2);
      });
    }
    console.log(`PASS ${checks} intercepted owner-answer UI scenarios`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
