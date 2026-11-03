// Fully intercepted disposable Puppeteer fixtures: no request reaches a backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const assets = Object.fromEntries(['html','js','css'].map(ext => [ext, fs.readFileSync(path.join(root, `static/decision-guide.${ext}`), 'utf8')]));
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), 'ccc-decision-guide-'));
const id = '00000000-0000-4000-8000-000000000001';
const secondId = '00000000-0000-4000-8000-000000000002';
const answerId = '00000000-0000-4000-8000-000000000003';
const otherAnswerId = '00000000-0000-4000-8000-000000000004';
const exact = '  Region A\nKeep this exact spacing.  ';
const base = {request_id:id, revision:4, adapter:'watchtower-block', origin_id:'TASK-1', title:'Choose a deployment region',
  body:'[Q1:region] Which region should we use?\n\n  A: West\n  B: East  ', aliases:['[Q1:region]','D-12'], kind:'question',
  status:'open', needs_answer:true, delivery_state:'none', answer_state:'none', observed_at:'2026-01-02T12:00:00Z',
  episode_provenance:true, can_answer:true, answer:null};
const second = {...base, request_id:secondId, origin_id:'TASK-2', title:'Choose a release window', body:'What time should the release begin?'};
const summary = (state, text=exact, aid=answerId) => ({answer_id:aid, state, text, expected_revision:4});
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
  let checks = 0;
  async function fixture(name, run, {before, origin='https://decisions.test'} = {}) {
    const page = await browser.newPage();
    await page.setViewport({width:364,height:860,deviceScaleFactor:1});
    if (before) await page.evaluateOnNewDocument(before);
    const model = {ok:true, answers_enabled:true, source_current:true, needs_answer_count:2, requests:[{...base},{...second}], coverage_issues:[], checked_at:base.observed_at};
    const posts = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let handler = req => req.abort('failed'), readHandler = null;
    await page.setRequestInterception(true);
    page.on('request', async req => {
      const url = new URL(req.url());
      try {
        if (req.method() === 'POST') {
          assert.equal(url.origin,origin);
          assert.match(url.pathname,/^\/api\/decision-inbox\/answers(?:\/(send|reconcile))?$/);
          posts.push({url:url.pathname, body:JSON.parse(req.postData())});
          return await handler(req);
        }
        if (['/','/decision-guide.html','/static/decision-guide.html'].includes(url.pathname)) return await req.respond({status:200,contentType:'text/html',body:assets.html});
        if (url.pathname === '/static/decision-guide.js') return await req.respond({status:200,contentType:'application/javascript',body:assets.js});
        if (url.pathname === '/static/decision-guide.css') return await req.respond({status:200,contentType:'text/css',body:assets.css});
        if (url.pathname === '/api/decision-inbox/requests') {
          const response = {status:200,contentType:'application/json',body:JSON.stringify(model)};
          return await (readHandler ? readHandler(req,response) : req.respond(response));
        }
        return await req.abort('blockedbyclient');
      } catch (error) { errors.push(error.message); if (!req.isInterceptResolutionHandled()) await req.abort(); }
    });
    const ready = () => page.waitForFunction(() => !document.querySelector('#refresh').disabled && !document.querySelector('#intro').textContent.startsWith('Loading'));
    const load = async () => { await page.goto(origin+'/static/decision-guide.html'); await ready(); };
    const refresh = async () => { await page.click('#refresh'); await ready(); };
    const click = label => page.evaluate(label => {
      const button = [...document.querySelectorAll('button')].find(b=>b.textContent===label);
      if (!button || button.disabled) throw Error('Button unavailable: '+label);
      button.click();
    },label);
    const wait = value => page.waitForFunction(value=>document.body.textContent.includes(value),{},value);
    const idle = () => page.waitForFunction(()=>document.querySelector('#composer').getAttribute('aria-busy') !== 'true');
    const selected = () => page.$eval('#current article', el=>el.dataset.requestId);
    const reply = (req,state='pending',changes={}) => req.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,request_id:id,answer_id:answerId,state,answer:{request_id:id,expected_revision:4,text:exact,actor:'owner'},work_completed:false,...changes})});
    try {
      await load();
      await run({page,model,posts,load,refresh,click,wait,idle,selected,reply,setHandler:fn=>{handler=fn;},setReadHandler:fn=>{readHandler=fn;}});
      assert.deepEqual(errors,[]); checks++; console.log('PASS '+name);
    } finally { await page.close(); }
  }
  try {
    await fixture('raw source, read-only queue, ticket navigation and narrow layouts',async ({page,model,refresh,posts})=>{
      assert.equal(await page.$eval('#intro',el=>el.textContent),'Hi, you have 2 open questions. Here is the first one.');
      assert.equal(await page.$eval('#current .raw',el=>el.textContent),base.body);
      assert.match(await page.$eval('#current time',el=>el.textContent),/Jan 2, 2026.*04:00:00 AM PST/);
      assert.deepEqual(await page.$eval('#current .ticket',el=>[el.textContent,el.getAttribute('href'),el.target]),['TASK-1','/?ticket=TASK-1','_top']);
      for (const width of [320,364,390]) {
        await page.setViewport({width,height:860});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        assert.equal(await page.$eval('textarea',el=>el.getBoundingClientRect().right<=innerWidth),true);
        await page.screenshot({path:path.join(screenshots,`guide-${width}.png`),fullPage:true});
      }
      model.answers_enabled=false;
      model.requests[1]={...second,adapter:'session-question',can_answer:false,answer_unavailable_reason:'Reply in the originating session.'};
      await refresh(); await page.click('#queue summary');
      assert.equal(await page.$eval('#answerFields',el=>el.getBoundingClientRect().height),0);
      assert.equal(await page.$eval('#send',el=>el.getBoundingClientRect().height),0);
      assert.equal(await page.$eval('#skip',el=>el.disabled),false);
      await page.screenshot({path:path.join(screenshots,'guide-read-only-390.png'),fullPage:true});
      assert.equal(await page.$$eval('#items button',nodes=>nodes.length),2);
      await page.click('#items button:nth-child(2)');
      assert.match(await page.$eval('#guidance',el=>el.textContent),/not enabled/);
      model.answers_enabled=true; await refresh();
      assert.match(await page.$eval('#guidance',el=>el.textContent),/originating session/); assert.equal(posts.length,0);
      assert.equal(await page.$eval('#answerFields',el=>el.getBoundingClientRect().height),0);
    });
    await fixture('accepted receipt advances and retains exact transcript',async ({page,posts,click,wait,idle,selected,reply,setHandler,model,refresh,load})=>{
      setHandler(async req=>{
        const stored=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('ccc:decision-guide:v1')));
        assert.equal(stored.entries[id].payload.text,exact);
        assert.equal(stored.entries[id].payload.expected_revision,4);
        if (req.url().endsWith('/send')) {
          assert.equal(stored.entries[id].saved.state,'unknown');
          assert.equal(stored.entries[id].deliveryUnknown,true);
        } else assert.equal(stored.entries[id].payload.idempotency_key,JSON.parse(req.postData()).idempotency_key);
        return reply(req,req.url().endsWith('/send')?'accepted':'pending');
      });
      await page.type('textarea',exact); await click('Send answer'); await wait('Answer accepted by source.'); await idle();
      assert.equal(await selected(),secondId); assert.equal(posts.length,2);
      assert.equal(await page.$eval('#current h2',el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),true);
      assert.deepEqual(Object.keys(posts[0].body).sort(),['expected_revision','idempotency_key','request_id','text']);
      assert.equal(posts[0].body.text,exact); assert.equal(posts[0].body.expected_revision,4);
      assert.deepEqual(posts[1].body,{answer_id:answerId});
      assert.equal(await page.$eval('#transcript .raw',el=>el.textContent),base.body);
      assert.equal(await page.$eval('#transcript .reply span:last-child',el=>el.textContent),exact);
      await page.screenshot({path:path.join(screenshots,'guide-accepted.png'),fullPage:true});
      model.requests[0].answer=summary('pending'); await refresh(); await load();
      assert.match(await page.$eval('#transcript',el=>el.textContent),/Answer accepted by source/); assert.equal(posts.length,2);
    });
    for (const result of ['rejected','unknown']) await fixture(result+' never advances',async ({page,posts,click,wait,idle,selected,reply,setHandler})=>{
      setHandler(req=>reply(req,req.url().endsWith('/send')?result:'pending'));
      await page.type('textarea',exact); await click('Send answer'); await idle();
      await wait(result==='rejected'?'Answer rejected by source':'Check delivery');
      assert.equal(await selected(),id); assert.equal(posts.length,2);
      assert.equal(await page.$eval('#answer',el=>el.value),exact);
      assert.equal(await page.$eval('#answer',el=>el.disabled),true);
      assert.equal(await page.$$eval('#recovery button',nodes=>nodes.some(el=>el.textContent==='Send saved answer')),false);
      if (result==='unknown') await page.screenshot({path:path.join(screenshots,'guide-unknown.png'),fullPage:true});
    });
    await fixture('lost save survives reload and retries only the same key without sending',async ({page,posts,click,wait,idle,load,setHandler,reply})=>{
      await page.type('textarea',exact); await click('Send answer'); await wait('Save not confirmed'); await idle();
      const frozen=posts[0]; await load(); assert.equal(posts.length,1);
      setHandler(req=>reply(req)); await click('Retry saving same answer'); await idle(); await wait('Send saved answer');
      assert.equal(posts.length,2); assert.deepEqual(posts[1],frozen);
      await load(); assert.equal(posts.length,2); await wait('Send saved answer');
      setHandler(req=>reply(req,'accepted')); await click('Send saved answer'); await idle();
      assert.equal(posts[2].url,'/api/decision-inbox/answers/send'); assert.equal(posts.length,3);
    });
    await fixture('lost send reload only checks delivery; absence needs another explicit send',async ({page,posts,click,wait,idle,load,setHandler,reply,model,refresh,selected})=>{
      setHandler(req=>req.url().endsWith('/send')?req.abort('failed'):reply(req));
      await page.type('textarea',exact); await click('Send answer'); await idle(); await wait('Check delivery');
      model.requests[0].answer=summary('pending'); await load(); await refresh(); assert.equal(posts.length,2);
      assert.equal(await page.$$eval('#recovery button',nodes=>nodes.some(el=>el.textContent==='Send saved answer')),false);
      setHandler(req=>reply(req)); await click('Check delivery'); await idle(); await wait('Send saved answer');
      assert.equal(await selected(),id); assert.equal(posts.length,3); assert.equal(posts[2].url,'/api/decision-inbox/answers/reconcile');
      await load(); assert.equal(posts.length,3);
      setHandler(req=>reply(req,'accepted')); await click('Send saved answer'); await idle();
      assert.equal(await selected(),secondId); assert.deepEqual(posts[3].body,{answer_id:answerId});
    });
    await fixture('lost save recovered by GET never sends automatically',async ({page,model,posts,click,idle,load,wait})=>{
      await page.type('textarea',exact); await click('Send answer'); await idle();
      model.requests[0].answer=summary('pending');
      await load(); await wait('Send saved answer');
      assert.equal(posts.length,1);
      assert.equal(await page.$eval('textarea',el=>el.disabled),true);
      assert.equal(await page.$eval('#current .reply span:last-child',el=>el.textContent),exact);
    });
    await fixture('strict boolean gates and unsupported source never submit',async ({page,model,refresh,posts})=>{
      for (const change of [{answers_enabled:false},{answers_enabled:'true'},{answers_enabled:1},{source_current:false},{source_current:'true'},
        {requests:[{...base,can_answer:false,answer_unavailable_reason:'Owned elsewhere.'}]},{requests:[{...base,can_answer:'true'}]},
        {requests:[{...base,delivery_owner:'routine'}]},{requests:[{...base,adapter:'session-question'}]}, {requests:[{...base,kind:'approval'}]}]) {
        Object.assign(model,{answers_enabled:true,source_current:true,requests:[{...base}]},change); await refresh();
        assert.equal(await page.$eval('#answer',el=>el.disabled),true);
        assert.equal(await page.$eval('#send',el=>el.disabled),true);
        await page.$eval('#composer',el=>el.requestSubmit());
      }
      assert.equal(posts.length,0);
    });
    await fixture('recorded pending recovery can send despite new-answer can_answer false',async ({page,model,refresh,posts,load,click,idle,setHandler,reply})=>{
      model.requests=[{...base,can_answer:false,needs_answer:false,answer_state:'recorded',answer:summary('pending')}];
      await refresh();
      assert.equal(await page.$eval('#recovery button',el=>el.disabled),false);
      await load(); assert.equal(posts.length,0);
      setHandler(req=>reply(req,'accepted')); await click('Send saved answer'); await idle();
      assert.equal(posts.length,1); assert.equal(posts[0].url,'/api/decision-inbox/answers/send');
    });
    await fixture('lost-record same-key replay survives unavailable and withdrawn source, without sending',async ({page,model,posts,click,idle,load,refresh,setHandler,reply,wait})=>{
      await page.type('textarea',exact); await click('Send answer'); await idle();
      model.source_current=false; model.requests=[{...base,status:'withdrawn',revision:5,can_answer:false}];
      await refresh(); await load(); await wait('Retry saving same answer');
      assert.equal(await page.$eval('#recovery button',el=>el.disabled),false);
      setHandler(req=>reply(req)); await click('Retry saving same answer'); await idle();
      assert.deepEqual(posts[0],posts[1]); assert.equal(posts.length,2);
      assert.equal(await page.$eval('#recovery button',el=>el.disabled),true);
    });
    await fixture('revision pin persists; editing does not rebind; explicit review does',async ({page,model,refresh,posts,click,wait,idle,load})=>{
      await page.type('textarea','draft for revision four');
      await page.$eval('textarea',el=>{el.focus();el.setSelectionRange(3,7);});
      await page.evaluate(()=>document.querySelector('#refresh').click()); await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
      assert.deepEqual(await page.$eval('textarea',el=>[el.value,document.activeElement===el,el.selectionStart,el.selectionEnd]),['draft for revision four',true,3,7]);
      model.requests[0]={...base,revision:5,body:'Updated exact source question\n  Preserve me.  '}; await refresh();
      await page.$eval('textarea',el=>el.setSelectionRange(el.value.length,el.value.length));
      await page.type('textarea',' plus edit'); assert.equal(await page.$eval('#send',el=>el.disabled),true);
      await page.$eval('#composer',el=>el.requestSubmit()); assert.equal(posts.length,0);
      await load(); await wait('Use draft for current question');
      assert.equal(await page.$eval('textarea',el=>el.value),'draft for revision four plus edit');
      assert.equal(await page.$eval('#send',el=>el.disabled),true);
      await click('Use draft for current question'); await click('Send answer'); await idle(); await wait('Save not confirmed');
      assert.equal(posts[0].body.expected_revision,5); assert.equal(posts[0].body.text,'draft for revision four plus edit');
    });
    await fixture('skip preserves drafts and visits all open questions without source changes',async ({page,click,selected,posts,load})=>{
      await page.type('textarea','deferred draft'); await click('Skip / next'); assert.equal(await selected(),secondId);
      await page.type('textarea','second draft'); await click('Skip / next'); assert.equal(await selected(),id);
      assert.equal(await page.$eval('textarea',el=>el.value),'deferred draft'); await load();
      assert.equal(await page.$eval('textarea',el=>el.value),'deferred draft'); assert.equal(posts.length,0);
    });
    await fixture('explicit navigation reveals current question after accumulating history; drafts and refresh keep viewport/focus',async ({page,model,refresh,click,posts})=>{
      assert.equal(await page.evaluate(()=>scrollY),0);
      assert.equal(await page.$eval('textarea',el=>document.activeElement===el),false);
      model.requests=[base,second,...[3,4,5].map(n=>({...base,request_id:`00000000-0000-4000-8000-${String(n+10).padStart(12,'0')}`,origin_id:`TASK-${n}`,title:`Question ${n}`}))];
      model.needs_answer_count=5; await refresh();
      const inView=()=>page.$eval('#current h2',el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;});
      for (let n=0;n<4;n++) { await click('Skip / next'); assert.equal(await inView(),true); }
      assert.equal(await page.$$eval('#transcript article',nodes=>nodes.length),4);
      await page.focus('textarea'); await page.type('textarea','retained draft');
      const before=await page.evaluate(()=>({scroll:scrollY,focus:document.activeElement.id}));
      await page.keyboard.type(' plus text');
      assert.deepEqual(await page.evaluate(()=>({scroll:scrollY,focus:document.activeElement.id})),before);
      await page.evaluate(()=>document.querySelector('#refresh').click());
      await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
      assert.deepEqual(await page.evaluate(()=>({scroll:scrollY,focus:document.activeElement.id})),before);
      assert.equal(await page.$eval('textarea',el=>el.value),'retained draft plus text');
      await page.evaluate(()=>{document.querySelector('#queue').open=true;document.querySelector('#items button').click();});
      assert.equal(await inView(),true); assert.equal(posts.length,0);
      await page.screenshot({path:path.join(screenshots,'guide-next-viewport.png')});
    });
    await fixture('coverage unavailable never presents cached count as current; unstable questions visible',async ({page,model,refresh,posts,setReadHandler})=>{
      model.source_current=false; model.needs_answer_count=900;
      model.coverage_issues=[{origin_id:'TASK-9',title:'Unstable question',body:'Exact\n  question?',reason:'No stable episode; read-only.'}];
      await refresh(); assert.match(await page.$eval('#intro',el=>el.textContent),/count unavailable/);
      assert.doesNotMatch(await page.$eval('#intro',el=>el.textContent),/900/);
      assert.equal(await page.$eval('#issues .raw',el=>el.textContent),'Exact\n  question?');
      setReadHandler(req=>req.abort('failed')); await refresh();
      assert.match(await page.$eval('#coverage',el=>el.textContent),/unavailable/); assert.equal(posts.length,0);
    });
    await fixture('empty source has a verified zero and no actionable composer',async ({page,model,refresh,posts})=>{
      model.requests=[]; model.needs_answer_count=0; await refresh();
      assert.match(await page.$eval('#intro',el=>el.textContent),/0 open questions/);
      assert.equal(await page.$eval('#send',el=>el.disabled),true); assert.equal(posts.length,0);
    });
    await fixture('withdrawal is not success or automatic advancement',async ({page,model,posts,click,wait,idle,refresh,selected,reply,setHandler})=>{
      setHandler(req=>req.url().endsWith('/send')?req.abort('failed'):reply(req));
      await page.type('textarea',exact); await click('Send answer'); await idle();
      model.requests=[second]; model.needs_answer_count=1; await refresh();
      await wait('Withdrawal is not delivery confirmation'); assert.equal(await selected(),id);
      assert.equal(posts.length,2); await wait('Check delivery');
    });
    for (const invalid of ['request','text','revision','answer-id']) await fixture('reject mismatched '+invalid+' receipt without advancing or resending',async ({page,posts,click,idle,wait,selected,reply,setHandler})=>{
      setHandler(req=>{
        if (!req.url().endsWith('/send')) return reply(req);
        const changes=invalid==='answer-id'?{answer_id:otherAnswerId}:invalid==='request'?{request_id:secondId}:{answer:{request_id:id,expected_revision:invalid==='revision'?5:4,text:invalid==='text'?'different':exact}};
        return reply(req,'accepted',changes);
      });
      await page.type('textarea',exact); await click('Send answer'); await idle(); await wait('Unrecognized answer receipt');
      assert.equal(await selected(),id); await wait('Check delivery'); assert.equal(posts.length,2);
    });
    await fixture('accepted receipt remains sticky after racing stale GET and replacement identity',async ({page,posts,model,refresh,load,click,idle,wait,reply,setHandler,setReadHandler})=>{
      let release;
      setHandler(req=>req.url().endsWith('/send')?req.abort('failed'):reply(req,'pending'));
      await page.type('textarea',exact); await click('Send answer'); await idle();
      // Reconcile an uncertain send while a stale GET is held in flight.
      model.requests[0].answer=summary('pending');
      const started=new Promise(resolve=>setReadHandler((req,response)=>{release=()=>req.respond(response);resolve();}));
      await page.evaluate(()=>document.querySelector('#refresh').click()); await started;
      setHandler(req=>reply(req,'accepted')); await click('Check delivery'); await idle(); await wait('Answer accepted by source');
      await release(); await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
      setReadHandler(null); model.requests[0].answer=summary('unknown',exact,otherAnswerId); await refresh(); await load();
      assert.match(await page.$eval('#transcript',el=>el.textContent),/Answer accepted by source/);
      assert.equal(posts.length,3);
      const stored=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('ccc:decision-guide:v1')));
      assert.equal(stored.entries[id].saved.answer_id,answerId); assert.equal(stored.entries[id].saved.state,'accepted');
    });
    await fixture('malformed recovery storage blocks submission',async ({page,load,posts})=>{
      await page.evaluate(()=>sessionStorage.setItem('ccc:decision-guide:v1','{broken')); await load();
      assert.equal(await page.$eval('textarea',el=>el.disabled),true); assert.match(await page.$eval('#guidance',el=>el.textContent),/storage/); assert.equal(posts.length,0);
    });
    await fixture('storage write failure blocks POST',async ({page,posts})=>{
      assert.equal(await page.$eval('textarea',el=>el.disabled),true); assert.equal(posts.length,0);
    },{before:()=>{Storage.prototype.setItem=()=>{throw Error('Storage unavailable');};}});
    await fixture('HTTP UUID fallback preserves frozen idempotency',async ({page,posts,click,idle,wait})=>{
      assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
      await page.type('textarea',exact); await click('Send answer'); await idle(); await wait('Retry saving same answer');
      assert.match(posts[0].body.idempotency_key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      await click('Retry saving same answer'); await idle(); assert.deepEqual(posts[0],posts[1]);
    },{origin:'http://decisions.test',before:()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true})});
    await fixture('same-origin parent ticket navigation and verified count/null messages',async ({page,posts,model})=>{
      await page.evaluate(()=>{
        window.ticketMessages=[]; window.decisionCounts=[];
        addEventListener('message',event=>{
          if(event.data?.type==='ccc:open-ticket') window.ticketMessages.push({data:event.data,origin:event.origin});
          if(event.data?.type==='ccc:decision-count') window.decisionCounts.push(event.data.count);
        });
        const frame=document.createElement('iframe'); frame.src='/static/decision-guide.html'; document.body.append(frame);
      });
      await page.waitForFunction(()=>document.querySelector('iframe').contentDocument.querySelector('.ticket'));
      const frame=page.frames().find(f=>f!==page.mainFrame()); await frame.click('.ticket');
      await page.waitForFunction(()=>window.ticketMessages.length===1);
      await page.waitForFunction(()=>window.decisionCounts.length===1);
      assert.deepEqual(await page.evaluate(()=>window.decisionCounts),[2]);
      assert.deepEqual(await page.evaluate(()=>window.ticketMessages),[{data:{type:'ccc:open-ticket',ref:'TASK-1'},origin:'https://decisions.test'}]);
      for (const modifier of ['ctrlKey','metaKey','shiftKey','altKey']) {
        assert.equal(await frame.$eval('.ticket',(node,modifier)=>{
          let prevented;node.addEventListener('click',event=>{prevented=event.defaultPrevented;event.preventDefault();},{once:true});
          node.dispatchEvent(new MouseEvent('click',{button:0,bubbles:true,cancelable:true,[modifier]:true}));return prevented;
        },modifier),false);
      }
      model.source_current=false; model.needs_answer_count=900;
      await frame.click('#refresh'); await page.waitForFunction(()=>window.decisionCounts.length===2);
      assert.deepEqual(await page.evaluate(()=>window.decisionCounts),[2,null]);
      assert.equal(await page.evaluate(()=>window.ticketMessages.length),1); assert.equal(posts.length,0);
    });
    await fixture('promoted session inbox acceptance is separate from explicit consumption',async ({page,model,refresh,posts,click,wait,idle,setHandler})=>{
      model.source_current=false; model.needs_answer_count=null;
      model.requests=[{...base,adapter:'session-question',reply_supported:true,source_current:true,session_id:secondId}];
      await refresh();
      assert.equal(await page.$eval('#answerFields',el=>el.hidden),false);
      assert.equal(await page.$eval('#current a',el=>el.getAttribute('href')),'/?session='+secondId);
      setHandler(req=>req.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,request_id:id,answer_id:answerId,state:req.url().endsWith('/send')?'accepted':'pending',answer:{request_id:id,expected_revision:4,text:exact,actor:'owner'},receipt:{acceptance:'source_inbox',session_consumed:false},work_completed:false})}));
      await page.type('textarea',exact); await click('Send answer'); await idle();
      await wait('Answer saved for the originating session; waiting for session receipt.');
      model.requests[0].answer={...summary('accepted'),session_consumed:true};
      await refresh(); await wait('Answer received by the originating session.');
      model.requests[0].answer.session_consumed=false; await refresh(); await wait('Answer received by the originating session.');
      assert.equal(posts.length,2);
    });
    await fixture('hung save and send release controls without another submission',async ({page,posts,click,idle,wait,load,reply,setHandler})=>{
      await page.evaluate(()=>{const native=window.setTimeout;window.setTimeout=(fn,ms,...args)=>native(fn,ms===30000?250:ms,...args);});
      setHandler(()=>{}); await page.type('textarea',exact); await click('Send answer');
      await wait('The response took too long.'); await idle(); await wait('Retry saving same answer');
      assert.equal(posts.length,1);
      setHandler(req=>reply(req)); await click('Retry saving same answer'); await idle(); await wait('Send saved answer');
      assert.deepEqual(posts[0],posts[1]);
      setHandler(()=>{}); await click('Send saved answer'); await wait('The response took too long.'); await idle();
      await wait('Check delivery'); assert.equal(posts.length,3);
      assert.equal(await page.evaluate(()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='Check delivery').disabled),false);
      await load(); assert.equal(posts.length,3); await wait('Check delivery');
      setHandler(req=>reply(req,'accepted')); await click('Check delivery'); await idle();
      assert.equal(posts[3].url,'/api/decision-inbox/answers/reconcile');
      assert.equal(posts.filter(post=>post.url.endsWith('/send')).length,1);
    });
    await fixture('confirmed acceptance stays clear after source stops waiting',async ({page,model,load,wait,posts})=>{
      model.requests[0]={...base,revision:5,status:'withdrawn',needs_answer:false,can_answer:false,answer:summary('accepted')};
      await page.evaluate(({key,id,base,exact,answerId})=>sessionStorage.setItem(key,JSON.stringify({selected:id,seen:[id],drafts:{},snapshots:{[id]:base},entries:{[id]:{payload:{request_id:id,expected_revision:4,text:exact,idempotency_key:answerId},saved:{answer_id:answerId,state:'unknown',text:exact,expected_revision:4},deliveryUnknown:true}}})),{key:'ccc:decision-guide:v1',id,base,exact,answerId});
      await load(); await wait('Answer accepted by source.');
      assert.equal(await page.evaluate(()=>document.body.textContent.includes('Withdrawal is not delivery confirmation.')),false);
      assert.equal(await page.evaluate(()=>document.body.textContent.includes('Current source question has changed')),false);
      assert.equal(posts.length,0);
    });
    await fixture('external answer refresh advances the retained selection and keeps its receipt in history',async ({page,model,load,selected,posts})=>{
      // Reproduce returning to a saved selection answered from another view.
      model.requests[0]={...base,revision:5,status:'withdrawn',needs_answer:false,can_answer:false,answer:summary('accepted')};
      model.needs_answer_count=1;
      await load();
      assert.equal(await selected(),secondId);
      assert.match(await page.$eval('#transcript',el=>el.textContent),/Answer accepted by source/);
      assert.equal(await page.$$eval('#items button',nodes=>nodes.length),1);
      assert.equal(await page.$eval('#current h2',el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),true);
      await page.screenshot({path:path.join(screenshots,'guide-external-answer.png'),fullPage:true});
      assert.equal(posts.length,0);
    });
    await fixture('foreground refresh removes answered questions without posting or losing a draft',async ({page,model,selected,posts})=>{
      await page.type('textarea','keep my exact draft');
      await page.$eval('textarea',el=>{el.focus();el.setSelectionRange(3,7);});
      model.requests[0]={...base,revision:5,body:'Changed while away'};
      await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
      await page.waitForFunction(()=>document.querySelector('#current .raw').textContent==='Changed while away');
      assert.deepEqual(await page.$eval('textarea',el=>[el.value,document.activeElement===el,el.selectionStart,el.selectionEnd]),['keep my exact draft',true,3,7]);
      assert.equal(await selected(),id);
      assert.equal(await page.$eval('#send',el=>el.disabled),true);
      assert.equal(posts.length,0);
    });
    await fixture('periodic refresh updates an external answer and skips hidden documents',async ({page,model,selected,posts})=>{
      model.requests[0]={...base,status:'withdrawn',needs_answer:false,answer:summary('accepted')};
      model.needs_answer_count=1;
      await page.evaluate(()=>{
        Object.defineProperty(document,'hidden',{configurable:true,value:true});
        window.guideRefreshTick();
      });
      assert.equal(await selected(),id);
      await page.evaluate(()=>{
        Object.defineProperty(document,'hidden',{configurable:true,value:false});
        window.guideRefreshTick();
      });
      await page.waitForFunction(id=>document.querySelector('#current article')?.dataset.requestId===id,{},secondId);
      assert.match(await page.$eval('#transcript',el=>el.textContent),/Answer accepted by source/);
      assert.equal(posts.length,0);
    },{before:()=>{
      const native=window.setInterval;
      window.setInterval=(fn,ms,...args)=>ms===60000?(window.guideRefreshTick=fn,0):native(fn,ms,...args);
    }});
    await fixture('returning to the sidebar view refreshes its retained iframe',async ({page,model,posts})=>{
      const tabScript=fs.readFileSync(path.join(root,'static/decision-tab.js'),'utf8');
      // Mount the production sidebar controller around its real guide document.
      await page.evaluate(script=>{
        const list=document.createElement('div'); list.id='convList';
        list.innerHTML='<div id="sidebarDecisionGuideHost" style="width:350px;height:700px"></div>';
        document.body.append(list);
        (0,eval)(script);
      },tabScript);
      await page.waitForFunction(()=>document.querySelector('#cccDecisionGuideFrame')?.contentDocument.querySelector('#current article'));
      await page.evaluate(()=>{document.querySelector('#sidebarDecisionGuideHost').remove();window.CCCDecisionTab.mount();});
      model.requests[0]={...base,status:'withdrawn',needs_answer:false,answer:summary('accepted')}; model.needs_answer_count=1;
      await page.evaluate(()=>{
        const host=document.createElement('div');host.id='sidebarDecisionGuideHost';host.style='width:350px;height:700px';
        document.querySelector('#convList').append(host); window.CCCDecisionTab.mount();
      });
      await page.waitForFunction(id=>document.querySelector('#cccDecisionGuideFrame').contentDocument.querySelector('#current article')?.dataset.requestId===id,{},secondId);
      assert.equal(posts.length,0);
    });
    console.log(`PASS ${checks} guided queue browser scenarios; screenshots: ${screenshots}`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
