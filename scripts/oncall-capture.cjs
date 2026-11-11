// Drive only real isolated ntfy/CCC pages. IPC is sequential; one browser per proof.
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const {Recording, outputDir, safeText, sleep, capturePixels} = require('./oncall-real-recording.cjs');
const [base, topic] = process.argv.slice(2);
const parsed = new URL(base);
if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !/^[a-z0-9-]+$/.test(topic)) throw Error('Expected isolated loopback ntfy');
const puppeteer = require('../require-puppeteer.js');
const out=outputDir(), shots=[];
let browser, page, dashboard, recording;
const phone={width:390,height:844,deviceScaleFactor:3};
const desktop={width:1440,height:900,deviceScaleFactor:2};
async function localPage(url, viewport, ccc=false) {
  const origin=new URL(url);
  if (origin.protocol!=='http:' || origin.hostname!=='127.0.0.1') throw Error('Expected loopback app');
  const p=await browser.newPage();
  await p.setViewport(viewport);
  await p.bringToFront();
  await p.emulateMediaFeatures([{name:'prefers-color-scheme',value:'light'}]);
  if (ccc) await p.evaluateOnNewDocument(() => {
    localStorage.setItem('ccc-theme','light');
    if (new URL(location.href).searchParams.get('oncall-home')!=='1') sessionStorage.setItem('ccc-oncall-home-closed','1');
  });
  await p.setRequestInterception(true);
  p.on('request', req => { const u=new URL(req.url());
    if (['http:','ws:'].includes(u.protocol) && u.hostname==='127.0.0.1' && u.port===origin.port) req.continue();
    else if (['data:','blob:'].includes(u.protocol)) req.continue(); else req.abort();
  });
  const targetUrl=new URL(url);
  if (ccc) targetUrl.searchParams.set('onboarding','1');
  await p.goto(targetUrl.href,{waitUntil:'domcontentloaded'});
  if (ccc) {
    // Dismiss the real first-run UI through its own control, never hide it.
    const welcome = await p.waitForSelector('.mz-overlay.open .mz-btn-ghost', {visible:true,timeout:60000});
    if (welcome) { await p.evaluate(() => document.querySelector('.mz-overlay.open .mz-btn-ghost')?.click()); await p.waitForFunction(() => !document.querySelector('.mz-overlay.open'), {polling:100,timeout:10000}); }
    // Consent is queued after welcome and may appear later. Decline through
    // its real control whenever it appears; never hide or replace the UI.
    await p.evaluate(() => {
      const dismiss=()=>document.querySelector('#cfgConsentModal.open [data-role=cfgc-later]')?.click();
      new MutationObserver(dismiss).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
      dismiss();
    });
    await p.waitForFunction(() => !document.querySelector('.upd-overlay.open'), {polling:100,timeout:10000});
  }
  return p;
}
async function light(p, selector='body') {
  // Verify actual theme; never recolour the DOM for a capture.
  const color=await p.$eval(selector, el => {
    while(el) { const c=getComputedStyle(el).backgroundColor; if(c!=='rgba(0, 0, 0, 0)' && c!=='transparent') return c; el=el.parentElement; }
    return 'rgb(255,255,255)';
  });
  const rgb=color.match(/[\d.]+/g)?.slice(0,3).map(Number);
  if (!rgb || rgb.reduce((a,b)=>a+b,0)<384) throw Error('App did not resolve to supported light theme: '+color);
}
async function shot(p,name,caption,selector=null) {
  await safeText(p,selector);
  fs.writeFileSync(path.join(out,name),await capturePixels(p,selector));
  shots.push({file:name,caption,viewport:p.viewport(),crop:selector,utc:new Date().toISOString(),
    run_id:process.env.PROOF_RUN_ID,kind:'real-browser-screenshot'});
}
async function start(name,p,selector=null) {
  if(recording) throw Error('Only one actual frame capture at a time');
  await p.bringToFront();
  await light(p,selector || 'body');
  recording=new Recording(out,name,p,selector);
}
async function stop() {
  if(!recording) return;
  const r=recording; recording=null; return await r.stop();
}
async function reply(text, prefix) {
  await page.goto(base+'/'+topic+'-reply',{waitUntil:'domcontentloaded'});
  await start(prefix==='fixed'?'fixed-approval':'session-phone',page);
  await sleep(1000);
  const input='input[placeholder="Type a message here"]';
  await page.waitForSelector(input);
  await page.type(input,text,{delay:140});
  await shot(page,prefix+'-reply-typed-phone.png','Numbered reply typed in real local ntfy.');
  await sleep(1500);
  const sent_at=new Date().toISOString();
  recording?.event('phone-send',{text,sent_at});
  await page.focus(input);
  await page.keyboard.press('Enter');
  await page.waitForFunction(t => document.body.innerText.includes(t),{polling:100,timeout:30000},text);
  await shot(page,prefix+'-reply-sent-phone.png','Reply published through the real ntfy input and button.');
  return {sent_at,text};
}
async function act(cmd) {
  if(cmd.step==='open') {
    browser=await puppeteer.launch({headless:true,executablePath:process.env.PUPPETEER_EXECUTABLE_PATH,
      args:['--no-sandbox','--disable-background-timer-throttling','--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1']});
    page=await localPage(base+'/'+topic,phone);
    await page.waitForSelector('input[placeholder="Type a message here"]',{polling:100,timeout:30000});
    // Prime the actual reply topic before recording, so its empty-state URL is never in frame.
    await page.goto(base+'/'+topic+'-reply',{waitUntil:'domcontentloaded'});
    await page.waitForSelector('input[placeholder="Type a message here"]');
    await page.type('input[placeholder="Type a message here"]','help');
    await page.click('button[aria-label="Publish message"]');
    await page.waitForFunction(() => document.body.innerText.includes('help'),{polling:100,timeout:30000});
    await page.bringToFront();
    await page.goto(base+'/'+topic,{waitUntil:'domcontentloaded'});
  } else if(cmd.step==='report_open') {
    dashboard=await localPage(cmd.base+'/?oncall-home=1',desktop,true);
    await dashboard.waitForSelector('#oncallHome:not([hidden])',{polling:100,timeout:30000});
    await shot(dashboard,'report-ready-desktop.png','Real CCC before the sample report.');
    await start('report-fix',dashboard,'#oncallHome');
    recording.event('before-report');
  } else if(cmd.step==='mark') {
    recording?.event(cmd.kind,cmd.details || {});
  } else if(cmd.step==='report_fixed') {
    await dashboard.waitForFunction(() => document.querySelector('.oncall-home-card.is-done'),{polling:100,timeout:30000});
    recording.event('fixed-visible');
    await shot(dashboard,'report-fixed-desktop.png','Real CCC On-call home after the verified local agent fix.');
    await sleep(Math.max(3000,17000-(performance.now()-recording.started))); await stop();
    // The still is full desktop at 2x; footage crops to the real On-call region.
    await dashboard.setViewport(phone);
    await shot(dashboard,'report-fixed-phone.png','Same real CCC On-call home at phone dimensions.');
  } else if(cmd.step==='fixed_reply') {
    await page.bringToFront();
    await page.goto(base+'/'+topic,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => /fixed/i.test(document.body.innerText),{polling:100,timeout:30000}).catch(async e => { throw Error(e.message + ': ntfy visible text: '+await page.$eval('body',e=>e.innerText)); });
    await shot(page,'fixed-alert-phone.png','Real local ntfy fixed notice awaiting reporter approval.');
    await start('fixed-approval-alert',page); await sleep(3500); await stop();
    const result=await reply(cmd.text,'fixed');
    await sleep(6000); await stop(); return result;
  } else if(cmd.step==='fixed_result') {
    await page.bringToFront();
    await page.goto(base+'/'+topic,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('input[placeholder="Type a message here"]');
    await page.waitForFunction(() => document.body.innerText.includes('Sent to reporter@example.test'),{polling:100,timeout:30000});
    await start('fixed-result',page); await sleep(9000);
    await shot(page,'fixed-result-phone.png','Actual ntfy thread after approval; mailbox delivery is checked separately, not depicted as webmail.');
    await stop();
  } else if(cmd.step==='session') {
    dashboard=await localPage(cmd.base,desktop,true);
    await dashboard.evaluate(() => [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Not now')?.click());
    const selector='.conv-item[data-session-id="'+cmd.sid+'"]';
    await dashboard.waitForSelector(selector,{polling:100,timeout:30000}); await dashboard.click(selector);
    await dashboard.waitForFunction(() => [...document.querySelectorAll('#conversationsView .assistant-text')].some(e=>e.innerText.includes('Should I use the blue option?')),{polling:100,timeout:30000});
    await light(dashboard);
    await shot(dashboard,'ccc-session-question.png','Real CCC question. Transcript crop excludes the sidebar.','#conversationsView');
    await page.waitForFunction(() => document.body.innerText.includes('Should I use the blue option?'),{polling:100,timeout:30000});
    await shot(page,'session-alert-phone.png','Actual local ntfy question at 390x844, 3x.');
    await page.setViewport(desktop);
    await shot(page,'session-alert-desktop.png','Same actual local ntfy question at 1440x900, 2x.');
    await page.setViewport(phone);
  } else if(cmd.step==='reply') {
    await start('session-alert',page); await sleep(4500); await stop();
    const result=await reply(cmd.text,'session');
    await sleep(5000); await stop(); return result;
  } else if(cmd.step==='session_reply') {
    // The page pauses transcript refresh while hidden. Its raw-text attribute
    // excludes the service badge that contributes extra rendered textContent.
    await dashboard.bringToFront();
    await dashboard.waitForFunction(text => [...document.querySelectorAll('#conversationsView .event.user_text:not(.pending) .user-msg')]
      .some(e=>{const raw=(e.dataset.rawText || e.textContent).trim();return raw===text || raw.endsWith('\n'+text);}),{polling:100,timeout:45000},cmd.text)
      .catch(async e=>{throw Error(e.message+': user bubbles '+JSON.stringify(await dashboard.$$eval('#conversationsView .user-msg',els=>els.map(e=>e.dataset.rawText || e.textContent))));});
    await dashboard.evaluate(text => [...document.querySelectorAll('#conversationsView .event.user_text:not(.pending) .user-msg')]
      .find(e=>(e.dataset.rawText || e.textContent).trim().endsWith(text))?.scrollIntoView({block:'center'}),cmd.text);
    await start('session-transcript',dashboard,'#conversationsView');
    recording.event('verified-new-user-turn',{answer:cmd.text,transcript_timestamp:cmd.timestamp});
    await shot(dashboard,'ccc-session-reply.png','Real CCC transcript showing the new user reply, verified against post-send JSONL.','#conversationsView');
    await sleep(10000); await stop();
  } else if(cmd.step==='finish') {
    await stop();
    fs.writeFileSync(path.join(out,'browser-provenance.json'),JSON.stringify({run_id:process.env.PROOF_RUN_ID,
      source:'actual local ntfy and CCC browser pixels',model:process.env.PROOF_MODEL,effort:'medium',screenshots:shots},null,2));
    await browser.close(); browser=null;
  } else throw Error('Unknown capture step');
}
const input=readline.createInterface({input:process.stdin});
let chain=Promise.resolve();
input.on('line',line=>{chain=chain.then(async()=>{
  try {const result=await act(JSON.parse(line)); console.log(JSON.stringify({ok:true,result}));}
  catch(e) {console.log(JSON.stringify({ok:false,error:e.stack || String(e)}));}
});});
input.on('close',()=>{chain.finally(async()=>{try {await stop();} finally {if(browser) await browser.close();}}).catch(e=>{console.error(e);process.exitCode=1;});});
