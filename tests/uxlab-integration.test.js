'use strict';
// Opt-in real Chromium checks: nice -n 15 node --test tests/uxlab-integration.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {BrowserSession} = require('../uxlab/browser');
const listen = (server, host) => new Promise(resolve => server.listen(0, host, resolve));
const close = server => new Promise(resolve => server.close(resolve));

test('real browser enforces host, click, form and touch boundaries', {timeout: 60000}, async () => {
  let escaped = 0, submitted = '', paid = 0, touched = false;
  const outside = http.createServer((req, res) => {escaped++; res.end('outside');});
  await listen(outside, '127.0.0.2');
  const external = `http://127.0.0.2:${outside.address().port}`;
  const app = http.createServer((req, res) => {
    if (req.url === '/redirect') {res.writeHead(302, {Location: external}); res.end(); return;}
    if (req.url === '/pay') paid++;
    if (req.url === '/touch') touched = true;
    if (req.url === '/form') {req.on('data', chunk => submitted += chunk); req.on('end', () => res.end('Saved fake form')); return;}
    res.setHeader('Content-Type', 'text/html');
    res.end(`<meta name="viewport" content="width=device-width"><style>button,input{display:block;margin:12px;font-size:20px}</style>
      <p>Visible words</p><span hidden>HIDDEN SENTINEL</span>
      <a href="${external}">Outside</a><a href="/redirect">Redirect</a>
      <button onclick="location='/pay'">Purchase</button>
      <button ontouchstart="fetch('/touch')">Touch me</button>
      <form action="/form" method="post"><label>Email<input name="email" type="email"></label><label>City<input name="city"></label><button>Save</button></form>
      <iframe src="${external}"></iframe><script>window.open('${external}')</script>`);
  });
  await listen(app, '127.0.0.1');
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-integration-'));
  const session = new BrowserSession({url: `http://127.0.0.1:${app.address().port}`, person: {id: 'tester-1', device: 'phone'},
    output, browser_sandbox: process.env.UXLAB_TEST_NO_SANDBOX !== '1', timeout: 45, steps: 40, safety: {hosts: ['127.0.0.1'], allow_risky: false}});
  const number = label => session.controls.findIndex(c => c.info.label === label) + 1;
  try {
    await session.start();
    const background = await session.page.evaluate(async () => {
      let refused = false;
      try { await navigator.serviceWorker.register('/worker.js'); } catch { refused = true; }
      const stream = new EventSource('/events');
      stream.close();
      return {refused, workers: (await navigator.serviceWorker.getRegistrations()).length, state: stream.readyState};
    });
    assert.deepEqual(background, {refused:true, workers:0, state:2});
    const seen = await session.call('look', {});
    assert.match(seen[0].text, /Visible words/);
    assert(!seen[0].text.includes('HIDDEN SENTINEL'));
    await assert.rejects(session.call('click', {control: number('Outside'), expected: 'outside'}), /Unsafe/);
    await assert.rejects(session.call('click', {control: number('Purchase'), expected: 'purchase'}), /Risky/);
    await session.call('click', {control: number('Touch me'), expected: 'touch event'});
    assert.equal(touched, true);
    await session.call('type', {control: number('Email'), value: 'email', expected: 'fake email'});
    await session.call('type', {control: number('City'), value: 'city', expected: 'fake city'});
    await session.call('click', {control: number('Save'), expected: 'safe form saved'});
    assert.equal(submitted, 'email=tester%40example.com&city=Example+City');
    await session.call('restart', {expected: 'start over'});
    await session.call('click', {control: number('Redirect'), expected: 'redirect checked'}).catch(() => {});
    assert.equal(escaped, 0);
    assert.equal(paid, 0);
  } finally {
    await session.close();
    await close(app); await close(outside);
    fs.rmSync(output, {recursive: true, force: true});
  }
});

