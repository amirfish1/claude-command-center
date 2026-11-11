// Capture the actual isolated WatchTower queue page at each proof milestone.
const puppeteer = require('../require-puppeteer.js');
const fs = require('fs');
const path = require('path');
const os = require('os');
const sensitive = require('./oncall-capture-safety.cjs');
const [base, queue, ref, state] = process.argv.slice(2);
const url = new URL(base);
if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !/^[A-Z0-9_-]+$/.test(queue) || !/^[A-Z0-9_-]+-\d+$/.test(ref) || !['reported','fixed'].includes(state)) throw Error('Invalid isolated queue capture');
const fixture = process.argv.includes('--fixture');
const prefix = fixture ? 'fixture-ticket-' : 'ticket-';
const output = path.join(__dirname, '../.claude/screenshots/launch-captures');
(async () => {
  const browser = await puppeteer.launch({headless:true, executablePath:process.env.PUPPETEER_EXECUTABLE_PATH,
    args:['--no-sandbox','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:900});
    await page.setRequestInterception(true);
    page.on('request', req => { const u = new URL(req.url());
      if (u.protocol === 'http:' && u.hostname === '127.0.0.1' && u.port === url.port) req.continue();
      else if (u.protocol === 'data:' || u.protocol === 'blob:') req.continue(); else req.abort();
    });
    await page.goto(base + '/q/' + encodeURIComponent(queue), {waitUntil:'domcontentloaded'});
    const selector = state === 'fixed' ? '.crow' : '.trow';
    await page.waitForFunction((sel, ticket) => [...document.querySelectorAll(sel)].some(e => e.innerText.includes(ticket)), {timeout:30000}, selector, ref);
    const text = await page.evaluate(() => document.body.innerText);
    if (sensitive(text) || (os.hostname().length > 3 && text.toLowerCase().includes(os.hostname().toLowerCase()))) throw Error('Sensitive address, path or hostname visible in ticket UI');
    fs.mkdirSync(output,{recursive:true});
    for (const [kind,width,height,scale] of [['desktop',1440,900,2],['phone',390,844,3]]) {
      await page.setViewport({width,height,deviceScaleFactor:scale});
      await page.screenshot({path:path.join(output,prefix + state + '-' + kind + '.png')});
    }
    fs.writeFileSync(path.join(output,prefix + state + '-provenance.json'), JSON.stringify({source:'scripts/oncall-proof.py --capture' + (fixture ? '' : ' --with-agent'),fixture,service:'isolated local WatchTower queue UI',state,ref},null,2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
