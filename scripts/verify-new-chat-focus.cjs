// Chromium regression checks with held launch responses and synthetic chats.
// All APIs are intercepted; the local server only serves static assets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const root = path.join(__dirname, '..');

(async () => {
  const temporaryDirectory = process.env.CCC_VERIFY_OUT ? null
    : fs.mkdtempSync(path.join(os.tmpdir(), 'ccc-new-chat-focus-'));
  const output = process.env.CCC_VERIFY_OUT || path.join(temporaryDirectory, 'check');
  let browser, page;
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const relative = pathname === '/' ? 'static/index.html' : pathname.slice(1);
    const file = pathname === '/' && process.env.CCC_INDEX_SOURCE ? process.env.CCC_INDEX_SOURCE
      : pathname === '/static/app.js' && process.env.CCC_APP_SOURCE ? process.env.CCC_APP_SOURCE
      : path.resolve(root, relative);
    if (!file.startsWith(root + path.sep) && ![process.env.CCC_INDEX_SOURCE, process.env.CCC_APP_SOURCE].includes(file)) {
      response.writeHead(404); response.end(); return;
    }
    try {
      const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
        '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(file)] || 'application/octet-stream';
      const bytes = fs.readFileSync(file);
      response.writeHead(200, { 'Content-Type': type }); response.end(bytes);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  const errors = [], writes = [], held = [], checks = [], logs = [];
  const rows = [];
  const row = (id, text) => ({ id, session_id: id, display_name: text, first_message: text,
    source: 'codex', engine: 'codex', repo_path: '/projects/example', folder_path: '/projects/example',
    session_cwd: '/projects/example', session_cwd_exists: true, modified: Date.now() / 1000,
    state: 'idle', is_live: false, archived: false, spawned_via: 'ui', spawned_lane: 'coding' });
  rows.push(row('existing-chat', 'Existing chat'));
  let phase = 'browser setup';
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const base = 'http://127.0.0.1:' + server.address().port;
    browser = await puppeteer.launch({ executablePath: findChromePath(),
      args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    page = await browser.newPage();
    page.setDefaultTimeout(30000);
    await page.setViewport({ width: 1440, height: 1000 });
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      localStorage.setItem('ccc-sidebar-tab', 'coding');
      localStorage.setItem('ccc-archive-window', '7d');
      localStorage.setItem('ccc-separate-tabs', 'off');
      localStorage.setItem('ccc-spawn-cwd', '/projects/example');
      localStorage.setItem('ccc-session-view', 'list');
    });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') logs.push(message.text()); });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      const json = data => request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
      if (url.origin !== base) return request.abort();
      if (!url.pathname.startsWith('/api/')) return request.continue();
      if (!['GET', 'HEAD'].includes(request.method())) {
        writes.push(request.method() + ' ' + url.pathname);
        if (/^\/api\/sessions\/spawn(?:-[^/]+)?$/.test(url.pathname)) {
          const body = JSON.parse(request.postData() || '{}');
          const id = 'launched-chat-' + held.length;
          held.push({ body, finish: async (ok = true) => {
            if (ok) rows.unshift(row(id, body.prompt));
            return json(ok ? { ok: true, session_id: id, spawn_id: 'pid-' + id }
              : { ok: false, error: 'Temporarily unavailable' });
          } });
          return;
        }
        return json({ ok: true });
      }
      if (['/api/conversations/list', '/api/conversations/all'].includes(url.pathname)) {
        return json({ ok: true, conversations: rows, count: rows.length, window: '7d' });
      }
      if (['/api/sessions', '/api/conversations', '/api/sessions/spawned'].includes(url.pathname)) return json([]);
      if (url.pathname === '/api/session/landed') return json({ landed: rows.some(row => row.id === url.searchParams.get('session_id')) });
      if (url.pathname === '/api/sessions/spawn/receipt') return json({ ok: true, found: false });
      if (url.pathname === '/api/objects') return json({ ok: true, objects: [] });
      if (url.pathname === '/api/repo/list') return json({ repos: [{ path: '/projects/example', label: 'Example' }], recent: [], rankings: [], by_kind: {} });
      if (url.pathname === '/api/spawn-defaults') return json({ ok: true, engine: 'codex' });
      if (url.pathname === '/api/project-memory') return json({ ok: true, projects: [] });
      if (url.pathname === '/api/handoff-sessions') return json({ ok: true, sessions: [] });
      if (url.pathname.startsWith('/api/group-chats/')) return json({ chats: [] });
      if (url.pathname === '/api/ux-fixes/health') return json({ queues: [], worker_session_ids: [] });
      if (url.pathname === '/api/wt/workers') return json({ workers: [] });
      if (url.pathname === '/api/composer/commands') return json({ ok: true, commands: [] });
      if (url.pathname === '/api/spawn-subagents') return json({ ok: true, subagents: [], modes: [] });
      if (url.pathname === '/api/spawn-mcps') return json({ ok: true, mcps: [] });
      if (url.pathname === '/api/modes' || url.pathname === '/api/spawn-modes') return json({ ok: true, modes: [] });
      if (url.pathname === '/api/queue/status') return json({ ok: true, queues: [] });
      if ((request.headers().accept || '').includes('text/event-stream')) {
        return request.respond({ status: 200, contentType: 'text/event-stream', body: ': fixture\n\n' });
      }
      if (/^\/api\/conversations\/[^/]+$/.test(url.pathname)) return json({ session_id: url.pathname.split('/').pop(),
        events: [{ type: 'user_text', line: 1, text: 'Existing conversation' }], last_line: 1 });
      if (url.pathname.endsWith('/stream')) return request.respond({ status: 200, contentType: 'text/event-stream', body: ': fixture\n\n' });
      return json({ ok: true });
    });
    const newChat = async () => {
      await page.$eval('#sidebarNewBtn', button => button.click());
      await page.waitForFunction(() => window.currentConversation === '__new__');
      await page.select('#convInputEngineSelect', 'codex');
    };
    const draft = text => page.$eval('#convInput', (input, text) => {
      input.value = text; input.dispatchEvent(new Event('input', { bubbles: true }));
    }, text);
    const send = async (text, count) => {
      await draft(text);
      await page.$eval('#convSendBtn', button => { if (button.disabled) throw new Error('Send disabled'); button.click(); });
      const deadline = Date.now() + 15000;
      while (held.length < count && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
      assert.equal(held.length, count);
    };
    const state = () => page.evaluate(() => ({ selected: window.currentConversation,
      draft: document.getElementById('convInput').value, disabled: document.getElementById('convSendBtn').disabled }));
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60000 });

    phase = 'overlapping launches';
    await newChat(); await send('First task', 1);
    await page.waitForFunction(() => String(window.currentConversation).startsWith('spawning-'));
    await newChat();
    assert.equal((await state()).disabled, false);
    await send('Second task', 2);
    await page.waitForFunction(() => String(window.currentConversation).startsWith('spawning-'));
    await newChat();
    await draft('Third draft stays here');
    await held[1].finish(); await held[0].finish();
    await page.waitForFunction(() => document.querySelector('.conv-item[data-session-id="launched-chat-0"]'));
    assert.equal((await state()).selected, '__new__');
    assert.equal((await state()).draft, 'Third draft stays here');
    checks.push('new chats open immediately; reverse replies preserve a third new-chat draft');
    await page.setViewport({ width: 393, height: 851 });
    await page.screenshot({ path: output + '-phone.png' });
    await page.setViewport({ width: 1440, height: 1000 });

    phase = 'late rejection';
    await send('Task that fails later', 3);
    await newChat(); await draft('Newer draft survives rejection');
    await held[2].finish(false);
    await page.waitForFunction(() => document.body.innerText.includes('Spawn failed'));
    assert.equal((await state()).selected, '__new__');
    assert.equal((await state()).draft, 'Newer draft survives rejection');
    checks.push('late rejection keeps the failed launch and preserves the newer draft');

    phase = 'launch then navigate to another chat';
    await newChat(); await send('Foreground task starts slowly', 4);
    await page.waitForFunction(() => String(window.currentConversation).startsWith('spawning-'));
    await page.evaluate(() => window.cccOpenSession('existing-chat'));
    await page.waitForFunction(() => window.currentConversation === 'existing-chat');
    await draft('Reply in the existing chat');
    await held[3].finish();
    await page.waitForFunction(() => document.querySelector('.conv-item[data-session-id="launched-chat-3"]'));
    assert.equal((await state()).selected, 'existing-chat');
    assert.equal((await state()).draft, 'Reply in the existing chat');
    checks.push('a late reply cannot replace another open chat or its draft');
    await page.screenshot({ path: output + '-desktop.png' });
    assert.deepEqual(errors, []);
    checks.push('no browser errors and all launch requests intercepted');
    fs.writeFileSync(output + '.json', JSON.stringify({ checks, errors, writes,
      launches: held.map(item => ({ prompt: item.body.prompt, engine: item.body.engine })) }, null, 2) + '\n');
    console.log(JSON.stringify({ checks, errors, output: temporaryDirectory ? null : output }));
  } catch (error) {
    if (page) await page.screenshot({ path: output + '-failed.png' }).catch(() => {});
    const state = page ? await page.evaluate(() => ({ selected: window.currentConversation,
      draft: document.getElementById('convInput')?.value, body: document.body.innerText.slice(-5000) })).catch(() => null) : null;
    fs.writeFileSync(output + '.json', JSON.stringify({ phase, error: error.message, errors, writes, logs, state }, null, 2) + '\n');
    throw error;
  } finally {
    try {
      if (browser) await browser.close();
    } finally {
      server.closeAllConnections();
      try {
        if (server.listening) await new Promise(resolve => server.close(resolve));
      } finally {
        if (temporaryDirectory) fs.rmSync(temporaryDirectory, { recursive: true, force: true });
      }
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