const crypto = require('crypto');
function authenticatedSpec(output, origin, script) {
  const filename = path.join(output, 'trusted-login.cjs');
  // Helper is outside the artifact directory in real runs; tests remove it before scanning.
  fs.writeFileSync(filename, script);
  return {url: origin, person: {id: 'tester-1', device: 'desktop', auth: 'shop', start_path: '/account/details'},
    authentication: {shop: {script: filename, sha256: crypto.createHash('sha256').update(script).digest('hex'),
      requests: [{method: 'POST', url: origin + '/session'}]}},
    output, browser_sandbox: process.env.UXLAB_TEST_NO_SANDBOX !== '1', timeout: 40, steps: 20,
    safety: {hosts: ['127.0.0.1'], allow_risky: false, read_only: true}};
}
const loginScript = (password = 'fixture-only-password', extra = '') => `module.exports = async ({page, origin}) => {
  console.log('fixture-only-password');
  await page.goto(origin + '/login');
  await page.type('[name=email]', 'fixture@example.com');
  await page.type('[name=password]', '${password}');
  ${extra}
  await Promise.all([page.waitForNavigation(), page.click('button')]);
  return (await page.evaluate(() => document.body.innerText)).includes('Account details');
};`;

test('authenticated shop starts after login, restarts at start_path and records no login secrets', {timeout: 60000}, async () => {
  const {server} = require('./fixtures/uxlab-shop/server');
  await listen(server, '127.0.0.1');
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-auth-'));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const spec = authenticatedSpec(output, origin, loginScript());
  const session = new BrowserSession(spec);
  try {
    await assert.rejects(session.call('look', {}), /not ready/);
    await assert.rejects(session.shot(), /not ready/);
    await session.start();
    assert.equal(session.shots, 0);
    assert(!fs.readdirSync(output).some(f => f.endsWith('.png')));
    fs.unlinkSync(spec.authentication.shop.script);
    assert.equal(session.page.url(), origin + '/account/details');
    const seen = await session.call('look', {});
    assert.match(seen[0].text, /Authenticated sample shop/);
    assert.equal(session.result.trace.filter(t => t.kind === 'look').length, 1);
    // Login POST is denied after bootstrap, even with fake/empty data.
    const status = await session.page.evaluate(async origin => {
      try { await fetch(origin + '/session', {method: 'POST'}); return 'allowed'; } catch {return 'denied';}
    }, origin);
    assert.equal(status, 'denied');
    await session.call('restart', {expected: 'authenticated starting page'});
    assert.equal(session.page.url(), origin + '/account/details');
    await session.call('finish', {success: true, worked: 'Authenticated page', actual: 'Goal complete'});
    for (const filename of fs.readdirSync(output)) {
      const bytes = fs.readFileSync(path.join(output, filename));
      for (const secret of ['fixture@example.com', 'fixture-only-password', 'fixture-only-cookie', '/login', '[name=password]'])
        assert(!bytes.includes(Buffer.from(secret)), `${filename} leaked ${secret}`);
    }
  } finally {await session.close(); await close(server); fs.rmSync(output, {recursive: true, force: true});}
});

test('failed login and denied bootstrap destinations fail closed without screenshots', {timeout: 120000}, async () => {
  const {server} = require('./fixtures/uxlab-shop/server');
  await listen(server, '127.0.0.1');
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    for (const [label, script] of [
      ['bad password', loginScript('wrong-fixture-password')],
      ['wrong endpoint', loginScript('fixture-only-password', `await page.evaluate(() => fetch('/wrong-session', {method:'POST', body:'secret=fixture-only-password'}).then(() => {throw Error('unexpected network success')}, () => {}));`)],
      ['offhost', loginScript('fixture-only-password', `await page.evaluate(() => fetch('http://127.0.0.2:58000/session', {method:'POST', body:'secret=fixture-only-password'}).then(() => {throw Error('unexpected network success')}, () => {}));`)],
      ['throw', `module.exports = async () => { throw Error('fixture-only-password'); };`],
    ]) {
      const output = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-auth-fail-'));
      const spec = authenticatedSpec(output, origin, script);
      const session = new BrowserSession(spec);
      try {
        if (['wrong endpoint', 'offhost'].includes(label)) {
          // Unrelated denied app traffic must not turn a verified login into
          // a failure. The request itself remains blocked.
          await session.start();
          assert.equal(session.authReady, true);
          continue;
        }
        await assert.rejects(session.start(), /^Error: Authentication bootstrap failed$/, label);
        assert.equal(session.browser, null);
        assert.equal(session.result.status, 'failed');
        await assert.rejects(session.call('look', {}), /not ready/);
        fs.unlinkSync(spec.authentication.shop.script);
        assert(!fs.readdirSync(output).some(f => f.endsWith('.png')));
        assert(!fs.readFileSync(path.join(output, 'result.json'), 'utf8').includes('fixture-only-password'));
      } finally {await session.close(); fs.rmSync(output, {recursive: true, force: true});}
    }
  } finally {await close(server);}
});

