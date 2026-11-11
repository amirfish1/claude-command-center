// Render actual isolated CLI output as a labelled transcript, not a terminal UI.
const puppeteer = require('../require-puppeteer.js');
const fs = require('fs');
const path = require('path');
const sensitive = require('./oncall-capture-safety.cjs');
const output = path.join(__dirname, '../.claude/screenshots/launch-captures');
(async () => {
  const data = JSON.parse(fs.readFileSync(0, 'utf8'));
  const transcript = '$ ccc oncall setup --set server=[local ntfy server]\n' + data.setup + '\n$ ccc oncall minor on\n' + data.minor + '\n$ ccc oncall decisions\n' + data.decisions;
  if (sensitive(transcript)) throw Error('Unredacted address or local path in CLI output');
  fs.mkdirSync(output, {recursive:true});
  const browser = await puppeteer.launch({headless:true, executablePath:process.env.PUPPETEER_EXECUTABLE_PATH,
    args:['--no-sandbox','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND']});
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', req => req.abort());
    await page.setContent('<!doctype html><meta charset="utf-8"><style>body{background:#111b22;color:#e7f1f0;font:15px/1.45 monospace;padding:20px}h1{font:600 18px system-ui}p{font:14px system-ui;color:#abc}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Actual CLI output</h1><p>Rendered transcript; defaults supplied on stdin; preview enabled by environment. Temporary paths and local server address redacted. The harness submits any sample minor decision; this is not evidence of an agent making it.</p><pre></pre>');
    await page.$eval('pre', (el, text) => { el.textContent = text; }, transcript);
    for (const [name,width,height] of [['cli-setup-desktop.png',1440,900], ['cli-setup-phone.png',390,844]]) {
      await page.setViewport({width,height,deviceScaleFactor:width === 390 ? 3 : 2});
      await page.screenshot({path:path.join(output,name),fullPage:true});
    }
    const agentFlag = process.env.PROOF_WITH_AGENT === '1' ? ' --with-agent' : '';
    fs.writeFileSync(path.join(output,'cli-provenance.json'), JSON.stringify({source:'scripts/oncall-proof.py' + agentFlag + ' --capture',description:'Actual setup, minor on, and decisions output rendered as text; temporary paths and local server address redacted.'},null,2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
