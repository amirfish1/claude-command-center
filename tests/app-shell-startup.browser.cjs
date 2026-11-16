// CCC_APP_SHELL_TEST_URL=http://127.0.0.1:8090 node tests/app-shell-startup.browser.cjs
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

(async () => {
  const base = process.env.CCC_APP_SHELL_TEST_URL;
  if (!base) throw new Error('Set CCC_APP_SHELL_TEST_URL to a dashboard');
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:1000});
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done','1');
      localStorage.setItem('ccc-engines-first-run-done','1');
      localStorage.setItem('ccc-ui-mode','advanced');
    });
    const apps = [{id:'sessions',label:'Sessions',url:'/'},
      {id:'queues',label:'Queues',url:'/q2.html'},
      {id:'shell-test',label:'Test app',url:'/__app_shell_test'}];
    let removedApp=false;
    const errors=[];
    let archiveReads=0;
    page.on('pageerror',error=>errors.push(error.stack));
    await page.setRequestInterception(true);
    page.on('request',request=>{
      const url = new URL(request.url());
      if (url.pathname==='/api/conversations/list') {
        if (request.frame()===page.mainFrame()) archiveReads++;
        return request.respond({contentType:'application/json',body:'[]'});
      }
      if (request.method()==='POST') return request.respond({contentType:'application/json',body:'{}'});
      if (url.pathname==='/api/apps') return request.respond({contentType:'application/json',body:JSON.stringify({apps:removedApp?apps.filter(app=>app.id!=='shell-test'):apps})});
      if (url.pathname==='/__app_shell_test') return request.respond({contentType:'text/html',body:'<!doctype html><h1>Test app</h1>'});
      if (url.hostname==='fonts.googleapis.com' || url.hostname==='fonts.gstatic.com') return request.abort();
      return request.continue();
    });
    const appIsUncovered = async () => {
      assert.equal(await page.evaluate(()=>{
        // Keep the dashboard in its loading state while the app is usable.
        const overlay=document.getElementById('cccLoadingOverlay');
        overlay.classList.remove('gone','fade-out');
        const frame=document.getElementById('cccMobileAppFrame');
        const rect=frame.getBoundingClientRect();
        return document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)===frame;
      }),true,'dashboard loading must not cover an open app');
    };
    const open = async () => {
      await page.goto(base+'/?ccc_app=shell-test',{waitUntil:'domcontentloaded'});
      await page.waitForFunction(()=>document.getElementById('cccMobileAppFrame')?.contentDocument?.querySelector('h1'));
      assert.equal(await page.evaluate(()=>typeof window.cccOpenSession),'undefined','dashboard waits for the app');
      await page.evaluate(()=>{
        const frame=document.getElementById('cccMobileAppFrame');
        window.testAppDocument=frame.contentDocument;
        frame.contentWindow.eval("parent.postMessage({type:'ccc:app-fully-ready'},location.origin)");
      });
      await page.waitForSelector('.ccc-side-nav [data-app-id="shell-test"]');
      assert.equal(await page.evaluate(()=>typeof window.cccSwitchCoreApp),'undefined','desktop navigation does not boot hidden dashboard');
      await page.evaluate(()=>window.__cccEnsureDashboard());
      await page.waitForFunction(()=>!window.__cccDeferDashboardBoot && typeof window.cccSwitchCoreApp==='function');
      await page.waitForSelector('.ccc-side-nav [data-app-id="shell-test"]');
      assert.equal(await page.evaluate(()=>document.getElementById('cccMobileAppFrame').contentDocument===window.testAppDocument),true,'adoption keeps the browsing context');
      const geometry=await page.evaluate(()=>({
        frame:document.getElementById('cccMobileAppFrame').getBoundingClientRect().left,
        rail:document.querySelector('.ccc-side-nav').getBoundingClientRect().right,
      }));
      assert.ok(Math.abs(geometry.frame-geometry.rail)<1,JSON.stringify(geometry));
      await appIsUncovered();
      assert.equal(archiveReads,0,'covered dashboard must not start archive reads');
    };
    await open(); // no saved app catalog
    await open(); // saved app starts before the body exists
    assert.equal(await page.evaluate(()=>document.getElementById('cccMobileAppFrame').parentElement.tagName),'HTML');
    await page.evaluate(()=>{
      window.testDashboard=document;
      window.testDraft=document.getElementById('convInput');
      window.testDraft.value='Unsent local test draft';
      window.cccOpenApp('sessions');
    });
    assert.equal(await page.$eval('#cccMobileAppFrame',frame=>frame.hidden),true);
    await page.waitForFunction(()=>document.getElementById('convList'));
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.ok(archiveReads>0,'Sessions resumes the deferred archive read');
    assert.equal(await page.$eval('#cccLoadingOverlay',overlay=>{
      overlay.classList.remove('gone','fade-out');
      return getComputedStyle(overlay).display;
    }),'flex',
      'returning to a still-loading dashboard restores its loading state');
    await page.evaluate(()=>window.cccOpenApp('shell-test'));
    assert.equal(await page.evaluate(()=>document===window.testDashboard
      && window.testDraft.isConnected && window.testDraft.value==='Unsent local test draft'
      && document.getElementById('cccMobileAppFrame').contentDocument===window.testAppDocument),true);
    await page.setViewport({width:390,height:844});
    await page.waitForFunction(()=>document.body.classList.contains('has-mobile-bottom-nav'));
    const phone=await page.evaluate(()=>({frame:document.getElementById('cccMobileAppFrame').getBoundingClientRect().bottom,
      nav:document.getElementById('mobileBottomNav').getBoundingClientRect().top}));
    assert.ok(phone.frame<=phone.nav+1,JSON.stringify(phone));
    await appIsUncovered();
    // A removed app in the saved catalog must uncover the dashboard and
    // release the same read waiter even when its early frame is under HTML.
    removedApp=true;
    archiveReads=0;
    await page.goto(base+'/?ccc_app=shell-test',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.getElementById('cccMobileAppFrame')?.contentDocument?.querySelector('h1'));
    await page.evaluate(()=>document.getElementById('cccMobileAppFrame').contentWindow.eval(
      "parent.postMessage({type:'ccc:app-fully-ready'},location.origin)"));
    await page.waitForFunction(()=>!document.documentElement.classList.contains('ccc-mobile-app-open')
      && !document.getElementById('cccMobileAppFrame'));
    await page.waitForFunction(()=>document.getElementById('convList'));
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.ok(archiveReads>0,'a removed app releases archive reads');
    assert.deepEqual(errors,[]);
    console.log('PASS: fresh/saved app starts, deferred dashboard, frame adoption, draft and phone geometry');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