test('authenticated MCP socket is available only after bootstrap and uses the same browser', {timeout: 60000}, async () => {
  const {spawn} = require('child_process');
  const {server} = require('./fixtures/uxlab-shop/server');
  await listen(server, '127.0.0.1');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-socket-'));
  const output = path.join(root, 'artifacts'); fs.mkdirSync(output);
  const origin = `http://127.0.0.1:${server.address().port}`;
  const spec = authenticatedSpec(output, origin, loginScript());
  const specFile = path.join(root, 'spec.json'), socketFile = path.join(root, 'browser.sock');
  fs.writeFileSync(specFile, JSON.stringify(spec));
  const child = spawn(process.execPath, [path.resolve(__dirname, '../uxlab/browser.js'), specFile, '--serve', socketFile], {stdio: ['ignore', 'pipe', 'pipe']});
  let stdout = '', stderr = '', socket;
  child.stdout.on('data', b => stdout += b); child.stderr.on('data', b => stderr += b);
  const exited = new Promise(resolve => child.once('exit', resolve));
  try {
    const until = Date.now() + 40000;
    while (!fs.existsSync(path.join(root, 'browser.ready'))) {
      assert.equal(child.exitCode, null, stderr);
      assert(Date.now() < until, 'bootstrap timed out');
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    assert.equal(fs.statSync(socketFile).mode & 0o777, 0o600);
    socket = require('net').createConnection(socketFile);
    const lines = require('readline').createInterface({input: socket})[Symbol.asyncIterator]();
    socket.write(JSON.stringify({jsonrpc:'2.0', id:0, method:'initialize', params:{}})+'\n');
    const initialized=JSON.parse((await lines.next()).value);
    assert.equal(initialized.jsonrpc,'2.0');
    assert.equal(initialized.id,0);
    socket.write(JSON.stringify({jsonrpc: '2.0', id: 1, method: 'tools/call', params: {name: 'look', arguments: {}}}) + '\n');
    const response = JSON.parse((await lines.next()).value);
    assert.equal(response.result.isError, undefined);
    assert.match(response.result.content[0].text, /Authenticated sample shop/);
    assert.match(response.result.content[0].text, /account\/details/);
    socket.end(); await exited;
    assert.equal(stdout, ''); assert.equal(stderr, '');
  } finally {
    if (socket) socket.destroy();
    if (child.exitCode === null) {child.kill('SIGTERM'); await exited;}
    await close(server); fs.rmSync(root, {recursive: true, force: true});
  }
});

test('phone scroll targets the visible modal with a touch gesture', {timeout:60000}, async()=>{
 const app=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<meta name="viewport" content="width=device-width"><style>body{overflow:hidden;margin:0}.sheet{position:fixed;top:80px;bottom:20px;left:10px;right:10px;overflow:auto;background:white}.content{height:2400px}</style><div class="sheet"><div class="content">Scrollable modal</div></div>');});
 await listen(app,'127.0.0.1');
 const output=fs.mkdtempSync(path.join(os.tmpdir(),'uxlab-scroll-'));
 const session=new BrowserSession({url:`http://127.0.0.1:${app.address().port}`,person:{id:'tester-1',device:'phone'},output,browser_sandbox:process.env.UXLAB_TEST_NO_SANDBOX!=='1',timeout:45,steps:10,safety:{hosts:['127.0.0.1']}});
 try{
  await session.start();await session.call('look',{});
  await session.call('scroll',{direction:'down',expected:'read lower content'});
  await session.page.waitForFunction(()=>document.querySelector('.sheet').scrollTop>50);
  assert.equal(await session.page.evaluate(()=>devicePixelRatio),3);
  assert.match(await session.page.evaluate(()=>navigator.userAgent),/Android/);
 }finally{await session.close();await close(app);fs.rmSync(output,{recursive:true,force:true});}
});
