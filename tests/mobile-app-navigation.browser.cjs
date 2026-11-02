const root=require('node:path').resolve(__dirname,'..'),assert=require('node:assert/strict');
const base=process.env.CCC_MOBILE_APPS_TEST_URL; if(!base) { console.log('SKIP: set CCC_MOBILE_APPS_TEST_URL to a running CCC'); process.exit(0); }
const pp=require(root+'/require-puppeteer.js'),{findChromePath}=require(root+'/puppeteer-browser-config.js');
(async()=>{const b=await pp.launch({executablePath:findChromePath(),args:['--no-sandbox']});try{const p=await b.newPage();await p.setViewport({width:390,height:844});
await p.evaluateOnNewDocument(()=>{localStorage.setItem('ccc-tour-done','1');localStorage.setItem('ccc-engines-first-run-done','1');});
const errors=[];p.on('pageerror',e=>errors.push(e.message));
console.log('load dashboard');await p.goto(base+'/',{waitUntil:'domcontentloaded'});await p.waitForSelector('[data-mobile-app="decision-inbox"]');
const catalog=await p.evaluate(()=>window.cccRailApps.map(a=>a.id).sort());
assert.deepEqual(await p.$$eval('[data-mobile-app]',els=>els.map(e=>e.dataset.mobileApp).sort()),catalog);
await p.waitForFunction(()=>getComputedStyle(document.querySelector('#mobileBottomNav')).display!=='none');
console.log('open more');await p.click('[data-mobile-nav-menu]');await p.screenshot({path:'/tmp/mobile-apps-more.png'});
for(const id of catalog) {
  assert.ok(await p.evaluate(id=>{const el=[...document.querySelectorAll('[data-mobile-app]')].find(e=>e.dataset.mobileApp===id);el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));},id), 'App is obscured: '+id);
}

console.log('open decisions');await p.click('[data-mobile-app="decision-inbox"]');await p.waitForSelector('#cccMobileAppFrame');
await p.waitForFunction(()=>document.querySelector('#cccMobileAppFrame').contentDocument?.querySelector('.decision-row'));
await p.screenshot({path:'/tmp/mobile-apps-decision.png'});
assert.ok(await p.$eval('#mobileBottomNav',e=>e.getBoundingClientRect().bottom<=innerHeight));
assert.ok(await p.evaluate(()=>document.querySelector('#cccMobileAppFrame').getBoundingClientRect().bottom<=document.querySelector('#mobileBottomNav').getBoundingClientRect().top+1));
console.log('open more');await p.click('[data-mobile-nav-menu]');await p.click('[data-mobile-app="savings"]');
assert.match(p.url(),/ccc_app=savings/);
console.log('back');await p.evaluate(()=>history.back());await p.waitForFunction(()=>document.querySelector('#cccMobileAppFrame').title==='Decisions');
await p.click('[data-mobile-nav="workers"]');assert.ok(await p.$eval('#cccMobileAppFrame',e=>e.hidden));
console.log('direct link');await p.goto(base+'/decision-inbox.html',{waitUntil:'domcontentloaded'});await p.waitForFunction(()=>location.search.includes('ccc_app=decision-inbox') && document.querySelector('#cccMobileAppFrame'));
await p.reload({waitUntil:'domcontentloaded'});await p.waitForSelector('#cccMobileAppFrame');
assert.deepEqual(errors,[]);console.log(JSON.stringify({pass:true,errors}));
}finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
