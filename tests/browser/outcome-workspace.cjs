// Isolated contract fixture: never reads or mutates a running outcome registry.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../../require-puppeteer.js');
const {findChromePath} = require('../../puppeteer-browser-config.js');
const html = fs.readFileSync('static/index.html','utf8');
const behavior = fs.readFileSync('static/app.js','utf8').split('// Outcomes are a projection of durable server state, never a browser supervisor.')[1];
assert(behavior, 'Outcome controller must exist');
assert(html.includes('id="flowBoard"') && html.includes('id="simpleHome"'), 'Existing modes retained');
const mount = html.match(/  <section id="outcomeWorkspace"[\s\S]*?<\/section>/)[0];
const nav = html.match(/  <nav id="outcomeNavigation"[\s\S]*?<\/nav>/)[0];
const settingsStart = html.lastIndexOf('            <div class="settings-row"', html.indexOf('id="outcomeSettingLabel"'));
const settings = html.slice(settingsStart, html.indexOf('            <div class="settings-row" data-keywords="simple', settingsStart));
const css = fs.readFileSync('static/app.css','utf8');
let rows = [], mode = '', calls = [], enabled = false, reads = 0, sourceRows = [];
const base = () => ({id:'outcome-example',title:'Improve release readiness',goal:'A repeatable verified release',criteria:[{id:'c1',text:'Release check passes',required:true,accepts:['verified'],max_age_s:3600}],state:'proposed',health:'unknown',revision:1,commitment:false,rank:2,repo_path:'',queue:'',next_action:{text:'Check release receipt',owner:'lead',trigger:'artifact available'},briefing:'Release receipt ready for inspection',health_reason:'Serving check pending',responsibility:{role:'lead',ref:'example-attempt',attempt_id:'attempt-example'},decision_refs:[],evidence:[],links:[],attempts:[],updated_at:'2026-01-01T00:00:00Z'});
const server = http.createServer(async(req,res)=>{
  const send = (code,data) => {res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url==='/decision-guide.html') {res.writeHead(200,{'Content-Type':'text/html'});res.end('<h1>Existing Decisions fixture</h1>');return;}
  if(req.url==='/api/decision-inbox/requests') return send(200,{ok:true,source_current:true,requests:sourceRows});
  if(req.url==='/api/outcomes/settings') {
    if(mode==='setting-error') return send(503,{ok:false,error:'Setting unavailable'});
    if(req.method==='POST') {let raw='';for await(const chunk of req) raw+=chunk;const value=JSON.parse(raw);assert.equal(typeof value.enabled,'boolean');enabled=value.enabled;}
    return send(200,{ok:true,enabled});
  }
  if(req.url.startsWith('/api/outcomes')) {
    assert(enabled,'No outcome API access while installation is off');
    if(req.method==='GET') reads++;

    if(mode==='wait') return;
    if(req.method==='GET') return send(200,{ok:true,outcomes:rows});
    let raw=''; for await(const chunk of req) raw+=chunk;
    const p=JSON.parse(raw);calls.push({url:req.url,p});
    if(req.url.endsWith('/create')) { assert.equal(p.state,'proposed'); assert(p.repo_path); }
    else { const allowed=new Set(['title','goal','criteria','description','rank','deadline','state','commitment']); assert(Object.keys(p.patch).every(k=>allowed.has(k))); }

    if(mode==='conflict') return send(409,{ok:false,error:'Changed by another writer'});
    if(mode==='invalid') return send(400,{ok:false,error:'Repository path is invalid'});
    let o;
    if(req.url.endsWith('/create')) {o={...base(),...p,id:'outcome-created'};rows.push(o);}
    else {o=rows.find(x=>x.id===p.id);assert.equal(p.expected_revision,o.revision);Object.assign(o,p.patch,{revision:o.revision+1});}
    return send(200,{ok:true,outcome:o});
  }
  res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+'</style>'+nav+mount+'<div id="legacy">Existing workspace'+settings+'</div><script>'+behavior+'</script>');
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const browser=await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.setViewport({width:1280,height:900});
    await page.goto(origin+'/?workspace=outcomes');
    await page.waitForFunction(()=>!document.querySelector('#outcomeSettingToggle').disabled);
    assert(await page.$eval('#outcomeNavigation',e=>e.hidden));
    assert(await page.$eval('#outcomeWorkspace',e=>e.hidden));
    assert.equal(reads,0,'Deep link cannot bypass default-off setting');
    assert.equal(await page.$eval('#legacy',e=>e.inert),false);
    await page.click('#outcomeSettingToggle');
    await page.waitForFunction(()=>document.querySelector('#outcomeSettingToggle').getAttribute('aria-checked')==='true');
    assert(await page.$eval('#outcomeSettingToggle',e=>e.classList.contains('is-on')));
    await page.click('[data-ow-view="today"]');
    await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('empty'));
    await page.click('[data-ow-action="create"]');
    await page.type('[name="title"]','Verify an example release');await page.type('[name="goal"]','Release consistently');await page.type('[name="criteria"]','Serving check passes');
    await page.type('[name="repo_path"]','/example/repository');
    mode='invalid';await page.click('[type="submit"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('Repository path is invalid'));
    assert(await page.$('[name="title"]'),'Keep draft after failed save');
    mode='';await page.click('[type="submit"]');await page.waitForSelector('.ow-brief');
    assert.equal(calls[0].p.commitment,false);assert.equal(rows.length,1);
    await page.click('[data-ow-action="commit"]');await page.waitForFunction(()=>document.querySelector('[data-ow-action="commit"]').textContent==='Remove commitment');assert.equal(rows[0].commitment,true);
    Object.assign(rows[0].criteria[0],{accepts:['verified'],max_age_s:3600});await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent==='Outcomes refreshed.');
    await page.click('[data-ow-action="edit"]');
    await page.$eval('[name="rank"]',e=>{e.value='1';});await page.$eval('[name="description"]',e=>{e.value='Upcoming release window';});await page.$eval('[name="deadline"]',e=>{e.value='2027-01-15';});await page.click('[type="submit"]');
    await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('valid deadline'));
    await page.$eval('[name="deadline"]',e=>{e.value='2027-01-15T12:00:00Z';});await page.click('[type="submit"]');
    await page.waitForFunction(()=>document.querySelector('.ow-brief').textContent.includes('Upcoming release window'));assert.equal(rows[0].rank,1);assert.deepEqual(rows[0].criteria[0].accepts,['verified']);assert.equal(rows[0].criteria[0].max_age_s,3600);assert((await page.$eval('.ow-brief',e=>e.textContent)).includes('lead · example-attempt'));assert((await page.$eval('.ow-brief',e=>e.textContent)).includes('Release receipt ready for inspection'));assert(await page.$('.ow-health'));
    mode='conflict';await page.click('[data-ow-action="park"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('Revision conflict'));assert.equal(rows[0].state,'active');
    mode='';await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent==='Outcomes refreshed.');
    rows[0].decision_refs=[{id:'pending-link',state:'unprepared'}];
    rows.push({...base(),id:'outcome-second',title:'Prepare documentation',commitment:true,rank:3,attempts:[{id:'attempt-recent',role:'verifier',spawned_at:new Date().toISOString()}],incidents:[{text:'Observed failure',verified:true}],displacement:{outcome_id:'outcome-created',reason:'Recovery first'},findings:[{text:'Receipt missing',owner:'lead',trigger:'next receipt check'}],evidence:[{criterion_id:'c1',result:'inconclusive',method:'release check'}],links:[{session_id:'example-session',relationship:'explicit'}]});
    await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>document.querySelectorAll('.ow-agenda [data-ow-select]').length===2);
    assert((await page.$eval('.ow-health summary',e=>e.textContent)).includes('1 active · 2 committed'));
    const counts=await page.$$eval('.ow-health dt',els=>Object.fromEntries(els.map(el=>[el.textContent,el.nextElementSibling.textContent])));assert.equal(counts['Attempt records in the last 24 hours'],'1');assert.equal(counts['Verified delivered with matching acceptance evidence'],'0');assert((await page.$eval('.ow-health',e=>e.textContent)).includes('Some outcome health is unknown.'));
    rows[1].decision_refs=[{id:'decision-example',kind:'decision',state:'pending',question:'Choose release timing',request_id:'request-example',revision:'3',preparation:{why_now:'Release window approaches',options:'Release now or wait',recommendation:'Wait for the check',tradeoffs:'Waiting delays availability'}}];sourceRows=[{request_id:'request-example',revision:3,kind:'question',status:'open'}];
    await page.click('[data-ow-view="needs"]');await page.waitForFunction(()=>document.querySelector('.ow-needs h2')?.textContent==='Choose release timing');await page.screenshot({path:'/tmp/outcomes-needs.png',fullPage:true});assert((await page.$eval('#owContent',e=>e.textContent)).includes('awaiting preparation'));
    assert.equal(await page.$$eval('#owContent [type="submit"]',e=>e.length),0,'No fabricated answer action');
    sourceRows[0].revision=4;await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>!document.querySelector('.ow-needs h2'));assert((await page.$eval('#owContent',e=>e.textContent)).includes('No prepared linked decisions'));sourceRows[0].revision=3;
    await page.click('[data-ow-view="today"]');await page.click('[data-ow-select="outcome-second"]');
    await page.screenshot({path:'/tmp/outcomes-desktop.png',fullPage:true});
    await page.setViewport({width:390,height:844});
    assert.equal(await page.$eval('.ow-agenda',e=>getComputedStyle(e).display),'none');
    assert(await page.$eval('#owSelect',e=>e.getBoundingClientRect().bottom<document.querySelector('.ow-brief').getBoundingClientRect().top));
    assert(await page.$eval('#outcomeWorkspace',e=>e.scrollWidth<=e.clientWidth),'Phone has no horizontal overflow');
    await page.select('#owSelect','outcome-created');
    assert.equal(await page.evaluate(()=>document.activeElement.tagName),'H2');
    await page.screenshot({path:'/tmp/outcomes-phone.png',fullPage:true});
    await page.click('[data-ow-action="park"]');await page.waitForFunction(()=>document.querySelector('.ow-state').textContent.includes('parked'));assert.equal(rows[0].commitment,false);
    await page.click('[data-ow-action="edit"]');const beforeReturn=reads;
    await page.evaluate(()=>{window.originalDateNow=Date.now;Date.now=()=>window.originalDateNow()+31000;window.dispatchEvent(new Event('focus'));});
    assert.equal(reads,beforeReturn,'Focus return never overwrites editor');
    await page.click('[data-ow-action="discard"]');
    await page.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForFunction(()=>document.querySelector('#owStatus').textContent==='Outcomes refreshed.');assert.equal(reads,beforeReturn+1,'One coalesced stale-return refresh');
    await page.evaluate(()=>{Date.now=window.originalDateNow;});
    mode='wait';await page.click('[data-ow-action="refresh"]');await page.click('[data-ow-action="cancel"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('cancelled'));assert(await page.$('.ow-brief'));
    mode='';await page.click('[data-ow-action="everything"]');assert(await page.$eval('#outcomeWorkspace',e=>e.hidden));assert.equal(await page.$eval('#legacy',e=>e.inert),false);assert.equal(await page.$eval('#outcomeTools',e=>e.hidden),false);
    await page.click('[data-ow-view="today"]');await page.waitForSelector('.ow-brief');await page.click('[data-ow-view="needs"]');await page.waitForFunction(()=>document.querySelector('#outcomeWorkspace').getAttribute('aria-busy')==='false');await page.click('[data-ow-action="decisions"]');
    assert.equal(await page.$eval('.ow-decisions-frame',e=>new URL(e.src).pathname),'/decision-guide.html');
    const accepted={...base(),id:'outcome-delivered',state:'delivered',criteria_revision:2,evidence:[{id:'evidence-pass',criterion_id:'c1',criteria_revision:2,result:'pass'}],acceptance:{accepted_at:new Date().toISOString(),criteria_revision:2,evidence_ids:{c1:'evidence-pass'}}};rows.push(accepted,{...accepted,id:'outcome-stale-delivery',criteria_revision:3});
    rows[1].health='gap';rows[1].events=[{state:'exhausted'}];rows[1].responsibility={role:'recovery'};
    await page.goto(origin+'/');await page.waitForSelector('.ow-brief');
    const canonicalCounts=await page.$$eval('.ow-health dt',els=>Object.fromEntries(els.map(el=>[el.textContent,el.nextElementSibling.textContent])));assert.equal(canonicalCounts['Verified delivered with matching acceptance evidence'],'1');assert.equal(canonicalCounts['Authoritative health gaps / exhausted recovery'],'1 / 1');assert((await page.$eval('.ow-health',e=>e.textContent)).includes('lack matching acceptance and evidence'));
    assert.equal(await page.$eval('#outcomeNavigation',e=>e.hidden),false,'Durable enabled setting becomes normal landing');
    await page.click('[data-ow-action="everything"]');await page.click('#outcomeSettingToggle');
    await page.waitForFunction(()=>document.querySelector('#outcomeSettingToggle').getAttribute('aria-checked')==='false');
    const readsBefore=reads;await page.goto(origin+'/?workspace=outcomes');await page.waitForFunction(()=>!document.querySelector('#outcomeSettingToggle').disabled);
    assert(await page.$eval('#outcomeNavigation',e=>e.hidden));assert.equal(reads,readsBefore);
    mode='setting-error';await page.reload();await page.waitForFunction(()=>!document.querySelector('#outcomeSettingRetry').hidden);
    assert(await page.$eval('#outcomeNavigation',e=>e.hidden),'Unknown setting fails closed');
    mode='';await page.click('#outcomeSettingRetry');await page.waitForFunction(()=>!document.querySelector('#outcomeSettingToggle').disabled);
    assert.deepEqual(errors,[]);console.log('PASS: durable default-off/opt-in/reload/disable/failure guard; unified navigation; outcome CRUD/CAS, errors, cancel, existing Decisions frame, desktop/phone layout and focus');
  } finally {await browser.close();server.closeAllConnections();server.close();}
})().catch(err=>{console.error(err);server.closeAllConnections();server.close();process.exitCode=1;});
