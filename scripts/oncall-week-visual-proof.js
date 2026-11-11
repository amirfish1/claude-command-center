// Batch visual check of generated private artifacts. No network or live app.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  let browser;
  const output = path.resolve(process.argv[2]);
  const requests = [], errors = [];
  try {
    browser = await puppeteer.launch({executablePath:findChromePath(),headless:true,args:['--no-sandbox']});
    const page = await browser.newPage();
    page.on('pageerror',e => errors.push(e.message));
    await page.setRequestInterception(true);
    page.on('request',req => {requests.push(req.url());req.abort();});
    for (const file of fs.readdirSync(output).filter(f => /\.(svg|html)$/.test(f))) {
      const content = fs.readFileSync(path.join(output,file),'utf8');
      const svg = file.endsWith('.svg');
      for (const width of [1200,390]) {
        await page.setViewport({width,height:svg?1080:900,deviceScaleFactor:1});
        await page.setContent(svg ? '<html><body style="margin:0"><style>svg{display:block;max-width:100%;height:auto}</style>'+content+'</body></html>' : content);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),file+' overflow');
        if (svg) {
          const bad = await page.evaluate(() => {
            const limit = document.querySelector('svg').viewBox.baseVal;
            return [...document.querySelectorAll('text')].filter(e => {
              const b=e.getBBox();return b.x<0 || b.x+b.width>limit.width-24 || b.y+b.height>limit.height;
            }).map(e=>e.textContent);
          });
          assert.deepEqual(bad,[],file+' text outside canvas');
        } else {
          assert.equal(await page.$$eval('section',els=>els.length),8);
        }
        const capture = path.join(output,file.replace(/\.(svg|html)$/,'')+(svg?'-card':'-page')+`-${width}.png`);
        if (svg) await (await page.$('svg')).screenshot({path:capture});
        else await page.screenshot({path:capture,fullPage:true});
      }
    }
    assert.deepEqual(errors,[]);
    assert.deepEqual(requests,[]);
    const png=fs.readFileSync(path.join(output,'month-dark-square-export.png'));
    assert.equal(png.readUInt32BE(16),1080);assert.equal(png.readUInt32BE(20),1080);
    console.log('PASS: all themes and shapes at 390 and 1200; no overflow, page errors or external requests; PNG is 1080 square.');
  } finally {if(browser)await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
