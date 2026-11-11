// Capture only the real, isolated ntfy web app used by oncall-session-proof.py.
const puppeteer = require('../require-puppeteer.js');
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const {URL} = require('url');
const sensitive = require('./oncall-capture-safety.cjs');
const [base, topic] = process.argv.slice(2);
const parsed = new URL(base);
if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !/^[a-z0-9-]+$/.test(topic)) throw Error('Expected isolated loopback ntfy');
const out = path.join(__dirname, '../.claude/screenshots/launch-captures');
fs.mkdirSync(out, {recursive:true});
let browser, page, dashboard;
const shots = [];
async function shot(name, width, height, caption) {
  await page.setViewport({width, height, deviceScaleFactor:width === 390 ? 3 : 2});
  const visible = await page.evaluate(() => document.body.innerText);
  if (sensitive(visible)) {
    throw Error('Visible address or local path in screenshot; refusing capture');
  }
  await page.screenshot({path:path.join(out,name)});
  shots.push({name, caption, width, height, scale:width === 390 ? 3 : 2});
}
async function act(cmd) {
  if (cmd.step === 'open') {
    browser = await puppeteer.launch({headless:true, executablePath:process.env.PUPPETEER_EXECUTABLE_PATH,
      args:['--no-sandbox','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1']});
    page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', req => { const u = new URL(req.url());
      if ((u.protocol === 'http:' || u.protocol === 'ws:') && u.hostname === '127.0.0.1' && u.port === parsed.port) req.continue();
      else if (u.protocol === 'data:' || u.protocol === 'blob:') req.continue();
      else req.abort();
    });
    await page.setViewport({width:390,height:844});
    await page.goto(base + '/' + topic, {waitUntil:'domcontentloaded'});
    await page.waitForSelector('input[placeholder="Type a message here"]', {timeout:30000});
  } else if (cmd.step === 'session') {
    const dashboardUrl = new URL(cmd.base);
    if (dashboardUrl.protocol !== 'http:' || dashboardUrl.hostname !== '127.0.0.1') throw Error('Dashboard must use loopback');
    dashboard = await browser.newPage();
    await dashboard.setViewport({width:1440,height:900,deviceScaleFactor:2});
    await dashboard.setRequestInterception(true);
    dashboard.on('request', req => { const u = new URL(req.url());
      if ((u.protocol === 'http:' || u.protocol === 'ws:') && u.hostname === '127.0.0.1' && u.port === dashboardUrl.port) req.continue();
      else if (u.protocol === 'data:' || u.protocol === 'blob:') req.continue();
      else req.abort();
    });
    await dashboard.goto(cmd.base, {waitUntil:'domcontentloaded'});
    const selector = '.conv-item[data-session-id="' + cmd.sid + '"]';
    await dashboard.waitForSelector(selector, {timeout:30000});
    await dashboard.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Not now')?.click());
    await dashboard.click(selector);
    await dashboard.waitForFunction(() => [...document.querySelectorAll('#conversationsView .assistant-text')].some(e => e.innerText.includes('Should I use the blue option?')), {timeout:30000});
    const view = await dashboard.$('#conversationsView');
    if (sensitive(await dashboard.evaluate(e => e.innerText, view))) throw Error('Address or local path in session transcript');
    await view.screenshot({path:path.join(out,'ccc-session-question.png')});
    shots.push({name:'ccc-session-question.png',caption:'Cropped real CCC session transcript showing its question.'});
  } else if (cmd.step === 'session_reply') {
    await dashboard.waitForFunction(text => document.querySelector('#conversationsView')?.innerText.includes(text), {timeout:30000}, cmd.text);
    const view = await dashboard.$('#conversationsView');
    if (sensitive(await dashboard.evaluate(e => e.innerText, view))) throw Error('Address or local path in session transcript');
    await view.screenshot({path:path.join(out,'ccc-session-reply.png')});
    shots.push({name:'ccc-session-reply.png',caption:'Cropped real CCC session transcript after the ntfy reply.'});
  } else if (cmd.step === 'reply') {
    await page.waitForFunction(() => document.body.innerText.includes('Should I use the blue option?'), {timeout:30000});
    await shot('session-alert-phone.png',390,844,'Real ntfy web alert on a phone viewport.');
    await shot('session-alert-desktop.png',1440,900,'The same received alert on a desktop viewport.');
    await page.goto(base + '/' + topic + '-reply', {waitUntil:'domcontentloaded'});
    await page.setViewport({width:390,height:844});
    await page.waitForSelector('input[placeholder="Type a message here"]');
    // The real help command is handled independently of any pending alert.
    // It also avoids ntfy's empty-state help showing the local server address.
    await page.type('input[placeholder="Type a message here"]','help');
    await page.click('button[aria-label="Publish message"]');
    await page.waitForFunction(() => document.body.innerText.includes('help'), {timeout:30000});
    await page.type('input[placeholder="Type a message here"]',cmd.text,{delay:35});
    await shot('session-reply-typed-phone.png',390,844,'Reply typed in the real ntfy reply topic.');
    await page.click('button[aria-label="Publish message"]');
    await page.waitForFunction(text => document.body.innerText.includes(text), {timeout:30000}, cmd.text);
    await shot('session-reply-sent-phone.png',390,844,'Reply published in the real ntfy web app.');
    await shot('session-reply-sent-desktop.png',1440,900,'Published reply on a desktop viewport.');
  } else if (cmd.step === 'finish') {
    const cards = shots.map(s => `<figure><img src="${s.name}" alt=""><figcaption>${s.caption}</figcaption></figure>`).join('\n');
    fs.writeFileSync(path.join(out,'session-index.html'), `<!doctype html><meta charset="utf-8"><title>On-call capture evidence</title><h1>On-call capture evidence</h1><p>Real local ntfy web app and a real isolated CCC/Codex session. Phone: 390 × 844 at 3×; desktop: 1440 × 900 at 2×. The session proof verifies the reply reached the agent and closed its alert. Screens are captured from one proof run; they are not a continuous film.</p><style>body{font:16px system-ui;margin:2rem;background:#111;color:#eee}main{display:flex;flex-wrap:wrap;gap:1rem}figure{margin:0;background:#222;padding:1rem;max-width:600px}img{max-width:100%;max-height:600px}figcaption{padding-top:.5rem}</style><main>${cards}</main>`);
    fs.writeFileSync(path.join(out,'session-provenance.json'),JSON.stringify({source:'scripts/oncall-session-proof.py --with-agent --capture',service:'local ntfy v2.17.0',model:'gpt-6.1-terra',effort:'medium',screenshots:shots},null,2));
    await browser.close();
  } else throw Error('Unknown capture step');
}
const input = readline.createInterface({input:process.stdin});
let chain = Promise.resolve();
input.on('line', line => { chain = chain.then(async () => {
  try { await act(JSON.parse(line)); console.log(JSON.stringify({ok:true})); }
  catch (e) { console.log(JSON.stringify({ok:false,error:String(e)})); }
}); });

input.on('close', () => { chain.finally(async () => { if (browser) await browser.close(); }); });
