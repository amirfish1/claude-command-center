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
const entry = html.match(/<button type="button" class="outcome-entry"[\s\S]*?<\/button>/)[0];
const css = fs.readFileSync('static/app.css','utf8');
let rows = [], mode = '', calls = [];
const base = () => ({id:'outcome-example',title:'Improve release readiness',goal:'A repeatable verified release',criteria:[{id:'c1',text:'Release check passes',required:true}],state:'proposed',health:'unknown',revision:1,commitment:false,rank:2,repo_path:'',queue:'',next_action:{text:'Check release receipt',owner:'lead',trigger:'artifact available'},responsibility:'lead',decision_refs:[],evidence:[],links:[],attempts:[],updated_at:'2026-01-01T00:00:00Z'});
const server = http.createServer(async(req,res)=>{
  const send = (code,data) => {res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url.startsWith('/api/outcomes')) {
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
  res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+'</style>'+entry+mount+'<div id="legacy">Existing workspace</div><script>'+behavior+'</script>');
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const browser=await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.setViewport({width:1280,height:900});
    await page.goto(origin+'/?workspace=outcomes');
    await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('empty'));
    await page.click('[data-ow-action="create"]');
    await page.type('[name="title"]','Verify an example release');await page.type('[name="goal"]','Release consistently');await page.type('[name="criteria"]','Serving check passes');
    await page.type('[name="repo_path"]','/example/repository');
    mode='invalid';await page.click('[type="submit"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('Repository path is invalid'));
    assert(await page.$('[name="title"]'),'Keep draft after failed save');
    mode='';await page.click('[type="submit"]');await page.waitForSelector('.ow-brief');
    assert.equal(calls[0].p.commitment,false);assert.equal(rows.length,1);
    await page.click('[data-ow-action="commit"]');await page.waitForFunction(()=>document.querySelector('[data-ow-action="commit"]').textContent==='Remove commitment');assert.equal(rows[0].commitment,true);
    await page.click('[data-ow-action="edit"]');
    await page.$eval('[name="rank"]',e=>{e.value='1';});await page.$eval('[name="description"]',e=>{e.value='Upcoming release window';});await page.click('[type="submit"]');
    await page.waitForFunction(()=>document.querySelector('.ow-brief').textContent.includes('Upcoming release window'));assert.equal(rows[0].rank,1);
    mode='conflict';await page.click('[data-ow-action="park"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('Revision conflict'));assert.equal(rows[0].state,'active');
    mode='';await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent==='Outcomes refreshed.');
    rows[0].decision_refs=[{id:'pending-link',state:'unprepared'}];
    rows.push({...base(),id:'outcome-second',title:'Prepare documentation',commitment:true,rank:3,incidents:[{text:'Observed failure',verified:true}],displacement:{outcome_id:'outcome-created',reason:'Recovery first'},findings:[{text:'Receipt missing',owner:'lead',trigger:'next receipt check'}],evidence:[{criterion_id:'c1',result:'inconclusive',method:'release check'}],links:[{session_id:'example-session',relationship:'explicit'}]});
    await page.click('[data-ow-action="refresh"]');await page.waitForFunction(()=>document.querySelectorAll('.ow-agenda [data-ow-select]').length===2);
    await page.click('[data-ow-view="needs"]');assert((await page.$eval('#owContent',e=>e.textContent)).includes('awaiting preparation'));
    assert.equal(await page.$$eval('#owContent [type="submit"]',e=>e.length),0,'No fabricated answer action');
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
    mode='wait';await page.click('[data-ow-action="refresh"]');await page.click('[data-ow-action="cancel"]');await page.waitForFunction(()=>document.querySelector('#owStatus').textContent.includes('cancelled'));assert(await page.$('.ow-brief'));
    mode='';await page.click('[data-ow-action="everything"]');assert(await page.$eval('#outcomeWorkspace',e=>e.hidden));assert.equal(await page.$eval('#legacy',e=>e.inert),false);
    await page.click('#outcomeEntry');await page.waitForSelector('.ow-brief');await page.click('[data-ow-view="needs"]');await Promise.all([page.waitForNavigation(),page.click('[data-ow-action="decisions"]')]);assert(page.url().endsWith('/decision-guide.html'));
    assert.deepEqual(errors,[]);console.log('PASS: isolated outcome CRUD/CAS, errors, cancel, decisions fallback, legacy return, desktop/phone layout and focus');
  } finally {await browser.close();server.closeAllConnections();server.close();}
})().catch(err=>{console.error(err);server.closeAllConnections();server.close();process.exitCode=1;});
