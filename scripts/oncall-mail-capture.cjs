// GreenMail has no webmail UI: these are labelled decoded RFC822 fields.
const puppeteer = require('../require-puppeteer.js');
const fs = require('fs');
const path = require('path');
const sensitive = require('./oncall-capture-safety.cjs');
const out = path.join(__dirname, '../.claude/screenshots/launch-captures');
(async () => {
  const data = JSON.parse(fs.readFileSync(0, 'utf8'));
  const fields = ['From: '+data.from, 'To: '+data.to, 'Subject: '+data.subject, '', data.body].join('\n');
  // Only RFC-reserved example addresses may appear in this local mail proof.
  if (sensitive(fields.replace(/[\w.+-]+@example\.test\b/g, '[example address]'))) throw Error('Unexpected private data in mail');
  const browser = await puppeteer.launch({executablePath:process.env.PUPPETEER_EXECUTABLE_PATH,
    args:['--no-sandbox','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND']});
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', req => req.abort());
    await page.setContent('<!doctype html><meta charset="utf-8"><style>body{background:#f8fafb;color:#15202b;font:17px/1.6 system-ui;margin:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}p{color:#465568}</style><h1>Fixed notice</h1><p id="label"></p><pre></pre>');
    await page.$eval('#label', (el, agent) => {el.textContent='Decoded real email from the local GreenMail mailbox. Example addresses. ' + (agent ? 'The agent proof verified the fix.' : 'Protocol fixture: the harness closed the ticket. No agent fixed code in this run.');}, data.agent);
    await page.$eval('pre', (el, text) => {el.textContent=text;}, fields);
    fs.mkdirSync(out,{recursive:true});
    for (const [kind,width,height,scale] of [['phone',390,844,3],['desktop',1440,900,2]]) {
      await page.setViewport({width,height,deviceScaleFactor:scale});
      await page.screenshot({path:path.join(out,`mail-fixed-${kind}.png`),fullPage:true});
    }
    fs.writeFileSync(path.join(out,'mail-fixed.eml'),data.source,{mode:0o600});
    fs.writeFileSync(path.join(out,'mail-provenance.json'),JSON.stringify({source:'scripts/oncall-proof.py --capture' + (data.agent ? ' --with-agent' : ''),agent:data.agent,description:'Decoded actual RFC822 message from local GreenMail. Not a webmail UI.'},null,2));
  } finally {await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});
