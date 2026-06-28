const puppeteer = require('puppeteer');
(async () => {
  const b = await puppeteer.launch({headless:'new', args:['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width:1400, height:900});
  await p.goto('http://127.0.0.1:8090/throughput', {waitUntil:'domcontentloaded'});
  await p.waitForSelector('#weekly-banner', {visible:true, timeout:15000}).catch(()=>console.log('banner never visible'));
  await new Promise(r=>setTimeout(r,600));
  const pct = await p.$eval('#weekly-pct', e=>e.textContent).catch(()=>'(none)');
  const sub = await p.$eval('#weekly-sub', e=>e.innerText).catch(()=>'(none)');
  console.log('banner pct:', pct, '| sub:', JSON.stringify(sub));
  await p.screenshot({path:'/tmp/wk-banner.png'});
  await b.close();
})();
