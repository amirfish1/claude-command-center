// Render only the generated SVG on stdin. No browser network access.
const fs = require('fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  let browser;
  try {
    const svg = fs.readFileSync(0,'utf8');
    const dimensions = svg.match(/width="(1080|1200)" height="(675|1080)"/);
    if (!dimensions) throw new Error('Invalid card dimensions');
    browser = await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request',req => req.abort());
    await page.setViewport({width:Number(dimensions[1]),height:Number(dimensions[2]),deviceScaleFactor:1});
    await page.setContent('<html><body style="margin:0">'+svg+'</body></html>');
    process.stdout.write(await page.screenshot({type:'png'}));
  } finally {if(browser) await browser.close();}
})().catch(e => {console.error(e.message);process.exitCode=1;});
