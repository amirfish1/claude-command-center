// Check the real static page without reaching external services.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const out = path.resolve(__dirname,'../.claude/screenshots/launch-captures');
    fs.mkdirSync(out,{recursive:true});
    for (const width of [390,1440]) {
      const page=await browser.newPage();
      await page.setViewport({width,height:900,deviceScaleFactor:width===390?3:2});
      await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
      await page.setRequestInterception(true);
      page.on('request',req=>req.url().startsWith('file:')?req.continue():req.abort());
      await page.goto(pathToFileURL(path.resolve(__dirname,'../docs/index.html')).href);
      const geometry=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,hero:document.querySelector('.hero').scrollWidth}));
      assert.equal(geometry.document,width,JSON.stringify(geometry));
      assert.equal(geometry.hero,width,JSON.stringify(geometry));
      await page.screenshot({path:path.join(out,`hero-layout-${width}.png`)});
      console.log('PASS hero layout',JSON.stringify(geometry));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
