// Optional local browser capture. Nothing is fetched outside the demo server.
const fs = require('fs'), path = require('path');
const {performance} = require('perf_hooks');
const [root,url,frames] = process.argv.slice(2);
const puppeteer = require(path.join(root,'require-puppeteer.js'));
const {findChromePath} = require(path.join(root,'puppeteer-browser-config.js'));
(async()=>{
 let browser;
 try {
  browser=await puppeteer.launch({headless:true,executablePath:findChromePath(),args:['--no-sandbox','--no-proxy-server']});
  const page=await browser.newPage();
  page.on('pageerror',error=>console.error('Page:',error.message));
  page.on('requestfailed',request=>console.error('Request:',request.url().split('#')[0],request.failure().errorText));
  await page.setRequestInterception(true);
  page.on('request',request=>new URL(request.url()).origin===new URL(url).origin?request.continue():request.abort());
  await page.setViewport({width:800,height:960});await page.goto(url);
  await page.waitForSelector('#messages');
  const start=performance.now(), shots=[];let choiceSince=0,lastChoice='',clicked='',doneAt=0;
  process.stdout.write('READY\n');
  while(performance.now()-start<120000){
   const now=performance.now();
   const state=await page.evaluate(()=>({
    choices:[...document.querySelectorAll('#choices button')].filter(b=>!b.disabled).map(b=>b.textContent),
    done:document.querySelector('#status').textContent.includes('Demo complete')}));
   const wanted=state.choices.includes('zero')?'zero':state.choices.includes('decline')?'decline':state.choices.includes('yes')?'yes':'';
   if(wanted!==lastChoice){choiceSince=now;lastChoice=wanted;}
   // Leave time to read each decision, especially the money hard stop.
   if(wanted && wanted!==clicked && now-choiceSince>(wanted==='decline'?6500:4000)){
    await page.evaluate(text=>[...document.querySelectorAll('#choices button')].find(b=>b.textContent===text).click(),wanted);clicked=wanted;
   }
   const name=`frame-${String(shots.length).padStart(5,'0')}.png`;
   await page.screenshot({path:path.join(frames,name)});shots.push({file:name,at:(performance.now()-start)/1000});
   if(state.done){if(!doneAt)doneAt=now;if(now-doneAt>2000)break;}
   await new Promise(resolve=>setTimeout(resolve,Math.max(0,200-(performance.now()-now))));
  }
  if(!doneAt)throw Error('Browser demo did not finish within two minutes');
  fs.writeFileSync(path.join(frames,'frames.json'),JSON.stringify(shots));
 }finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
