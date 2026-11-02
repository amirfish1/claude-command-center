const assert=require('node:assert/strict');
const root=process.cwd();
const puppeteer=require(root+'/require-puppeteer.js');
const {findChromePath}=require(root+'/puppeteer-browser-config.js');
(async()=>{const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});try {
const page=await browser.newPage();await page.setViewport({width:1440,height:1050});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.emulateTimezone('Europe/London');
await page.goto((process.env.CCC_SERVER || 'http://127.0.0.1:8090')+'/decision-inbox.html');await page.waitForSelector('.decision-row');
assert.equal(await page.$eval('#cards',e=>e.children.length),10);
await page.click('.decision-row summary');assert.ok(await page.$eval('.decision-row',e=>e.open));
assert.match(await page.$eval('.decision-row .origin',e=>e.textContent),/Why this appeared/);
await page.waitForSelector('.sweep');
assert.equal(await page.$eval('#sweepHistory', e=>e.tagName),'SECTION');
assert.equal(await page.$$eval('#sweepHistory details, #sweepHistory summary',e=>e.length),0);
assert.match(await page.$eval('#sweepSummary',e=>e.textContent),/Sweep history/);
const newestRun=await page.$eval('.sweep',e=>e.dataset.runId);
assert.match(await page.$eval('.sweep',e=>e.textContent),/Finished:.*P[SD]T/);
assert.match(await page.$eval('.sweep-llm',e=>e.textContent),/Yes|No|Unknown/);
await page.click('#sweepNext');
await page.waitForFunction(()=>document.querySelector('#sweepPage').textContent.startsWith('6–'));
assert.notEqual(await page.$eval('.sweep',e=>e.dataset.runId),newestRun);
await page.click('#sweepPrev');
await page.waitForFunction(()=>document.querySelector('#sweepPage').textContent.startsWith('1–'));
await page.screenshot({path:'/tmp/decision-desktop.png'});
await page.click('#next');assert.match(await page.$eval('#pageCount',e=>e.textContent),/Page 2/);
const ref=await page.$eval('.row-ref',e=>e.textContent); await page.type('#search',ref);assert.equal(await page.$$eval('.decision-row',e=>e.length),1);
await page.$eval('#search',e=>{e.value='';e.dispatchEvent(new Event('input'));});
await page.select('#statusFilter','closed');assert.ok(await page.$$eval('.decision-row',e=>e.length)>0);
await page.select('#statusFilter','open');
await page.setViewport({width:390,height:844});
await page.waitForSelector('#cccMobileAppFrame');
await page.waitForFunction(()=>document.querySelector('#cccMobileAppFrame').contentDocument?.querySelector('.sweep') && document.querySelector('#cccMobileAppFrame').contentDocument.querySelector('.decision-row'));
await page.screenshot({path:'/tmp/decision-mobile.png'});
assert.ok(await page.evaluate(()=>{const doc=document.querySelector('#cccMobileAppFrame').contentDocument;return doc.documentElement.scrollWidth<=doc.defaultView.innerWidth;}));
assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
// Keep the empty-but-used-LLM case visible and pin both Pacific DST offsets.
const fixture=await browser.newPage();await fixture.setViewport({width:1280,height:900});
await fixture.emulateTimezone('Asia/Tokyo');
fixture.on('pageerror',e=>errors.push(e.message));
await fixture.setRequestInterception(true);
fixture.on('request',request=>{
  if(new URL(request.url()).pathname==='/api/decision-inbox/runs') return request.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,total:3,activity_total:2,hidden_count:1,invalid_records:0,activity_runs:[
    {run_id:'fixture-llm',started_at:'2026-11-02T02:00:00Z',finished_at:'2026-11-02T02:00:01Z',created:[],llm_used:true,llm_calls:1},
    {run_id:'fixture-script',started_at:'2026-07-01T16:00:00Z',created:[{id:'fixture-decision',title:'Example decision <tag>',source_id:'board:example'}],llm_used:false,llm_calls:0}
  ]})});
  return request.continue();
});
await fixture.goto((process.env.CCC_SERVER || 'http://127.0.0.1:8090')+'/decision-inbox.html');
await fixture.waitForSelector('.sweep');
assert.equal(await fixture.$$eval('.sweep',rows=>rows.length),2);
assert.match(await fixture.$eval('[data-run-id="fixture-llm"]',e=>e.textContent),/Nov 1, 2026, 06:00:00 PM PST/);
assert.match(await fixture.$eval('[data-run-id="fixture-llm"]',e=>e.textContent),/No decisions added.*Yes/);
assert.match(await fixture.$eval('[data-run-id="fixture-script"]',e=>e.textContent),/Jul 1, 2026, 09:00:00 AM PDT/);
assert.match(await fixture.$eval('[data-run-id="fixture-script"]',e=>e.textContent),/Example decision <tag>.*board:example.*No/);
assert.equal(await fixture.$$eval('#sweepHistory details, #sweepHistory summary, #sweepHistory tag',e=>e.length),0);
assert.deepEqual(errors,[]);
console.log('PASS desktop, mobile, expansion, pagination, search, history, no JS errors');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
