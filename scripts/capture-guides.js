// Capture real local result pages. No external page or browser profile is used.
const fs = require('fs');
const {spawn} = require('child_process');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const [mode, target, output] = process.argv.slice(2);
(async () => {
  let browser, demo;
  try {
    browser = await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
    const page = await browser.newPage();
    await page.setViewport({width:390,height:844,deviceScaleFactor:1});
    await page.setRequestInterception(true);
    page.on('request', req => {
      const url = new URL(req.url());
      ['localhost','127.0.0.1'].includes(url.hostname) || ['data:','blob:'].includes(url.protocol) ? req.continue() : req.abort();
    });
    let url = target;
    if (mode === 'demo') {
      // A private PTY lets the actual CLI use its browser phone. BROWSER writes
      // the capability URL to stdout instead of opening a personal browser.
      const opener = process.env.HOME + '/open-demo';
      fs.writeFileSync(opener, '#!/bin/sh\nprintf "GUIDE_PHONE=%s\\n" "$1"\n', {mode:0o700});
      demo = spawn('script',['-q','-c','ccc oncall demo --fast','/dev/null'], {
        env:{...process.env,TERM:'xterm',BROWSER:opener},stdio:['pipe','pipe','pipe']});
      url = await new Promise((resolve,reject) => {
        let text='';const timer=setTimeout(()=>reject(Error('Demo phone URL timeout')),20000);
        demo.stdout.on('data', data => {text+=data;const m=text.match(/Local phone: (http:\/\/127\.0\.0\.1:\d+\/#[A-Za-z0-9_-]+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
        demo.on('exit',code=>{if(code)reject(Error('Demo exited: '+code+' '+text));});
      });
    }
    await page.goto(url,{waitUntil:'networkidle2',timeout:30000});
    if (mode === 'ntfy') {
      // Topic routes subscribe through the real ntfy web app and read its cache.
      await page.waitForFunction(() => document.body.innerText.includes('This is a test') ||
        document.body.innerText.includes('empty cart') || document.body.innerText.includes('Yesterday:'), {timeout:20000});
      // Close the one-time notification permission prompt if the browser shows it.
      await page.evaluate(() => {
        const buttons=[...document.querySelectorAll('button')];
        const later=buttons.find(b=> /^(later|not now|maybe later)$/i.test(b.textContent.trim()));
        if(later)later.click();
      });
    }
    if (mode === 'decisions') {
      await page.waitForFunction(() => document.body.innerText.includes('Ignore generated Python cache files.'),{timeout:20000});
      const list = await page.evaluateHandle(() => [...document.querySelectorAll('.oncall-list')].find(e=>e.textContent.includes('Ignore generated Python cache files.')));
      await list.asElement().screenshot({path:output});
    } else {
      if(mode==='demo') await page.waitForFunction(()=>document.body.innerText.includes('Show zero or hide'),{timeout:20000});
      await page.screenshot({path:output});
    }
    if (mode==='demo') {
      await page.evaluate(()=>[...document.querySelectorAll('#choices button')].find(b=>b.textContent==='zero').click());
      await page.waitForFunction(()=>[...document.querySelectorAll('#choices button')].some(b=>b.textContent==='yes'),{timeout:20000});
      await page.evaluate(()=>[...document.querySelectorAll('#choices button')].find(b=>b.textContent==='yes').click());
      await new Promise((resolve,reject)=>{if(demo.exitCode!==null){return demo.exitCode===0?resolve():reject(Error('Demo exit '+demo.exitCode));}demo.once('exit',code=>code===0?resolve():reject(Error('Demo exit '+code)));setTimeout(()=>reject(Error('Demo completion timeout')),10000).unref();});
    }
    if(fs.statSync(output).size>=300000)throw Error('Capture exceeds 300 KB');
    console.log('Captured real local result: '+mode);
  } finally {
    if(demo && demo.exitCode===null)demo.kill('SIGTERM');
    if(browser)await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
